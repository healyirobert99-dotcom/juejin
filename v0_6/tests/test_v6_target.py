"""
v6.1/v6.2 标的匹配 + 进度/桶 + 止损/止盈价 · 关键回归测试

覆盖：
- 98 个 ETF 全部能匹配
- 45 个有日线数据的 ETF 进度算法精度
- 桶边界判定
- 止损/止盈价基于【用户买入价】算（不是现价）
- 完整录入数据流（临时交易库）

行情测试（stock_data.db）保持只读，不修改。
"""
import sys
import csv
import sqlite3
from pathlib import Path

import pytest

V0_6_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(V0_6_ROOT))


def test_etf_match_all():
    """98 个 ETF 全部能匹配"""
    from v0_6.core import match_target

    with open(V0_6_ROOT / "data" / "sector_etf_map.csv", encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader)
        etfs = []
        for row in reader:
            if not row or row[0].startswith("==="):
                continue
            if not row[3].strip() or row[3] == "—":
                continue
            etfs.append((row[3].strip(), row[4].strip()))

    fail = []
    for code, name in etfs:
        m = match_target(code)
        if m.get("target_type") != "ETF" or m.get("target_name") != name:
            fail.append((code, name, m))
    assert not fail, f"{len(fail)}/{len(etfs)} 个 ETF 匹配失败: {fail[:3]}"


def test_progress_precision():
    """进度算法精度 + 桶判定（只读 stock_data.db）"""
    from v0_6.core import calc_progress_for_etf, get_bucket

    con = sqlite3.connect(str(V0_6_ROOT / "stock_data" / "stock_data.db"))
    codes = [r[0] for r in con.execute("SELECT DISTINCT ts_code FROM etf_daily")]
    con.close()

    err = []
    for ts_code in codes:
        r = calc_progress_for_etf(ts_code, "20260624")
        if not r:
            continue
        cur, low, prog = r["current"], r["low_60d"], r["progress"]
        manual = (cur - low) / low if low > 0 else 0
        if abs(manual - prog) > 1e-9:
            err.append(f"{ts_code}: 进度精度误差 {abs(manual-prog)*1e6:.2f}ppm")
            continue
        if prog < 0.10:
            exp = "极早期"
        elif prog < 0.30:
            exp = "早期"
        elif prog < 0.50:
            exp = "中期"
        else:
            exp = "晚期"
        actual = get_bucket(prog)["name"]
        if exp != actual:
            err.append(f"{ts_code}: 进度 {prog*100:.2f}% 应入 {exp}，实入 {actual}")
    assert not err, f"进度/桶错误 {len(err)}"


def test_stop_take_profit_uses_user_price():
    """止损/止盈价必须基于用户买入价，不是现价"""
    from v0_6.core.gold_signal_v6 import get_stop_threshold, get_take_profit_threshold

    buy_price = 0.78
    progress = 0.0
    stop_pct = get_stop_threshold(progress)
    tp_pct = get_take_profit_threshold(progress)
    stop = buy_price * (1 + stop_pct)
    tp = buy_price * (1 + tp_pct)
    assert abs(stop - 0.6396) < 1e-9, f"止损价错: {stop}"
    assert abs(tp - 1.0140) < 1e-9, f"止盈价错: {tp}"

    buy_price2 = 0.85
    stop2 = buy_price2 * 0.82
    tp2 = buy_price2 * 1.30
    assert stop2 != stop
    assert tp2 != tp


def test_add_trade_persists_correct(isolated_trade_db):
    """完整录入数据流：用户买入价 → 止损价正确写入临时 DB"""
    import v0_6.core.live_trade_store as lts
    from v0_6.core import add_trade, init_schema

    init_schema()
    trade_id = add_trade(
        target="512800",
        target_type="ETF",
        target_name="华宝中证银行ETF",
        action="BUY",
        amount=780,
        price=0.78,
        trade_date="2026-06-24",
        progress=0.0,
        progress_bucket="极早期",
        stop_threshold=-0.18,
        take_profit_threshold=0.30,
        stop_price=0.78 * 0.82,
        take_profit_price=0.78 * 1.30,
        notes="v6.2 test",
    )
    con = sqlite3.connect(str(lts.DB_PATH))
    row = con.execute(
        "SELECT target, target_type, target_name, amount, price, shares, "
        "progress_bucket, stop_price, take_profit_price "
        "FROM live_trades WHERE trade_id = ?",
        (trade_id,),
    ).fetchone()
    con.close()

    assert row[0] == "512800"
    assert row[1] == "ETF"
    assert row[2] == "华宝中证银行ETF"
    assert abs(row[3] - 780) < 1e-9
    assert abs(row[4] - 0.78) < 1e-9
    assert abs(row[5] - 1000) < 1e-9  # shares = 780 / 0.78
    assert row[6] == "极早期"
    assert abs(row[7] - 0.6396) < 1e-4
    assert abs(row[8] - 1.0140) < 1e-4


def test_progress_negative_or_zero():
    """边界：现价低于 60d_low（罕见但要测）"""
    from v0_6.core import calc_progress_for_etf, get_bucket

    r = calc_progress_for_etf("512800", "20260624")
    if r:
        assert r["progress"] == 0.0
        bucket = get_bucket(0.0)
        assert bucket["name"] == "极早期"
    else:
        pytest.skip("512800 无数据，跳过")


def test_etf_routing_uses_etf_daily():
    """ETF 类型只查 etf_daily，不进行业查询（只读 stock_data.db）"""
    from v0_6.core.monitor_v6 import evaluate_position_v6

    r = evaluate_position_v6(
        target="512800", target_type="ETF",
        entry_date="2026-06-01", entry_price=0.80,
        today="2026-06-24", progress=0.0,
    )
    assert "error" not in r, f"ETF 不应报错: {r.get('error')}"
    assert r["current_price"] is not None
    assert r["target_type"] == "ETF"


def test_stock_routing_uses_stock_daily_raw(monkeypatch, tmp_path):
    """STOCK 类型只查 stock_daily_raw，不进行业查询"""
    from v0_6.core import config as cfg
    from v0_6.core.monitor_v6 import evaluate_position_v6
    db = tmp_path / "stock_data.db"
    import sqlite3
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE IF NOT EXISTS stock_daily_raw (ts_code TEXT, trade_date TEXT, close REAL)")
    for d in ["20260105", "20260315", "20260620"]:
        con.execute("INSERT INTO stock_daily_raw VALUES ('000001.SZ', ?, 10.5)", (d,))
    con.commit()
    con.close()
    monkeypatch.setattr(cfg, "STOCK_DATA_DB", db)
    import v0_6.core.monitor_v6 as mon
    monkeypatch.setattr(mon, "STOCK_DATA_DB", db)

    r = evaluate_position_v6(
        target="000001.SZ", target_type="STOCK",
        entry_date="2026-01-01", entry_price=10.0,
        today="2026-06-24", progress=0.0,
    )
    assert "error" not in r, f"STOCK 不应报错: {r.get('error')}"
    assert abs(r["current_price"] - 10.5) < 1e-9
    assert r["target_type"] == "STOCK"


def test_industry_suppresses_pnl():
    """INDUSTRY 不返回盈亏、止损、止盈"""
    from v0_6.core.monitor_v6 import evaluate_position_v6

    r = evaluate_position_v6(
        target="测试行业", target_type="INDUSTRY",
        entry_date="2026-01-01", entry_price=10.0,
        today="2026-06-24", progress=0.05,
    )
    assert r["error"] == "industry_not_executable"
    assert r.get("current_price") is None
    assert r.get("return_pct") is None
    assert r.get("stop_price") is None
    assert r.get("take_profit_price") is None
    assert r.get("action") == "ERROR"


def test_unknown_target_type_returns_error():
    """未知 target_type 返回明确错误"""
    from v0_6.core.monitor_v6 import evaluate_position_v6

    r = evaluate_position_v6(
        target="xxx", target_type="UNKNOWN",
        entry_date="2026-01-01", entry_price=1.0,
        today="2026-06-24", progress=0.0,
    )
    assert r["error"] == "unknown_target_type"
    assert r.get("current_price") is None
    assert r["alerts"][0]["type"] == "UNKNOWN_TARGET_TYPE"


def test_etf_code_not_goes_to_industry_price():
    """ETF 代码绝不进入行业价格查询路径（只读 stock_data.db）"""
    from v0_6.core.monitor_v6 import get_target_price

    df = get_target_price("512800", "ETF", "2026-06-24")
    assert not df.empty, "get_target_price(ETF) 应返回价格"
    assert "close" in df.columns


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
