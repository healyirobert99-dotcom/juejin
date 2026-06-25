"""
v0.6 测试 - 掘金信号 + 进度桶 + 监控

全部在 pytest fixture 提供的临时交易数据库中运行，
不接触真实交易数据库。
"""
import pytest
import sys
from pathlib import Path

V0_6_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(V0_6_ROOT))


def test_v6_constants():
    from v0_6.core import SIGNAL_FACTORS, PROGRESS_BUCKETS

    assert SIGNAL_FACTORS["name"] == "掘金信号"
    assert len(SIGNAL_FACTORS["factors"]) == 4
    assert len(PROGRESS_BUCKETS) == 4


def test_v6_bucket():
    from v0_6.core import get_bucket

    cases = [(0.05, "极早期"), (0.20, "早期"), (0.40, "中期"), (0.60, "晚期")]
    for progress, expected in cases:
        bucket = get_bucket(progress)
        assert bucket["name"] == expected


def test_v6_prices():
    from v0_6.core import calc_stop_price, calc_take_profit_price

    stop = calc_stop_price(100.0, 0.05)
    tp = calc_take_profit_price(100.0, 0.05)
    assert abs(stop - 82.0) < 0.01
    assert abs(tp - 130.0) < 0.01


def test_v6_workflow(isolated_trade_db):
    """完整交易流程：录入 → 查询 → 平仓"""
    from v0_6.core import init_schema, add_trade, list_open_positions, close_trade

    init_schema()
    trade_id = add_trade(
        target="512480", target_type="ETF", target_name="国联安中证全指半导体ETF",
        industry="半导体", action="BUY", price=100.0, shares=1000,
        trade_date="2025-06-15", signal_type="GOLD", position_pct=0.08,
        progress=0.05, notes="v6 test",
    )
    assert trade_id > 0

    positions = list_open_positions()
    assert any(p["trade_id"] == trade_id for p in positions)
    pos = next(p for p in positions if p["trade_id"] == trade_id)
    assert pos["progress_bucket"] == "极早期"
    assert abs(pos["stop_price"] - 82.0) < 0.01

    close_trade(trade_id, 110.0, "2026-06-30")


def test_v6_monitor(isolated_trade_db):
    """监控：在临时库中录入一笔 ETF 持仓后执行 v6 监控"""
    from v0_6.core import init_schema, add_trade, monitor_all_positions_v6
    import sqlite3

    init_schema()
    # 在当前测试的临时库中创建一笔持仓
    add_trade(
        target="512480", target_type="ETF", target_name="国联安中证全指半导体ETF",
        industry="半导体", action="BUY", price=100.0, shares=1000,
        trade_date="2025-06-15", signal_type="GOLD", position_pct=0.08,
        progress=0.05, notes="v6 monitor test",
    )

    results = monitor_all_positions_v6("2026-06-23")
    # 临时库中没有行情数据，监控可能返回错误或空列表，不assert具体内容
    # 只验证函数可以正常运行不抛异常
    assert isinstance(results, list)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
