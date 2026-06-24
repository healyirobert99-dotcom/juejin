"""
v0.6 日报 V1.0 主入口

不动主系统。读取 v0_6/core 的信号 + 实盘监控 + 行业卡片，输出 V1.0 日报

用法：
    python v0_6/scripts/run_daily_v1.py                # 用今日
    python v0_6/scripts/run_daily_v1.py 2026-06-23     # 指定日期
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

from v0_6.core import (
    RULES,
    compute_industry_daily_metrics,
    compute_per_stock_indicators,
    detect_stabilizing_b,
    load_raw_daily,
    mark_repeat_priority,
    monitor_all_positions,
    monitor_all_positions_v6,
    SIGNAL_FACTORS,
)
from v0_6.core.config import DAILY_DIR, MONITORING_DIR


def get_today_signals(today: str, lookback_days: int = 7):
    """获取最近 N 日的 B 方案信号（含今日）"""
    print(f"加载 16 年原始日线...")
    sd = load_raw_daily("2010-01-01", today)
    print(f"  ✓ {len(sd):,} 行 / {sd['ts_code'].nunique():,} 只 / {sd['industry'].nunique()} 个行业")

    print("计算个股 + 行业指标...")
    sd_ind = compute_per_stock_indicators(sd)
    daily = compute_industry_daily_metrics(sd_ind)
    print(f"  ✓ {len(daily):,} 行业-日 样本")

    print("检测 B 方案信号...")
    signals = detect_stabilizing_b(daily)
    signals = mark_repeat_priority(signals)
    print(f"  ✓ B 方案信号: {len(signals)} 个")

    today_dt = pd.to_datetime(today)
    recent = signals[signals["signal_date"] >= (today_dt - pd.Timedelta(days=lookback_days))]
    return recent, signals


def render_daily_v1(today: str, today_signals: pd.DataFrame, monitoring: list, all_signals: pd.DataFrame) -> str:
    """渲染 V1.0 日报 Markdown"""
    today_dt = pd.to_datetime(today)

    today_only = today_signals[today_signals["signal_date"] == today_dt]
    n_today = len(today_only)
    n_repeat = len(today_only[today_only["priority"] == "REPEAT"])
    n_first = len(today_only[today_only["priority"] == "FIRST"])
    n_alerts = sum(len(r["alerts"]) for r in monitoring)

    md = []
    md.append(f"# A 股主线日报 V1.0 | {today}")
    md.append("")

    md.append("## 1️⃣ 今日结论（5 秒看完）")
    md.append("")
    md.append(f"- 🎯 **{SIGNAL_FACTORS['name']}触发**：{n_today} 个行业（🆕 首次 {n_first} / 🔁 重复 {n_repeat}）")
    md.append(f"- ⚠️ **监控告警**：{n_alerts} 条")
    if today_only.empty:
        md.append("- 💡 **建议**：今日无新信号，关注持仓监控")
    else:
        md.append(f"- 💡 **建议总仓位**：{n_today * 8}-{n_today * 12}%（首次 8% / 重复 12%）")
    md.append(f"- 🛡 **v6 止损规则**：极早期-18% / 早期-15% / 中期-12% / 晚期-8%")
    md.append(f"- 🎯 **v6 止盈规则**：极早期+30% / 早期+30% / 中期+25% / 晚期+20%（减 1/3 仓）")
    md.append("")

    md.append("## 2️⃣ 今日操作清单（≤6 行）")
    md.append("")
    if today_only.empty:
        md.append("| 动作 | 行业 | 信号 | 仓位 | 止损 | 持有期 |")
        md.append("|------|------|------|------|------|--------|")
        md.append("| — | 今日无触发 | — | — | — | — |")
    else:
        md.append("| 动作 | 行业 | 信号类型 | 仓位 | 止损 | 持有期 |")
        md.append("|------|------|---------|------|------|--------|")
        for _, sig in today_only.iterrows():
            priority = sig["priority"]
            pos_pct = RULES["position"]["repeat_trigger"] if priority == "REPEAT" else RULES["position"]["first_trigger"]
            sig_label = "🔁 重复" if priority == "REPEAT" else "🆕 首次"
            days_since = int(sig["days_since_prev"]) if pd.notna(sig["days_since_prev"]) else "—"
            md.append(
                f"| 买入 | {sig['industry']} | {sig_label}（{days_since}d）| {pos_pct*100:.0f}% | -7% | 60d |"
            )
    md.append("")

    md.append("## 3️⃣ 实盘监控（v6 掘金信号规则）")
    md.append("")
    if not monitoring:
        md.append("📭 当前无未平仓持仓")
    else:
        md.append("| 行业 | 入场日 | 桶 | 仓位 | 止损价 | 止盈价 | 现价 | 浮盈 | 距止损 | 距止盈 | 操作 |")
        md.append("|------|--------|-----|------|--------|--------|------|------|--------|--------|------|")
        for r in monitoring:
            if "error" in r:
                continue
            action_emoji = {
                "HOLD": "🟢",
                "WATCH": "🟡",
                "CLEAR": "🔴",
                "REDUCE_1_3": "🟠",
                "ADJUST": "🟡",
            }.get(r["action"], "⚪")
            md.append(
                f"| {r['industry']} | {r['entry_date']} | {r['progress_bucket_at_entry']} | "
                f"{r['stop_threshold']*-100:.0f}%止 | ¥{r['stop_price']:.4f} | ¥{r['take_profit_price']:.4f} | "
                f"¥{r['current_price']:.4f} | {r['return_pct']*100:+.1f}% | "
                f"{r['distance_to_stop']*100:+.1f}% | {r['distance_to_tp']*100:+.1f}% | "
                f"{action_emoji} {r['action']} |"
            )
    md.append("")

    md.append("## 4️⃣ 重点行业卡片（限 3 个）")
    md.append("")
    top = today_only.sort_values(["priority", "breadth_at_signal"], ascending=[True, False]).head(3)
    medals = ["🥇", "🥈", "🥉"]
    for i, (_, sig) in enumerate(top.iterrows()):
        md.append(f"### {medals[i]} {sig['industry']}")
        md.append(f"- **信号日**：{sig['signal_date'].strftime('%Y-%m-%d')}")
        md.append(f"- **触发类型**：{sig['priority']}（{int(sig['days_since_prev']) if pd.notna(sig['days_since_prev']) else '首次'}）")
        md.append(f"- **行业宽度**：{sig['breadth_at_signal']*100:.1f}%")
        md.append(f"- **量比**：{sig['vol_ratio_at_signal']:.2f}")
        md.append(f"- **20 日均涨幅**：{sig['avg_ret20_at_signal']*100:+.1f}%")
        md.append(f"- **行业股票数**：{sig['n_stocks']}")
        pos_pct = RULES["position"]["repeat_trigger"] if sig["priority"] == "REPEAT" else RULES["position"]["first_trigger"]
        md.append(f"- **建议**：买入 {pos_pct*100:.0f}%，设 -7% 止损")
        md.append("")

    md.append("## 5️⃣ 监控告警（v6 规则）")
    md.append("")
    triggered = [r for r in monitoring if r.get("alerts")]
    if not triggered:
        md.append("✅ 当前无告警")
    else:
        for r in triggered:
            emoji = "🔴" if r.get("priority") == "RED" else "🟡"
            md.append(f"### {emoji} {r['industry']}（{r['return_pct']*100:+.1f}%）")
            for a in r["alerts"]:
                md.append(f"- **{a['type']}**：{a['message']}（建议动作：{a['action']}）")
            md.append("")

    md.append("---")
    md.append("")
    md.append(f"📊 数据源：daily-lite 库 + stock_data 16 年价量 | 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    return "\n".join(md)


def main():
    import pandas as pd

    if len(sys.argv) >= 2:
        today = sys.argv[1]
    else:
        today = datetime.now().strftime("%Y-%m-%d")

    print(f"\n=== 跑日报 V1.0 | {today} ===\n")

    print("[1/3] 检测 B 方案信号...")
    today_signals, all_signals = get_today_signals(today)

    print("\n[2/3] 监控实盘持仓（v6 规则）...")
    monitoring = monitor_all_positions_v6(today)
    print(f"  ✓ 监控 {len(monitoring)} 个未平仓持仓")

    print("\n[3/3] 渲染日报...")
    md = render_daily_v1(today, today_signals, monitoring, all_signals)

    out_path = DAILY_DIR / f"daily_v1_{today}.md"
    out_path.write_text(md, encoding="utf-8")
    print(f"  ✓ 日报: {out_path}")

    monitoring_md = render_monitoring(today, monitoring)
    mon_path = MONITORING_DIR / f"monitoring_{today}.md"
    mon_path.write_text(monitoring_md, encoding="utf-8")
    print(f"  ✓ 监控报告: {mon_path}")
    return 0


def render_monitoring(today: str, monitoring: list) -> str:
    md = [f"# 实盘监控报告 | {today}", ""]
    if not monitoring:
        md.append("📭 当前无未平仓持仓")
        return "\n".join(md)
    md.append(f"持仓数：{len(monitoring)}")
    md.append("")
    md.append("| 行业 | 入场日 | 现价 | 浮盈 | 5d 最低 | 10d 最低 | 持有 |")
    md.append("|------|--------|------|------|---------|---------|------|")
    for r in monitoring:
        ret = f"{r['return_pct']*100:+.1f}%"
        m5 = f"{r['min_return_5d']*100:.1f}%" if r["min_return_5d"] is not None else "-"
        m10 = f"{r['min_return_10d']*100:.1f}%" if r["min_return_10d"] is not None else "-"
        md.append(
            f"| {r['industry']} | {r['entry_date']} | {r['current_price']:.4f} | {ret} | {m5} | {m10} | {r['days_held']}d |"
        )
    md.append("")
    triggered = [r for r in monitoring if r["alerts"]]
    if triggered:
        md.append(f"## ⚠️ 告警（{len(triggered)} 条）\n")
        for r in triggered:
            md.append(f"### {r['industry']}")
            for a in r["alerts"]:
                md.append(f"- {a['message']}")
            md.append("")
    return "\n".join(md)


if __name__ == "__main__":
    sys.exit(main())
