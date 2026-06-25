"""
v0.6 core 模块测试
"""
import pytest


def test_load_rules():
    from v0_6.core import RULES
    assert "stop_loss" in RULES
    assert "take_profit" in RULES
    assert "position" in RULES
    assert "cooldown" in RULES
    assert "signal" in RULES
    assert RULES["stop_loss"]["mechanical"] == -0.08
    assert RULES["position"]["first_trigger"] == 0.08
    assert RULES["position"]["repeat_trigger"] == 0.12


def test_mark_repeat_priority():
    import pandas as pd
    from v0_6.core import mark_repeat_priority

    signals = pd.DataFrame(
        {
            "industry": ["半导体", "半导体", "半导体", "银行", "汽车"],
            "signal_date": pd.to_datetime(
                ["2026-01-15", "2026-03-15", "2026-06-15", "2026-04-01", "2026-05-01"]
            ),
        }
    )
    result = mark_repeat_priority(signals, lookback_days=120)
    assert result[result["industry"] == "半导体"].iloc[0]["priority"] == "FIRST"
    assert result[result["industry"] == "半导体"].iloc[1]["priority"] == "REPEAT"
    assert result[result["industry"] == "半导体"].iloc[2]["priority"] == "REPEAT"
    assert result[result["industry"] == "银行"].iloc[0]["priority"] == "FIRST"
    assert result[result["industry"] == "汽车"].iloc[0]["priority"] == "FIRST"


def test_init_schema(isolated_trade_db):
    from v0_6.core import init_schema
    init_schema()
    assert True


def test_live_trade_store_crud(isolated_trade_db):
    from v0_6.core import add_trade, list_open_positions, close_trade, get_position_net

    trade_id = add_trade(
        industry="测试行业",
        action="BUY",
        price=1.0,
        shares=100,
        trade_date="2026-06-24",
        position_pct=0.08,
    )
    assert trade_id > 0

    positions = list_open_positions()
    assert any(p["trade_id"] == trade_id for p in positions)

    summary = get_position_net("测试行业")
    assert summary["net_shares"] == 100
    assert abs(summary["avg_cost"] - 1.0) < 0.01

    close_trade(trade_id, 1.10, "2026-06-30")
    positions = list_open_positions()
    assert not any(p["trade_id"] == trade_id for p in positions)
