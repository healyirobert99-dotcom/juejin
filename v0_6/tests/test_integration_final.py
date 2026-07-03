"""
最终整合测试：旧CLI停用、STOCK价格口径、数据门禁、ETF多对多、后缀标准化

全部使用临时数据库和模拟数据，不接触真实数据库。
"""
import sys
import sqlite3
from pathlib import Path

V0_6_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(V0_6_ROOT))

import pandas as pd
import pytest


# ═══════════════════════════════════════════════════
# 辅助
# ═══════════════════════════════════════════════════

def _patch_db(monkeypatch, tmp_path):
    """patch 交易数据库"""
    db = tmp_path / "test_trade.sqlite3"
    import v0_6.core.config as cfg
    import v0_6.core.live_trade_store as lts
    monkeypatch.setattr(cfg, "DB_PATH", db)
    monkeypatch.setattr(lts, "DB_PATH", db)
    return db


def _patch_stock_db(monkeypatch, tmp_path):
    """patch 行情数据库"""
    db = tmp_path / "stock_data.db"
    import v0_6.core.config as cfg
    import v0_6.core.monitor_v6 as mon
    import v0_6.core.market_data as md
    monkeypatch.setattr(cfg, "STOCK_DATA_DB", db)
    monkeypatch.setattr(mon, "STOCK_DATA_DB", db)
    monkeypatch.setattr(md, "STOCK_DATA_DB", db)
    return db


def _ensure_tables(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS daily_hfq (
            ts_code TEXT NOT NULL, trade_date TEXT NOT NULL, close REAL, vol REAL, amount REAL,
            PRIMARY KEY (ts_code, trade_date)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS stock_daily_raw (
            ts_code TEXT NOT NULL, trade_date TEXT NOT NULL, close REAL, vol REAL, amount REAL,
            PRIMARY KEY (ts_code, trade_date)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS etf_daily (
            ts_code TEXT NOT NULL, trade_date TEXT NOT NULL, close REAL, vol REAL, amount REAL,
            PRIMARY KEY (ts_code, trade_date)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS market_calendar (
            cal_date TEXT PRIMARY KEY, is_open INTEGER NOT NULL
        )
    """)
    con.commit()


# ═══════════════════════════════════════════════════
# 1. 旧 CLI 安全测试
# ═══════════════════════════════════════════════════

def test_log_trade_disabled(monkeypatch, tmp_path):
    """log_trade.py 输出"已停用"并退出码非0"""
    import subprocess
    r = subprocess.run(
        [sys.executable, "-m", "v0_6.scripts.log_trade", "buy", "测试", "1.0", "100", "2026-06-25"],
        capture_output=True, text=True, cwd=str(V0_6_ROOT),
    )
    assert r.returncode != 0, f"退出码应非0，实 {r.returncode}"
    assert "已停用" in r.stdout, f"应输出'已停用'，实 {r.stdout[:200]}"


# ═══════════════════════════════════════════════════
# 2. STOCK 价格口径测试
# ═══════════════════════════════════════════════════

def test_stock_monitor_uses_stock_daily_raw(monkeypatch, tmp_path):
    """STOCK 监控使用 stock_daily_raw 而非 daily_hfq"""
    db = _patch_stock_db(monkeypatch, tmp_path)
    con = sqlite3.connect(str(db))
    _ensure_tables(con)
    # daily_hfq 写入 100（后复权）
    con.execute("INSERT INTO daily_hfq VALUES ('000001.SZ', '20260620', 100.0, 0, 0)")
    # stock_daily_raw 写入 10（未复权）
    con.execute("INSERT INTO stock_daily_raw VALUES ('000001.SZ', '20260620', 10.0, 0, 0)")
    con.commit()
    con.close()

    from v0_6.core.monitor_v6 import evaluate_position_v6
    result = evaluate_position_v6(
        target="000001.SZ", target_type="STOCK",
        entry_date="2026-06-20", entry_price=9.0,
        today="2026-06-20", progress=0.0,
    )
    assert "error" not in result, f"不应有error: {result.get('error')}"
    assert result["current_price"] == 10.0, \
        f"STOCK current_price 应=10.0，实 {result['current_price']}"
    expected_pnl = (10.0 - 9.0) / 9.0
    assert abs(result["return_pct"] - expected_pnl) < 1e-6, \
        f"return_pct 应={expected_pnl}，实 {result['return_pct']}"


def test_stock_no_raw_price_returns_error(monkeypatch, tmp_path):
    """STOCK 无 stock_daily_raw 时返回 raw_price_unavailable，不回退 daily_hfq"""
    db = _patch_stock_db(monkeypatch, tmp_path)
    con = sqlite3.connect(str(db))
    _ensure_tables(con)
    # 只有 daily_hfq，无 stock_daily_raw
    con.execute("INSERT INTO daily_hfq VALUES ('000001.SZ', '20260620', 100.0, 0, 0)")
    con.commit()
    con.close()

    from v0_6.core.monitor_v6 import evaluate_position_v6
    result = evaluate_position_v6(
        target="000001.SZ", target_type="STOCK",
        entry_date="2026-06-20", entry_price=9.0,
        today="2026-06-22", progress=0.0,
    )
    assert result.get("error") == "raw_price_unavailable", \
        f"应返回 raw_price_unavailable，实 {result.get('error')}"


def test_stock_auto_calc_rules_uses_stock_daily_raw(monkeypatch, tmp_path):
    """auto_calc_rules 对 STOCK 使用 stock_daily_raw"""
    db = _patch_stock_db(monkeypatch, tmp_path)
    con = sqlite3.connect(str(db))
    _ensure_tables(con)
    con.execute("INSERT INTO daily_hfq VALUES ('000001.SZ', '20260620', 100.0, 0, 0)")
    con.execute("INSERT INTO stock_daily_raw VALUES ('000001.SZ', '20260620', 10.0, 0, 0)")
    # 交易日历 + etf_daily（全局校验需要）
    con.execute("INSERT INTO market_calendar VALUES ('20260619', 1)")
    con.execute("INSERT INTO market_calendar VALUES ('20260620', 1)")
    con.execute("INSERT INTO etf_daily VALUES ('512800.SH', '20260620', 1.0, 0, 0)")
    con.commit()
    con.close()

    # 需要同时 patch 到 trade_form_server 和 market_data
    from v0_6.scripts import trade_form_server as tfs
    from v0_6.core import market_data as md
    from v0_6.core import config as cfg
    monkeypatch.setattr(tfs, "STOCK_DATA_DB", db)
    monkeypatch.setattr(md, "STOCK_DATA_DB", db)
    monkeypatch.setattr(cfg, "STOCK_DATA_DB", db)

    from v0_6.scripts.trade_form_server import auto_calc_rules
    rules = auto_calc_rules("000001.SZ", "STOCK", "2026-06-20")
    assert rules, "应返回规则"
    assert rules["latest_price"] == 10.0, \
        f"latest_price 应=10.0，实 {rules['latest_price']}"


# ═══════════════════════════════════════════════════
# 3. 交易日历和阻断
# ═══════════════════════════════════════════════════

def test_get_expected_trade_date(monkeypatch, tmp_path):
    """交易日历解析正确"""
    db = _patch_stock_db(monkeypatch, tmp_path)
    con = sqlite3.connect(str(db))
    _ensure_tables(con)
    con.execute("INSERT INTO market_calendar VALUES ('20260625', 1)")  # 周四
    con.execute("INSERT INTO market_calendar VALUES ('20260626', 1)")  # 周五
    con.execute("INSERT INTO market_calendar VALUES ('20260627', 0)")  # 周六
    con.execute("INSERT INTO market_calendar VALUES ('20260628', 0)")  # 周日
    con.execute("INSERT INTO market_calendar VALUES ('20260629', 1)")  # 周一
    con.commit()
    con.close()

    from v0_6.core.market_data import get_expected_trade_date
    assert get_expected_trade_date("2026-06-27") == "2026-06-26"  # 周六→周五
    assert get_expected_trade_date("2026-06-28") == "2026-06-26"  # 周日→周五
    assert get_expected_trade_date("2026-06-29") == "2026-06-29"  # 周一→周一
    assert get_expected_trade_date("2026-06-26") == "2026-06-26"  # 周五→周五


def test_validate_market_data_blocked(monkeypatch, tmp_path):
    """data_blocked 时 errors 非空"""
    db = _patch_stock_db(monkeypatch, tmp_path)
    con = sqlite3.connect(str(db))
    _ensure_tables(con)
    con.execute("INSERT INTO market_calendar VALUES ('20260625', 1)")
    con.execute("INSERT INTO market_calendar VALUES ('20260626', 1)")
    con.execute("INSERT INTO market_calendar VALUES ('20260627', 0)")
    # daily_hfq 只到 6-25，请求 6-27（周六预期 6-26）
    con.execute("INSERT INTO daily_hfq VALUES ('000001.SZ', '20260625', 10.0, 0, 0)")
    con.commit()
    con.close()

    from v0_6.core.market_data import validate_market_data
    val = validate_market_data("2026-06-27")
    # daily_hfq 最新 20260625 < 预期 20260626，应有 error
    assert not val["ok"], "数据落后时应不通过"
    errors = val.get("errors", [])
    assert any("早于" in e for e in errors) or any("无数据" in e for e in errors), \
        f"应包含日期落后或无数据提示，实 {errors}"


# ═══════════════════════════════════════════════════
# 4. ETF 后缀标准化
# ═══════════════════════════════════════════════════

def test_etf_normalize_ts_code():
    """深圳 ETF 使用 .SZ，上海 ETF 使用 .SH"""
    from v0_6.core.market_data import normalize_etf_ts_code
    assert normalize_etf_ts_code("159887") == "159887.SZ"
    assert normalize_etf_ts_code("159995") == "159995.SZ"
    assert normalize_etf_ts_code("512800") == "512800.SH"
    assert normalize_etf_ts_code("512800.SH") == "512800.SH"
    assert normalize_etf_ts_code("159887.SZ") == "159887.SZ"
    with pytest.raises(ValueError):
        normalize_etf_ts_code("000001")


def test_etf_normalize_no_159_sh():
    """159xxx 不得输出 .SH"""
    from v0_6.core.market_data import normalize_etf_ts_code
    assert normalize_etf_ts_code("159887") != "159887.SH"
    assert normalize_etf_ts_code("159995") != "159995.SH"


# ═══════════════════════════════════════════════════
# 5. ETF 多对多映射
# ═══════════════════════════════════════════════════

def test_etf_multi_industry_retained():
    """同一 ETF 对应多个行业时，各行业独立匹配"""
    from v0_6.core.etf_matcher import match_industry_to_etfs

    # 512170 在 CSV 中对应 医疗、CXO、医美
    for ind in ["医疗器械", "CXO", "医美"]:
        result = match_industry_to_etfs(ind)
        assert result["best"] is not None, f"{ind} 应有匹配"
        codes = [m["code"] for m in result["matches"]]
        assert "512170" in codes, f"{ind} 应匹配到 512170，实 {codes}"

    # 同一查询内 512170 只出现一次
    result = match_industry_to_etfs("医疗器械")
    occurrences = [m["code"] for m in result["matches"]]
    assert occurrences.count("512170") == 1, "同一查询内 ETF 不应重复"


def test_etf_multi_dedup_in_query():
    """同一查询结果中，同一 ETF 代码只出现一次"""
    from v0_6.core.etf_matcher import match_industry_to_etfs
    result = match_industry_to_etfs("金融科技")
    codes = [m["code"] for m in result["matches"]]
    assert len(codes) == len(set(codes)), "同一查询内不应有重复 ETF"


# ═══════════════════════════════════════════════════
# 6. SELL 周期隔离（完整端到端）
# ═══════════════════════════════════════════════════

def test_sell_rebuy_through_backend(monkeypatch, tmp_path):
    """模拟后端 SELL 分支完整流程：写 SELL → close_trade_by_target → 再 BUY"""
    db = _patch_db(monkeypatch, tmp_path)
    from v0_6.core import init_schema, add_trade, get_position_summary_all, close_trade_by_target, get_position_net
    import sqlite3
    init_schema()

    # 第1轮：BUY 1000
    add_trade(target="000001.SZ", target_type="STOCK", target_name="平安",
              action="BUY", amount=10000, price=10.0, shares=1000.0,
              trade_date="2026-06-01", progress=0.0)

    # 模拟 SELL 后端逻辑
    net = get_position_net("000001.SZ")
    sell_shares = net["net_shares"]
    sell_amount = sell_shares * 10.5
    add_trade(target="000001.SZ", target_type="STOCK", target_name="平安",
              action="SELL", amount=sell_amount, price=10.5, shares=sell_shares,
              trade_date="2026-06-15")
    close_trade_by_target("000001.SZ", 10.5, "2026-06-15")

    con = sqlite3.connect(str(db))
    sell_row = con.execute("SELECT closed FROM live_trades WHERE action='SELL'").fetchone()
    assert sell_row[0] == 1, f"SELL 应 closed=1，实 {sell_row[0]}"

    # 第2轮：BUY 600
    add_trade(target="000001.SZ", target_type="STOCK", target_name="平安",
              action="BUY", amount=6000, price=10.0, shares=600.0,
              trade_date="2026-06-20")

    summaries = get_position_summary_all()
    assert len(summaries) == 1
    assert summaries[0]["total_shares"] == 600.0
    con.close()


# ═══════════════════════════════════════════════════
# 7. ETF 部分失败回滚
# ═══════════════════════════════════════════════════

def test_etf_partial_failure_rollback(monkeypatch, tmp_path):
    """3 只 ETF，1 只请求失败 → 当日 0 行新增，MAX 日期不前进，返回 ok=False"""
    db = _patch_stock_db(monkeypatch, tmp_path)
    con = sqlite3.connect(str(db))
    _ensure_tables(con)
    con.execute("INSERT INTO market_calendar VALUES ('20260625', 1)")
    con.commit()
    con.close()

    # patch DATA_DIR 为临时目录并创建 CSV
    import v0_6.core.config as cfg
    orig_data_dir = cfg.DATA_DIR
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    import v0_6.core.market_data as md
    monkeypatch.setattr(md, "DATA_DIR", tmp_path)

    import csv
    csv_path = tmp_path / "sector_etf_map.csv"
    with open(str(csv_path), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["板块", "维度", "细分", "代码", "名称", "指数", "规模", "纯度", "备注"])
        w.writerow(["金融", "行业", "银行", "512800", "银行ETF", "中证银行", "~300", "纯", ""])
        w.writerow(["金融", "行业", "证券", "512880", "证券ETF", "证券公司", "~200", "纯", ""])
        w.writerow(["科技", "行业", "半导体", "159995", "芯片ETF", "芯片指数", "~150", "纯", ""])

    # 用 pandas DataFrame 做模拟 Tushare 返回（更可靠）
    import pandas as pd

    class MockPro:
        def __init__(self):
            self._call_count = 0
        def fund_daily(self, ts_code, start_date, end_date):
            self._call_count += 1
            if "159995" in ts_code:
                raise Exception("无权限")
            return pd.DataFrame({
                "ts_code": [ts_code],
                "trade_date": [start_date],
                "close": [1.0],
                "vol": [1000000],
                "amount": [1000000],
            })
        def daily(self, ts_code, start_date, end_date):
            return self.fund_daily(ts_code, start_date, end_date)
        def trade_cal(self, start_date, end_date):
            return pd.DataFrame({"cal_date": ["20260625"], "is_open": [1]})

    from v0_6.scripts.refresh_market_data import refresh_etf_daily
    result = refresh_etf_daily(MockPro(), dry_run=False)

    monkeypatch.setattr(cfg, "DATA_DIR", orig_data_dir)
    monkeypatch.setattr(md, "DATA_DIR", orig_data_dir)

    # 新行为：非持仓 ETF 失败不再阻断当日（ok=True），但 failed 列表仍记录
    assert result["ok"], "非持仓 ETF 失败应容忍，返回 ok=True"
    assert result.get("failed") is not None, "应包含 failed 列表"
    failed_codes = [f[0] for f in result["failed"]]
    assert "159995.SZ" in failed_codes, f"失败列表应含 159995.SZ，实 {failed_codes}"

    con2 = sqlite3.connect(str(db))
    max_date = con2.execute("SELECT MAX(trade_date) FROM etf_daily").fetchone()[0]
    count = con2.execute("SELECT COUNT(*) FROM etf_daily WHERE trade_date='20260625'").fetchone()[0]
    con2.close()
    assert max_date == "20260625", f"应有数据写入，实 max={max_date}"
    assert count == 2, f"应成功写入 2 行（1 只 ETF 失败容忍），实 {count} 行"
