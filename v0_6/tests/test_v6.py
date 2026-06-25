"""
v0.6 测试 - 掘金信号 + 进度桶 + 监控

直接用 python 运行：
    python v0_6/tests/test_v6.py
"""
import sqlite3
import sys
from pathlib import Path

V0_6_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(V0_6_ROOT))

# 确保使用真实数据库，不受其他测试（如 test_ledger）的 DB_PATH 替换影响
from v0_6.core.config import DB_PATH as _REAL_DB
import v0_6.core.live_trade_store as _LTS
import v0_6.core.config as _CFG
_LTS.DB_PATH = _REAL_DB
_CFG.DB_PATH = _REAL_DB

from v0_6.core import (
    init_schema,
    add_trade,
    list_open_positions,
    evaluate_position_v6,
    monitor_all_positions_v6,
    get_bucket,
    get_position_pct,
    calc_stop_price,
    calc_take_profit_price,
    SIGNAL_FACTORS,
    PROGRESS_BUCKETS,
)


def test_v6_constants():
    print("\n[Test 1] v6 常量定义")
    assert SIGNAL_FACTORS["name"] == "掘金信号"
    assert len(SIGNAL_FACTORS["factors"]) == 4
    assert len(PROGRESS_BUCKETS) == 4
    print(f"  OK 信号名称: {SIGNAL_FACTORS['name']}")
    print(f"  OK 因子数: {len(SIGNAL_FACTORS['factors'])}")
    print(f"  OK 进度桶数: {len(PROGRESS_BUCKETS)}")
    for b in PROGRESS_BUCKETS:
        print(f"    {b['name']}: stop={b['stop']*100:.0f}%, tp={b['take_profit']*100:.0f}%, pos={b['position_first']*100:.0f}%")


def test_v6_bucket():
    print("\n[Test 2] 进度桶分桶")
    cases = [(0.05, "极早期"), (0.20, "早期"), (0.40, "中期"), (0.60, "晚期")]
    for progress, expected in cases:
        bucket = get_bucket(progress)
        assert bucket["name"] == expected
        print(f"  OK progress={progress:.2f} -> {bucket['name']}")


def test_v6_prices():
    print("\n[Test 3] 止损/止盈价")
    stop = calc_stop_price(100.0, 0.05)
    tp = calc_take_profit_price(100.0, 0.05)
    assert abs(stop - 82.0) < 0.01
    assert abs(tp - 130.0) < 0.01
    print(f"  OK 极早期 entry=100: stop={stop}, tp={tp}")


def test_v6_workflow():
    from v0_6.core.config import DB_PATH as _R
    import v0_6.core.live_trade_store as _L
    import v0_6.core.config as _C
    _L.DB_PATH = _R; _C.DB_PATH = _R
    print("\n[Test 4] v6 交易流程")
    init_schema()
    con = sqlite3.connect("D:/zhuxian-catch-v0_6/data/a_stock_selector.sqlite3", uri=False)
    con.execute("DELETE FROM live_trades")
    con.commit()
    con.close()

    trade_id = add_trade(
        target="512480", target_type="ETF", target_name="国联安中证全指半导体ETF",
        industry="半导体", action="BUY", price=100.0, shares=1000,
        trade_date="2025-06-15", signal_type="GOLD", position_pct=0.08,
        progress=0.05, notes="v6 test",
    )
    print(f"  OK buy trade_id={trade_id}")
    positions = list_open_positions()
    assert any(p["trade_id"] == trade_id for p in positions), f"trade_id={trade_id} 应出现在持仓列表中"
    pos = next(p for p in positions if p["trade_id"] == trade_id)
    assert pos["progress_bucket"] == "极早期"
    assert abs(pos["stop_price"] - 82.0) < 0.01
    print(f"  OK bucket={pos['progress_bucket']}, stop=¥{pos['stop_price']:.2f}, tp=¥{pos['take_profit_price']:.2f}")


def test_v6_monitor():
    print("\n[Test 5] v6 监控")
    results = monitor_all_positions_v6("2026-06-23")
    print(f"  OK monitoring {len(results)} positions")
    for r in results:
        if "error" in r:
            print(f"  ! {r.get('target', '?')}: {r['error']}")
            continue
        print(f"  - {r.get('target', '?')} ({r['progress_bucket_at_entry']}): ret={r['return_pct']*100:+.1f}%, action={r['action']}, alerts={len(r['alerts'])}")
        for a in r["alerts"]:
            print(f"      [{a['priority']}] {a['type']}: {a['message']}")


def main():
    test_v6_constants()
    test_v6_bucket()
    test_v6_prices()
    test_v6_workflow()
    test_v6_monitor()
    print("\nAll tests passed!")


if __name__ == "__main__":
    main()
