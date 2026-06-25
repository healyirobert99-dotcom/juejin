"""
v0.6 持仓状态查询（v6 升级：掘金信号 + 进度桶 + 止损/止盈提醒）

用法：
    python query_state.py
    python query_state.py --today 2026-06-24
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path

V0_6_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(V0_6_ROOT))

from v0_6.core import monitor_all_positions_v6, init_schema, get_position_summary_all
from v0_6.core.market_data import validate_market_data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--today", default=None, help="查询日期 YYYY-MM-DD")
    args = parser.parse_args()

    init_schema()
    requested_date = args.today or datetime.now().strftime("%Y-%m-%d")
    print(f"查询日期: {requested_date}\n")

    # 数据门禁
    open_positions = get_position_summary_all()
    validation = validate_market_data(requested_date, open_positions=open_positions)
    if not validation["ok"]:
        print("行情数据未就绪，暂停生成持仓操作建议。")
        print(f"请求日期：{requested_date}")
        print(f"预期交易日：{validation.get('expected_trade_date', '不可用')}")
        print("错误：")
        for err in validation["errors"]:
            print(f"  - {err}")
        for pe in validation.get("position_errors", []):
            print(f"  - {pe['target']} ({pe['target_type']}): {pe['error']} (actual={pe['actual_date']}, expected={pe['expected_date']})")
        sys.exit(2)

    results = monitor_all_positions_v6(today=requested_date)

    if not results:
        print("暂无未平仓持仓。")
        return

    print(f"{'标的':<8} {'入场日':<12} {'桶':<6} {'止损阈值':<10} {'止损价':<10} {'止盈价':<10} {'现价':<10} {'浮盈':<8} {'距止损':<8} {'距止盈':<8} {'价格日期':<12} {'操作':<10} {'告警'}")
    print("-" * 120)
    for r in results:
        if "error" in r:
            print(f"{r.get('target', '?'):<8} - 无价格数据")
            continue
        action_emoji = {
            "HOLD": "🟢", "WATCH": "🟡", "CLEAR": "🔴",
            "REDUCE_1_3": "🟠", "ADJUST": "🟡",
        }.get(r["action"], "⚪")
        alerts_str = "; ".join([a["message"] for a in r.get("alerts", [])])
        price_date = r.get("today", r.get("requested_date", "?"))
        print(
            f"{r.get('target', '?'):<8} "
            f"{r['entry_date']:<12} "
            f"{r['progress_bucket_at_entry']:<6} "
            f"{(r.get('stop_threshold', 0)*-1):.0%}止{'':<2} "
            f"¥{r['stop_price']:<9.4f} "
            f"¥{r['take_profit_price']:<9.4f} "
            f"¥{r['current_price']:<9.4f} "
            f"{r['return_pct']*100:>+6.1f}% "
            f"{r['distance_to_stop']*100:>6.1f}% "
            f"{r['distance_to_tp']*100:>6.1f}% "
            f"{price_date:<12} "
            f"{action_emoji} {r['action']:<8} "
            f"{alerts_str}"
        )

    # 告警汇总
    print("\n" + "=" * 80)
    print("今日告警汇总")
    print("=" * 80)
    red_alerts = [r for r in results if r.get("priority") == "RED"]
    yellow_alerts = [r for r in results if r.get("priority") == "YELLOW"]
    if red_alerts:
        print(f"\n🔴 红色告警（{len(red_alerts)} 笔）:")
        for r in red_alerts:
            for a in r["alerts"]:
                print(f"  - {r.get('target', '?')} | {a['type']} | {a['message']} | 操作: {a['action']}")
    if yellow_alerts:
        print(f"\n🟡 黄色告警（{len(yellow_alerts)} 笔）:")
        for r in yellow_alerts:
            for a in r["alerts"]:
                print(f"  - {r.get('target', '?')} | {a['type']} | {a['message']} | 操作: {a['action']}")
    if not red_alerts and not yellow_alerts:
        print("\n✅ 全部持仓状态正常。")
    print()


if __name__ == "__main__":
    main()
