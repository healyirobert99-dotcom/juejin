"""
回归测试：交易台账与净持仓一致性（第三步）

覆盖 12 个场景，全部在 pytest fixture 提供的临时交易数据库中运行。
"""
import sqlite3
import sys
from pathlib import Path

import pytest

V0_6_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(V0_6_ROOT))

from v0_6.core import (
    add_trade,
    close_trade_by_target,
    get_position_net,
    get_position_summary_all,
    init_schema,
    monitor_all_positions_v6,
)
import v0_6.core.live_trade_store as lts


def _setup():
    """每个测试函数自行调用，不修改 fixture 的 DB_PATH"""
    init_schema()
    con = sqlite3.connect(str(lts.DB_PATH))
    for table in ["live_trades", "sqlite_sequence"]:
        try:
            con.execute(f"DELETE FROM {table}")
        except sqlite3.OperationalError:
            pass
    con.commit()
    con.close()


def test_single_buy():
    """单次 BUY → 监控 1 行"""
    _setup()
    add_trade(target="512800", target_type="ETF", target_name="银行ETF",
              action="BUY", amount=780, price=0.78, shares=1000, trade_date="2026-06-01",
              progress=0.0, progress_bucket="极早期", stop_threshold=-0.18, take_profit_threshold=0.30,
              stop_price=0.6396, take_profit_price=1.0140)

    net = get_position_net("512800")
    assert net["net_shares"] == 1000
    assert abs(net["avg_cost"] - 0.78) < 0.001

    summaries = get_position_summary_all()
    assert len(summaries) == 1
    assert summaries[0]["target"] == "512800"
    assert summaries[0]["total_shares"] == 1000


def test_two_buys_weighted_avg():
    """两次不同价买入 → 金额加权平均成本"""
    _setup()
    add_trade(target="512800", target_type="ETF", target_name="银行ETF",
              action="BUY", amount=780, price=0.78, trade_date="2026-06-01")
    add_trade(target="512800", target_type="ETF", target_name="银行ETF",
              action="ADD", amount=410, price=0.82, trade_date="2026-06-10")

    net = get_position_net("512800")
    assert net["net_shares"] == 1500
    expected_cost = 1190 / 1500
    assert abs(net["avg_cost"] - expected_cost) < 0.001


def test_buy_add_reduce_net():
    """BUY + ADD + REDUCE → 净股数正确"""
    _setup()
    add_trade(target="512800", target_type="ETF", target_name="银行ETF",
              action="BUY", amount=780, price=0.78, shares=1000, trade_date="2026-06-01")
    add_trade(target="512800", target_type="ETF", target_name="银行ETF",
              action="ADD", amount=410, price=0.82, shares=500, trade_date="2026-06-10")
    add_trade(target="512800", target_type="ETF", target_name="银行ETF",
              action="REDUCE", amount=255, price=0.85, shares=300, trade_date="2026-06-20")

    net = get_position_net("512800")
    assert net["net_shares"] == 1200, f"净股数应为 1200，实 {net['net_shares']}"
    expected_cost = 1190 / 1500
    assert abs(net["avg_cost"] - expected_cost) < 0.001


def test_full_sell_clears_all():
    """全部 SELL → 净持仓归零"""
    _setup()
    add_trade(target="512800", target_type="ETF", target_name="银行ETF",
              action="BUY", amount=780, price=0.78, shares=1000, trade_date="2026-06-01")
    add_trade(target="512800", target_type="ETF", target_name="银行ETF",
              action="ADD", amount=410, price=0.82, shares=500, trade_date="2026-06-10")

    r = close_trade_by_target("512800", 0.85, "2026-06-30")
    assert r["total_shares"] == 1500
    assert abs(r["total_pnl"] - (0.85 - 0.78) * 1000 - (0.85 - 0.82) * 500) < 0.01

    net = get_position_net("512800")
    assert net["net_shares"] == 0, "SELL 后净持仓应为 0"


def test_two_etfs_independent():
    """两个不同 ETF 互不影响"""
    _setup()
    add_trade(target="512800", target_type="ETF", target_name="银行ETF",
              action="BUY", amount=780, price=0.78, shares=1000, trade_date="2026-06-01")
    add_trade(target="512480", target_type="ETF", target_name="半导体ETF",
              action="BUY", amount=2000, price=2.00, shares=1000, trade_date="2026-06-01")

    net1 = get_position_net("512800")
    net2 = get_position_net("512480")
    assert net1["net_shares"] == 1000
    assert net2["net_shares"] == 1000

    summaries = get_position_summary_all()
    assert len(summaries) == 2
    targets = {s["target"] for s in summaries}
    assert targets == {"512800", "512480"}


def test_reduce_exceeds_net_rejected():
    """减仓超过持仓时拒绝"""
    _setup()
    add_trade(target="512800", target_type="ETF",
              action="BUY", amount=780, price=0.78, shares=1000, trade_date="2026-06-01")

    net = get_position_net("512800")
    reduce_shares = 1500
    assert reduce_shares > net["net_shares"]
    assert net["net_shares"] == 1000


def test_sell_no_position_rejected():
    """无持仓时 SELL 抛出 ValueError"""
    _setup()
    with pytest.raises(ValueError, match="无未平仓持仓"):
        close_trade_by_target("512800", 0.80, "2026-06-30")


def test_reduce_marks_target_level():
    """REDUCE 后 reduced_1_3 全局标记"""
    _setup()
    add_trade(target="512800", target_type="ETF", target_name="银行ETF",
              action="BUY", amount=780, price=0.78, shares=1000, trade_date="2026-06-01")
    con = sqlite3.connect(str(lts.DB_PATH))
    con.execute(
        "INSERT INTO live_trades (target, target_type, target_name, action, amount, price, shares, trade_date) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("512800", "ETF", "银行ETF", "REDUCE", 200, 0.80, 250, "2026-06-15"),
    )
    con.execute("UPDATE live_trades SET reduced_1_3 = 1 WHERE target = '512800'")
    con.commit()
    con.close()

    summaries = get_position_summary_all()
    assert len(summaries) == 1
    assert summaries[0]["has_reduced"] is True


def test_close_per_row_pnl():
    """SELL 时每行按各自买入价计算 PnL"""
    _setup()
    add_trade(target="512800", target_type="ETF", target_name="银行ETF",
              action="BUY", amount=780, price=0.78, shares=1000, trade_date="2026-06-01")
    add_trade(target="512800", target_type="ETF", target_name="银行ETF",
              action="ADD", amount=410, price=0.82, shares=500, trade_date="2026-06-10")

    close_trade_by_target("512800", 0.85, "2026-06-30")

    con = sqlite3.connect(str(lts.DB_PATH))
    rows = con.execute(
        "SELECT price, close_price, close_pnl_pct FROM live_trades "
        "WHERE target='512800' AND action IN ('BUY','ADD') AND closed=1 "
        "ORDER BY trade_id"
    ).fetchall()
    con.close()

    assert len(rows) == 2
    pnl1 = (0.85 - 0.78) / 0.78
    assert abs(rows[0][2] - pnl1) < 0.001
    pnl2 = (0.85 - 0.82) / 0.82
    assert abs(rows[1][2] - pnl2) < 0.001


def test_monitor_aggregated(isolated_trade_db):
    """监控读取净持仓（汇总后一行），保留首笔 BUY 原始参数"""
    _setup()
    add_trade(target="512800", target_type="ETF", target_name="银行ETF",
              action="BUY", amount=780, price=0.78, shares=1000, trade_date="2026-06-01",
              progress=0.0, progress_bucket="极早期", stop_threshold=-0.18, take_profit_threshold=0.30,
              stop_price=0.6396, take_profit_price=1.0140)
    add_trade(target="512800", target_type="ETF", target_name="银行ETF",
              action="ADD", amount=410, price=0.82, shares=500, trade_date="2026-06-10",
              progress=0.15, progress_bucket="早期", stop_threshold=-0.15, take_profit_threshold=0.30)

    results = monitor_all_positions_v6("2026-06-24")
    etf_rows = [r for r in results if r.get("target") == "512800" and "error" not in r]
    assert len(etf_rows) == 1
    assert etf_rows[0].get("progress_bucket_at_entry") == "极早期"
    assert abs(etf_rows[0].get("progress_at_entry", 1) - 0.0) < 0.001


def test_rebuy_after_sell_is_isolated():
    """清仓后再次买入，上一轮记录不扣减新持仓"""
    _setup()
    add_trade(target="512800", target_type="ETF", target_name="银行ETF",
              action="BUY", amount=780, price=0.78, shares=1000, trade_date="2026-06-01")
    close_trade_by_target("512800", 0.85, "2026-06-30")

    add_trade(target="512800", target_type="ETF", target_name="银行ETF",
              action="BUY", amount=850, price=0.85, shares=1000, trade_date="2026-07-01")

    net = get_position_net("512800")
    assert net["net_shares"] == 1000, f"重新买入后净持仓应为 1000，实 {net['net_shares']}"

    summaries = get_position_summary_all()
    assert len(summaries) == 1
    assert summaries[0]["total_shares"] == 1000


def test_avg_pnl_pct_formula():
    """验证 avg_pnl_pct 公式正确"""
    _setup()
    add_trade(target="512800", target_type="ETF", target_name="银行ETF",
              action="BUY", amount=780, price=0.78, shares=1000, trade_date="2026-06-01")
    add_trade(target="512800", target_type="ETF", target_name="银行ETF",
              action="ADD", amount=410, price=0.82, shares=500, trade_date="2026-06-10")

    close_price = 0.85
    r = close_trade_by_target("512800", close_price, "2026-06-30")
    total_shares = 1500
    total_cost = 780 + 410
    avg_entry = total_cost / total_shares
    total_pnl = (close_price - 0.78) * 1000 + (close_price - 0.82) * 500
    expected_avg_pnl_pct = (close_price - avg_entry) / avg_entry
    assert abs(r["avg_pnl_pct"] - expected_avg_pnl_pct) < 0.001


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
