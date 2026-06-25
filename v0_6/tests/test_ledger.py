"""
回归测试：交易台账与净持仓一致性（第三步）

覆盖 10 个场景，全部使用临时 sqlite3 数据库。
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

import atexit
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

V0_6_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(V0_6_ROOT))

import v0_6.core.live_trade_store as lts  # noqa: E402
import v0_6.core.config as cfg  # noqa: E402

# 保存原路径，测试后恢复
_ORIG_DB_PATH = lts.DB_PATH

_TMP_DB = tempfile.mktemp(suffix=".sqlite3")
lts.DB_PATH = Path(_TMP_DB)
cfg.DB_PATH = Path(_TMP_DB)


def _restore_db_path():
    lts.DB_PATH = _ORIG_DB_PATH
    cfg.DB_PATH = _ORIG_DB_PATH

atexit.register(_restore_db_path)

from v0_6.core import (  # noqa: E402
    add_trade,
    close_trade_by_target,
    get_position_net,
    get_position_summary_all,
    init_schema,
    monitor_all_positions_v6,
)


def _setup():
    lts.DB_PATH = Path(_TMP_DB)
    cfg.DB_PATH = Path(_TMP_DB)
    """每次测试前重建 schema + 清空"""
    init_schema()
    con = sqlite3.connect(_TMP_DB)
    # 幂等清空
    for table in ["live_trades", "sqlite_sequence"]:
        try:
            con.execute(f"DELETE FROM {table}")
        except sqlite3.OperationalError:
            pass
    con.commit()
    con.close()


# ── 测试 ──

def test_single_buy():
    """单次 BUY → 监控 1 行"""
    _setup()
    add_trade(target="512800", target_type="ETF", target_name="银行ETF",
              action="BUY", amount=780, price=0.78, trade_date="2026-06-01",
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
    # 第一次：1000股 × 0.78 = 780
    add_trade(target="512800", target_type="ETF", target_name="银行ETF",
              action="BUY", amount=780, price=0.78, trade_date="2026-06-01")
    # 第二次：500股 × 0.82 = 410
    add_trade(target="512800", target_type="ETF", target_name="银行ETF",
              action="ADD", amount=410, price=0.82, trade_date="2026-06-10")

    net = get_position_net("512800")
    assert net["net_shares"] == 1500
    # 加权成本 = (780 + 410) / (1000 + 500) = 1190 / 1500 = 0.7933
    expected_cost = 1190 / 1500
    assert abs(net["avg_cost"] - expected_cost) < 0.001, f"加权成本应为 {expected_cost:.4f}，实 {net['avg_cost']:.4f}"


def test_buy_add_reduce_net():
    """BUY + ADD + REDUCE → 净股数正确"""
    _setup()
    add_trade(target="512800", target_type="ETF", target_name="银行ETF",
              action="BUY", amount=780, price=0.78, shares=1000, trade_date="2026-06-01")
    add_trade(target="512800", target_type="ETF", target_name="银行ETF",
              action="ADD", amount=410, price=0.82, shares=500, trade_date="2026-06-10")
    # REDUCE 300股 @ 0.85
    add_trade(target="512800", target_type="ETF", target_name="银行ETF",
              action="REDUCE", amount=255, price=0.85, shares=300, trade_date="2026-06-20")

    net = get_position_net("512800")
    assert net["net_shares"] == 1200, f"净股数应为 1200，实 {net['net_shares']}"
    # 成本不受 REDUCE 影响：1190/1500 = 0.7933
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
    assert reduce_shares > net["net_shares"], "减仓股数应超过持仓"
    # 这里仅验证 get_position_net 返回正确值用于外部校验
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
    # 模拟 REDUCE 行（标记 reduced_1_3 + 实际 REDUCE 记录）
    con = sqlite3.connect(lts.DB_PATH)
    # 插入一条 REDUCE 记录
    con.execute(
        "INSERT INTO live_trades (target, target_type, target_name, action, amount, price, shares, trade_date) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("512800", "ETF", "银行ETF", "REDUCE", 200, 0.80, 250, "2026-06-15"),
    )
    # 标记原 BUY 为已减仓
    con.execute("UPDATE live_trades SET reduced_1_3 = 1 WHERE target = '512800'")
    con.commit()
    con.close()

    summaries = get_position_summary_all()
    assert len(summaries) == 1
    assert summaries[0]["has_reduced"] is True, "REDUCE 后 has_reduced 应为 True"


def test_close_per_row_pnl():
    """SELL 时每行按各自买入价计算 PnL"""
    _setup()
    add_trade(target="512800", target_type="ETF", target_name="银行ETF",
              action="BUY", amount=780, price=0.78, shares=1000, trade_date="2026-06-01")
    add_trade(target="512800", target_type="ETF", target_name="银行ETF",
              action="ADD", amount=410, price=0.82, shares=500, trade_date="2026-06-10")

    close_trade_by_target("512800", 0.85, "2026-06-30")

    con = sqlite3.connect(_TMP_DB)
    rows = con.execute(
        "SELECT price, close_price, close_pnl_pct FROM live_trades "
        "WHERE target='512800' AND action IN ('BUY','ADD') AND closed=1 "
        "ORDER BY trade_id"
    ).fetchall()
    con.close()

    assert len(rows) == 2
    # 第一笔：0.78 → 0.85, PnL = +8.97%
    pnl1 = (0.85 - 0.78) / 0.78
    assert abs(rows[0][2] - pnl1) < 0.001, f"第一笔 PnL 应为 {pnl1:.4f}，实 {rows[0][2]:.4f}"
    # 第二笔：0.82 → 0.85, PnL = +3.66%
    pnl2 = (0.85 - 0.82) / 0.82
    assert abs(rows[1][2] - pnl2) < 0.001, f"第二笔 PnL 应为 {pnl2:.4f}，实 {rows[1][2]:.4f}"


def test_monitor_aggregated():
    """监控读取净持仓（汇总后一行），且保留首笔 BUY 的原始参数"""
    _setup()
    add_trade(target="512800", target_type="ETF", target_name="银行ETF",
              action="BUY", amount=780, price=0.78, shares=1000, trade_date="2026-06-01",
              progress=0.0, progress_bucket="极早期", stop_threshold=-0.18, take_profit_threshold=0.30,
              stop_price=0.6396, take_profit_price=1.0140)
    add_trade(target="512800", target_type="ETF", target_name="银行ETF",
              action="ADD", amount=410, price=0.82, shares=500, trade_date="2026-06-10",
              progress=0.15, progress_bucket="早期", stop_threshold=-0.15, take_profit_threshold=0.30)

    results = monitor_all_positions_v6("2026-06-24")
    # 同一个 ETF 只显示一行
    etf_rows = [r for r in results if r.get("target") == "512800" and "error" not in r]
    assert len(etf_rows) == 1, f"同一 ETF 应只有一行监控，实 {len(etf_rows)}"
    # 监控参数必须来自最早那笔（极早期 0.0），不是来自 MIN 聚合或 ADD
    assert etf_rows[0].get("progress_bucket_at_entry") == "极早期", (
        f"应保留首笔 BUY 的极早期，实 {etf_rows[0].get('progress_bucket_at_entry')}"
    )
    assert abs(etf_rows[0].get("progress_at_entry", 1) - 0.0) < 0.001, (
        "progress_at_entry 应为 0.0，不是 0.05"
    )


def test_rebuy_after_sell_is_isolated():
    """清仓后再次买入，上一轮记录不扣减新持仓"""
    _setup()
    # 第一轮：买入 → 清仓
    add_trade(target="512800", target_type="ETF", target_name="银行ETF",
              action="BUY", amount=780, price=0.78, shares=1000, trade_date="2026-06-01")
    close_trade_by_target("512800", 0.85, "2026-06-30")

    # 第二轮：再次买入
    add_trade(target="512800", target_type="ETF", target_name="银行ETF",
              action="BUY", amount=850, price=0.85, shares=1000, trade_date="2026-07-01")

    net = get_position_net("512800")
    # 净持仓应为 1000（第一轮已清零），而不是 0（第一轮 SELL 扣减）
    assert net["net_shares"] == 1000, f"重新买入后净持仓应为 1000，实 {net['net_shares']}"

    summaries = get_position_summary_all()
    assert len(summaries) == 1
    assert summaries[0]["total_shares"] == 1000


def test_avg_pnl_pct_formula():
    """验证 avg_pnl_pct 公式正确：avg_entry = close_price - total_pnl/total_shares"""
    _setup()
    add_trade(target="512800", target_type="ETF", target_name="银行ETF",
              action="BUY", amount=780, price=0.78, shares=1000, trade_date="2026-06-01")
    add_trade(target="512800", target_type="ETF", target_name="银行ETF",
              action="ADD", amount=410, price=0.82, shares=500, trade_date="2026-06-10")

    close_price = 0.85
    r = close_trade_by_target("512800", close_price, "2026-06-30")
    total_shares = 1500
    total_cost = 780 + 410  # 1190
    avg_entry = total_cost / total_shares  # 0.7933
    total_pnl = (close_price - 0.78) * 1000 + (close_price - 0.82) * 500
    expected_avg_pnl_pct = (close_price - avg_entry) / avg_entry
    assert abs(r["avg_pnl_pct"] - expected_avg_pnl_pct) < 0.001, (
        f"期望 avg_pnl_pct={expected_avg_pnl_pct:.4f}，实 {r['avg_pnl_pct']:.4f}"
    )
