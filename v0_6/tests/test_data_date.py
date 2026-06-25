"""
回归测试：数据更新、交易日识别与数据时效性（第五步）

覆盖 8 个场景：
1. 周末日报回退至最近交易日
2. 数据库落后时只显示数据日期
3. 正常同日
4. 无数据时报错
5. ETF 监控返回实际价格日期
6. ETF 和个股日期不同时各自独立
7. 历史录入不读取未来价格
8. target_type 路由正确

全部使用临时 SQLite 数据库。
"""
import sys
import sqlite3
import pandas as pd
from pathlib import Path

V0_6_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(V0_6_ROOT))

import pytest


def _patch_stock_db(monkeypatch, db_path):
    """同时 patch config.STOCK_DATA_DB 和所有引用 STOCK_DATA_DB 的模块级变量"""
    from v0_6.core import config as cfg
    from v0_6.core import monitor_v6
    from v0_6.core import market_data as md
    monkeypatch.setattr(cfg, "STOCK_DATA_DB", db_path)
    monkeypatch.setattr(monitor_v6, "STOCK_DATA_DB", db_path)
    monkeypatch.setattr(md, "STOCK_DATA_DB", db_path)
    try:
        from v0_6.scripts import trade_form_server as tfs
        monkeypatch.setattr(tfs, "STOCK_DATA_DB", db_path)
    except ImportError:
        pass


# ── Fixtures ──

@pytest.fixture
def db_with_data(tmp_path):
    """创建包含模拟 daily_hfq/etf_daily/stock_daily_raw 的临时 stock_data.db"""
    db_path = tmp_path / "stock_data.db"
    con = sqlite3.connect(str(db_path))
    con.execute("CREATE TABLE daily_hfq (ts_code TEXT, trade_date TEXT, close REAL)")
    con.execute("CREATE TABLE etf_daily (ts_code TEXT, trade_date TEXT, close REAL)")
    con.execute("CREATE TABLE stock_daily_raw (ts_code TEXT, trade_date TEXT, close REAL)")
    for code in ["000001.SZ", "600000.SH"]:
        for d in [20260619, 20260620, 20260623, 20260624, 20260625, 20260626]:
            con.execute("INSERT INTO daily_hfq VALUES (?, ?, ?)",
                        (code, str(d), 10.0 + (d % 10) * 0.1))
            con.execute("INSERT INTO stock_daily_raw VALUES (?, ?, ?)",
                        (code, str(d), 10.0 + (d % 10) * 0.1))
    for code in ["512800.SH", "512480.SH"]:
        for d in [20260619, 20260620, 20260623, 20260624, 20260625, 20260626]:
            con.execute("INSERT INTO etf_daily VALUES (?, ?, ?)",
                        (code, str(d), 0.8 + (d % 10) * 0.01))
    con.commit()
    con.close()
    return db_path


@pytest.fixture
def db_etf_newer(tmp_path):
    """etf_daily 最新日期 20260626，但 daily_hfq 只到 20260625"""
    db_path = tmp_path / "stock_data.db"
    con = sqlite3.connect(str(db_path))
    con.execute("CREATE TABLE daily_hfq (ts_code TEXT, trade_date TEXT, close REAL)")
    con.execute("CREATE TABLE etf_daily (ts_code TEXT, trade_date TEXT, close REAL)")
    con.execute("CREATE TABLE stock_daily_raw (ts_code TEXT, trade_date TEXT, close REAL)")
    for code in ["000001.SZ"]:
        for d in [20260623, 20260624, 20260625]:
            con.execute("INSERT INTO daily_hfq VALUES (?, ?, ?)",
                        (code, str(d), 10.0 + (d % 10) * 0.1))
            con.execute("INSERT INTO stock_daily_raw VALUES (?, ?, ?)",
                        (code, str(d), 10.0 + (d % 10) * 0.1))
    for code in ["512800.SH"]:
        for d in [20260623, 20260624, 20260625, 20260626]:
            con.execute("INSERT INTO etf_daily VALUES (?, ?, ?)",
                        (code, str(d), 0.8 + (d % 10) * 0.01))
    con.commit()
    con.close()
    return db_path


# ── 测试 1-4：get_signal_data_date ──

def test_get_signal_data_date_weekend_fallback(monkeypatch, db_with_data):
    _patch_stock_db(monkeypatch, db_with_data)
    from v0_6.scripts.run_daily_v1_html import get_signal_data_date
    result = get_signal_data_date("2026-06-27")
    assert result == "2026-06-26", f"周末回退至 2026-06-26，实 {result}"


def test_get_signal_data_date_holiday_fallback(monkeypatch, db_with_data):
    _patch_stock_db(monkeypatch, db_with_data)
    from v0_6.scripts.run_daily_v1_html import get_signal_data_date
    result = get_signal_data_date("2026-07-01")
    assert result == "2026-06-26", f"回退至 2026-06-26，实 {result}"


def test_get_signal_data_date_same_day(monkeypatch, db_with_data):
    _patch_stock_db(monkeypatch, db_with_data)
    from v0_6.scripts.run_daily_v1_html import get_signal_data_date
    result = get_signal_data_date("2026-06-26")
    assert result == "2026-06-26"


def test_get_signal_data_date_no_data(monkeypatch, tmp_path):
    db = tmp_path / "stock_data.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE daily_hfq (ts_code TEXT, trade_date TEXT, close REAL)")
    con.close()
    _patch_stock_db(monkeypatch, db)
    from v0_6.scripts.run_daily_v1_html import get_signal_data_date
    result = get_signal_data_date("2026-06-27")
    assert result is None


# ── 测试 5：ETF 监控返回实际价格日期 ──

def test_etf_monitor_returns_price_date(monkeypatch, db_with_data):
    from v0_6.core.monitor_v6 import evaluate_position_v6
    _patch_stock_db(monkeypatch, db_with_data)
    result = evaluate_position_v6(
        target="512800", target_type="ETF",
        entry_date="2026-06-23", entry_price=0.82,
        today="2026-06-27", progress=0.0,
    )
    assert "error" not in result
    assert result.get("today") == "2026-06-26", \
        f"ETF today={result.get('today')}"
    assert result.get("requested_date") == "2026-06-27"


# ── 测试 6：ETF 和个股日期不同时各自独立 ──

def test_etf_stock_date_independent(monkeypatch, db_etf_newer):
    from v0_6.core.monitor_v6 import get_target_price
    _patch_stock_db(monkeypatch, db_etf_newer)

    etf_df = get_target_price("512800", "ETF", "2026-06-27")
    assert not etf_df.empty
    assert etf_df["trade_date"].iloc[-1] >= pd.Timestamp("2026-06-26")

    stock_df = get_target_price("000001.SZ", "STOCK", "2026-06-27")
    assert not stock_df.empty
    assert stock_df["trade_date"].iloc[-1] == pd.Timestamp("2026-06-25")

    assert etf_df["trade_date"].iloc[-1] > stock_df["trade_date"].iloc[-1]


# ── 测试 7：历史录入不读取未来价格 ──

def test_auto_calc_rules_no_future_price(monkeypatch, db_with_data):
    from v0_6.scripts.trade_form_server import auto_calc_rules
    _patch_stock_db(monkeypatch, db_with_data)

    rules = auto_calc_rules("512800", "ETF", "2026-06-20")
    assert rules, "应有规则返回"
    assert rules.get("as_of", "99999999") <= "20260620", \
        f"as_of({rules.get('as_of')}) 不应晚于录入日期"


# ── 测试 8：target_type 路由 ──

def test_target_type_routing(monkeypatch, db_with_data):
    from v0_6.core.monitor_v6 import get_target_price
    _patch_stock_db(monkeypatch, db_with_data)

    assert not get_target_price("512800", "ETF", "2026-06-27").empty
    assert not get_target_price("000001.SZ", "STOCK", "2026-06-27").empty
    assert get_target_price("测试行业", "INDUSTRY", "2026-06-27").empty
    assert get_target_price("xxx", "UNKNOWN", "2026-06-27").empty


# ── 测试 9：render_html 双日期 + 信号出现 ──

def test_render_html_weekend_shows_signal_and_data_date(monkeypatch, db_with_data):
    """周六生成日报，信号使用周五数据，HTML 包含双日期"""
    from v0_6.core import config as cfg
    from v0_6.scripts.run_daily_v1_html import render_html, get_signal_data_date, get_today_signals
    import pandas as pd
    _patch_stock_db(monkeypatch, db_with_data)

    requested_date = "2026-06-27"  # 周六
    signal_data_date = get_signal_data_date(requested_date)
    assert signal_data_date == "2026-06-26"

    # 构造信号：模拟一个 2026-06-26 的证券行业信号
    signals = pd.DataFrame([{
        "signal_date": pd.Timestamp("2026-06-26"),
        "industry": "证券",
        "priority": "FIRST",
        "breadth_at_signal": 0.45,
        "vol_ratio_at_signal": 1.2,
        "avg_chg_20d": 0.03,
    }])

    html = render_html(requested_date, signal_data_date, signals, [])
    # HTML 应包含生成日期（6-27）
    assert "2026-06-27" in html, "HTML 应含生成日期"
    # HTML 应包含信号数据日期（6-26）
    assert signal_data_date in html, f"HTML 应含信号数据日期 {signal_data_date}"
    # HTML 应含证券信号（周五的数据）
    assert "证券" in html, "HTML 应含周五的信号行业"
    # HTML 应含 data-note（日期不一致时）
    assert "数据截至" in html, "日期不一致时应显示数据截至提示"


# ── 测试 10：auto_calc_rules target_type 路由 ──

def test_auto_calc_rules_routing(monkeypatch, db_with_data):
    """auto_calc_rules 按 target_type 路由到不同表"""
    from v0_6.scripts.trade_form_server import auto_calc_rules
    _patch_stock_db(monkeypatch, db_with_data)

    # ETF -> etf_daily
    etf_rules = auto_calc_rules("512800", "ETF", "2026-06-26")
    assert etf_rules, "ETF 应返回规则"
    assert "latest_price" in etf_rules

    # STOCK -> stock_daily_raw（需要在 db_with_data 中添加 stock_daily_raw）
    _patch_stock_db(monkeypatch, db_with_data)
    con2 = sqlite3.connect(str(db_with_data))
    con2.execute("CREATE TABLE IF NOT EXISTS stock_daily_raw (ts_code TEXT, trade_date TEXT, close REAL)")
    for d in [20260619, 20260620, 20260623, 20260624, 20260625, 20260626]:
        con2.execute("INSERT INTO stock_daily_raw VALUES ('000001.SZ', ?, 10.0)", (str(d),))
    con2.commit()
    con2.close()

    stock_rules = auto_calc_rules("000001.SZ", "STOCK", "2026-06-26")
    assert stock_rules, "STOCK 应返回规则"
    assert "latest_price" in stock_rules

    # INDUSTRY -> 空
    ind_rules = auto_calc_rules("xxx", "INDUSTRY", "2026-06-26")
    assert ind_rules == {}, "INDUSTRY 应返回空"


# ── 测试 11：1-4 条历史行情时返回价格但不生成进度桶 ──

@pytest.fixture
def db_sparse(tmp_path):
    """只有 3 条 ETF 历史行情"""
    db_path = tmp_path / "stock_data.db"
    con = sqlite3.connect(str(db_path))
    con.execute("CREATE TABLE daily_hfq (ts_code TEXT, trade_date TEXT, close REAL)")
    con.execute("CREATE TABLE etf_daily (ts_code TEXT, trade_date TEXT, close REAL)")
    con.execute("CREATE TABLE stock_daily_raw (ts_code TEXT, trade_date TEXT, close REAL)")
    for code in ["512800.SH"]:
        for d in [20260620, 20260621, 20260622]:
            con.execute("INSERT INTO etf_daily VALUES (?, ?, ?)",
                        (code, str(d), 0.8))
    con.commit()
    con.close()
    return db_path


def test_auto_calc_rules_sparse_data_returns_price_no_progress(monkeypatch, db_sparse):
    """1-4 条历史行情时返回 latest_price 和 as_of，但不生成进度桶"""
    from v0_6.scripts.trade_form_server import auto_calc_rules
    _patch_stock_db(monkeypatch, db_sparse)

    rules = auto_calc_rules("512800", "ETF", "2026-06-22")
    assert rules, "应返回规则"
    assert "latest_price" in rules, "应返回最新价"
    assert "as_of" in rules, "应返回价格日期"
    assert rules["as_of"] <= "20260622", "as_of 不应晚于录入日期"
    assert "progress" not in rules, "数据不足时不应生成进度"
    assert "bucket" not in rules, "数据不足时不应生成桶"

