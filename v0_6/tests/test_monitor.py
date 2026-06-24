"""
v0.6 监控模块测试
"""
import pytest


def test_evaluate_position_no_data():
    from v0_6.core import evaluate_position
    result = evaluate_position(
        industry="不存在的行业",
        entry_date="2026-06-01",
        entry_price=1.0,
        today="2026-06-24",
    )
    assert "error" in result
    assert result["alerts"] == []


def test_evaluate_position_normal():
    from v0_6.core import evaluate_position
    result = evaluate_position(
        industry="银行",
        entry_date="2026-06-15",
        entry_price=1.0,
        today="2026-06-24",
    )
    if "error" in result:
        pytest.skip(f"数据不足: {result['error']}")
    assert "current_price" in result
    assert "return_pct" in result
    assert "alerts" in result
    assert isinstance(result["alerts"], list)


def test_monitor_all_positions_empty():
    from v0_6.core import monitor_all_positions
    result = monitor_all_positions("2020-01-01")
    assert isinstance(result, list)
