"""
v0.6 独立监控报告入口（v6 掘金信号规则）

用法：
    python run_monitoring.py                       # 今日
    python run_monitoring.py 2026-06-24            # 指定日期
"""
import sys
from datetime import datetime
from pathlib import Path

V0_6_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(V0_6_ROOT))

from v0_6.core import monitor_all_positions_v6, init_schema
from v0_6.core.config import MONITORING_DIR


def render_monitoring_v6(today: str, results: list) -> str:
    md = [f"# 实盘监控报告 V6 | {today}", ""]
    if not results:
        md.append("📭 当前无未平仓持仓")
        return "\n".join(md)
    md.append(f"持仓数：**{len(results)}**")
    md.append("")
    md.append("## 持仓状态")
    md.append("")
    md.append("| 行业 | 入场日 | 桶 | 仓位 | 止损价 | 止盈价 | 现价 | 浮盈 | 距止损 | 距止盈 | 操作 | 告警 |")
    md.append("|------|--------|-----|------|--------|--------|------|------|--------|--------|------|------|")
    for r in results:
        if "error" in r:
            md.append(f"| {r.get('target', '?')} | - | - | - | - | - | - | - | - | - | - | 无价格数据 |")
            continue
        action_emoji = {
            "HOLD": "🟢", "WATCH": "🟡", "CLEAR": "🔴",
            "REDUCE_1_3": "🟠", "ADJUST": "🟡",
        }.get(r["action"], "⚪")
        alerts_str = "; ".join([a["type"] for a in r.get("alerts", [])]) or "—"
        md.append(
            f"| {r.get('target', '?')} | {r['entry_date']} | {r['progress_bucket_at_entry']} | "
            f"{r['stop_threshold']*-100:.0f}% | ¥{r['stop_price']:.4f} | ¥{r['take_profit_price']:.4f} | "
            f"¥{r['current_price']:.4f} | {r['return_pct']*100:+.1f}% | "
            f"{r['distance_to_stop']*100:+.1f}% | {r['distance_to_tp']*100:+.1f}% | "
            f"{action_emoji} {r['action']} | {alerts_str} |"
        )
    md.append("")

    # 告警汇总
    red = [r for r in results if r.get("priority") == "RED"]
    yellow = [r for r in results if r.get("priority") == "YELLOW"]

    if red:
        md.append(f"## 🔴 红色告警（{len(red)} 笔）")
        md.append("")
        for r in red:
            md.append(f"### {r.get('target', '?')}（{r['return_pct']*100:+.1f}%）")
            for a in r["alerts"]:
                md.append(f"- **{a['type']}**：{a['message']}（动作：{a['action']}）")
            md.append("")
    if yellow:
        md.append(f"## 🟡 黄色告警（{len(yellow)} 笔）")
        md.append("")
        for r in yellow:
            md.append(f"### {r.get('target', '?')}（{r['return_pct']*100:+.1f}%）")
            for a in r["alerts"]:
                md.append(f"- **{a['type']}**：{a['message']}（动作：{a['action']}）")
            md.append("")
    if not red and not yellow:
        md.append("## ✅ 全部持仓状态正常")
        md.append("")
    md.append("---")
    md.append(f"📊 v6 掘金信号规则 | 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    return "\n".join(md)


def main():
    if len(sys.argv) >= 2:
        today = sys.argv[1]
    else:
        today = datetime.now().strftime("%Y-%m-%d")

    print(f"\n=== 跑 v6 监控报告 | {today} ===\n")
    init_schema()
    results = monitor_all_positions_v6(today)
    print(f"  ✓ 监控 {len(results)} 个持仓")
    red = sum(1 for r in results if r.get("priority") == "RED")
    yellow = sum(1 for r in results if r.get("priority") == "YELLOW")
    print(f"  ✓ 红色告警: {red}, 黄色告警: {yellow}")

    md = render_monitoring_v6(today, results)
    out_path = MONITORING_DIR / f"monitoring_v6_{today}.md"
    out_path.write_text(md, encoding="utf-8")
    print(f"  ✓ 报告: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
