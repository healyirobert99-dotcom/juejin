"""
实盘影子测试 v3 — 修正版（基于 v2 真实结果）

v2 真实结果的关键发现：
- 79% 事件触发 8% 机械止损 → 阈值太严
- 20% + 40% 止盈 13.3% + 6.8% = 20% 触发，止盈占比较低
- 平均收益 0.01%（期望值几乎为 0）
- 最大亏损 0.96%（被 8% 锁住）

v3 调整方向：
1. 8% 机械止损 → 15% 机械止损（让 79% 触发率降到 ~30%）
2. 20%/40% 分档止盈 → 30% 单一止盈（简化规则）
3. 期望：胜率从 20.7% 升到 ~50%，平均从 0.01% 升到 ~2.5%
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

V0_6_ROOT = Path(__file__).resolve().parents[2]
PROJECT_V0_5 = Path("D:/zhuxian-catch_2026-06-24")
B_EVENTS_CSV = PROJECT_V0_5 / "reports" / "zhongqianqi" / "B_EVENTS.csv"
RULES_PATH = V0_6_ROOT / "v0_6" / "data" / "rules.json"
REPORTS_DIR = V0_6_ROOT / "reports" / "shadow"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

with open(RULES_PATH, encoding="utf-8") as f:
    RULES = json.load(f)


def simulate_v3(event: dict, position_pct: float) -> dict:
    """v3 修正版模拟：15% 止损 + 30% 单一止盈"""
    max_return = event["max_return"]
    min_return = event["min_return"]
    final_return = event["final_return"]

    # 规则 1: 15% 机械止损（替代 v2 的 8%）
    if min_return <= -0.15:
        return {
            "actual_return": -0.15 * position_pct,
            "exit_reason": "MECHANICAL_15PCT",
            "days_held": 50,
        }

    # 规则 2: 30% 单一止盈（替代 v2 的 20%/40%）
    if max_return >= 0.30:
        # 1/3 仓位在 30% 锁定，2/3 仓位持有到期
        tier1 = 0.30
        tier2 = final_return
        weighted = tier1 * 0.333 + tier2 * 0.667
        return {
            "actual_return": weighted * position_pct,
            "exit_reason": "TAKE_PROFIT_30",
            "days_held": 100,
        }

    # 默认: 持有到期
    return {
        "actual_return": final_return * position_pct,
        "exit_reason": "HOLD_TO_MATURITY",
        "days_held": 250,
    }


def run_simulation():
    events = pd.read_csv(B_EVENTS_CSV, encoding="utf-8-sig")
    print(f"B 方案事件数: {len(events)}")

    events = events.sort_values("signal_date").reset_index(drop=True)
    events["signal_date_dt"] = pd.to_datetime(events["signal_date"])
    events["prev_date"] = events.groupby("entity")["signal_date_dt"].shift(1)
    events["days_since_prev"] = (events["signal_date_dt"] - events["prev_date"]).dt.days
    events["is_repeat"] = events["days_since_prev"].apply(
        lambda x: 1 if pd.notna(x) and x <= 120 else 0
    )
    n_first = (events["is_repeat"] == 0).sum()
    n_repeat = (events["is_repeat"] == 1).sum()
    print(f"  首次触发: {n_first}, 重复触发: {n_repeat}")

    results = []
    for _, event in events.iterrows():
        position_pct = 0.12 if event["is_repeat"] else 0.08
        sim = simulate_v3(event.to_dict(), position_pct)
        results.append(
            {
                "entity": event["entity"],
                "signal_date": event["signal_date"],
                "is_repeat": event["is_repeat"],
                "position_pct": position_pct,
                "max_return": event["max_return"],
                "min_return": event["min_return"],
                "final_return": event["final_return"],
                "actual_return": sim["actual_return"],
                "exit_reason": sim["exit_reason"],
                "days_held": sim["days_held"],
            }
        )

    sim_df = pd.DataFrame(results)
    sim_df.to_csv(REPORTS_DIR / "shadow_trades_v3.csv", index=False, encoding="utf-8-sig")
    print(f"\n模拟交易明细: {REPORTS_DIR / 'shadow_trades_v3.csv'}")

    print("\n" + "=" * 60)
    print("实盘影子测试 v3 — 修正版核心结果")
    print("=" * 60)

    n_total = len(sim_df)
    n_pos = (sim_df["actual_return"] > 0).sum()
    n_neg = (sim_df["actual_return"] <= 0).sum()
    win_rate = n_pos / n_total * 100

    print(f"\n1. 交易总数: {n_total}")
    print(f"   盈利: {n_pos} ({win_rate:.1f}%)")
    print(f"   亏损: {n_neg} ({100-win_rate:.1f}%)")

    avg_return = sim_df["actual_return"].mean() * 100
    median_return = sim_df["actual_return"].median() * 100
    total_return = sim_df["actual_return"].sum() * 100
    max_loss = sim_df["actual_return"].min() * 100
    max_gain = sim_df["actual_return"].max() * 100

    print(f"\n2. 单笔收益统计（仓位加权后）:")
    print(f"   平均收益: {avg_return:.2f}%")
    print(f"   中位收益: {median_return:.2f}%")
    print(f"   累计收益: {total_return:.2f}%")
    print(f"   最大盈利: {max_gain:.2f}%")
    print(f"   最大亏损: {max_loss:.2f}%")

    print(f"\n3. 退出原因分布:")
    for reason, cnt in sim_df["exit_reason"].value_counts().items():
        pct = cnt / n_total * 100
        print(f"   {reason}: {cnt} ({pct:.1f}%)")

    print(f"\n4. 首次 vs 重复触发对比:")
    first = sim_df[sim_df["is_repeat"] == 0]
    repeat = sim_df[sim_df["is_repeat"] == 1]
    print(f"   首次触发 ({len(first)} 笔):")
    print(f"     胜率: {(first['actual_return'] > 0).mean()*100:.1f}%")
    print(f"     平均收益: {first['actual_return'].mean()*100:.2f}%")
    print(f"   重复触发 ({len(repeat)} 笔):")
    print(f"     胜率: {(repeat['actual_return'] > 0).mean()*100:.1f}%")
    print(f"     平均收益: {repeat['actual_return'].mean()*100:.2f}%")

    raw_return = events["final_return"].mean() * 100
    shadow_return = sim_df["actual_return"].mean() * 100
    print(f"\n5. 与'无规则持有到期'对比:")
    print(f"   持有到期 final 平均: {raw_return:.2f}%")
    print(f"   严格执行规则: {shadow_return:.2f}%")
    print(f"   规则提升/降低: {shadow_return - raw_return:+.2f}%")

    report_path = REPORTS_DIR / "SHADOW_TEST_REPORT_V3.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(
            f"""# 实盘影子测试 v3 — 修正版

**测试时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
**调整**: 8% → 15% 机械止损；20%/40% → 30% 单一止盈

## 0. v2 vs v3 对比

| 指标 | v2（8% 止损）| v3（15% 止损）|
|------|--------------|----------------|
| 胜率 | 20.7% | {win_rate:.1f}% |
| 平均收益 | +0.01% | {avg_return:.2f}% |
| 中位收益 | -0.64% | {median_return:.2f}% |
| 累计收益 | +6.94% | {total_return:.2f}% |
| 最大亏损 | -0.96% | {max_loss:.2f}% |
| 规则效果 | -6.09% | {shadow_return - raw_return:+.2f}% |

## 1. 交易总览

| 指标 | 数值 |
|------|------|
| 交易总数 | {n_total} |
| 盈利数 | {n_pos} ({win_rate:.1f}%) |
| 亏损数 | {n_neg} ({100-win_rate:.1f}%) |
| 首次触发 | {n_first} 笔 |
| 重复触发 | {n_repeat} 笔 |

## 2. 单笔收益统计

| 指标 | 数值 |
|------|------|
| 平均收益 | {avg_return:.2f}% |
| 中位收益 | {median_return:.2f}% |
| 累计收益 | {total_return:.2f}% |
| 最大单笔盈利 | {max_gain:.2f}% |
| 最大单笔亏损 | {max_loss:.2f}% |

## 3. 退出原因分布

| 退出原因 | 笔数 | 占比 |
|----------|------|------|
"""
        )
        for reason, cnt in sim_df["exit_reason"].value_counts().items():
            pct = cnt / n_total * 100
            f.write(f"| {reason} | {cnt} | {pct:.1f}% |\n")

        f.write(
            f"""

## 4. 首次 vs 重复触发对比

| 维度 | 首次触发 | 重复触发 |
|------|----------|----------|
| 笔数 | {len(first)} | {len(repeat)} |
| 胜率 | {(first['actual_return'] > 0).mean()*100:.1f}% | {(repeat['actual_return'] > 0).mean()*100:.1f}% |
| 平均收益 | {first['actual_return'].mean()*100:.2f}% | {repeat['actual_return'].mean()*100:.2f}% |

## 5. 与'无规则'对比

| 策略 | 平均单笔收益 |
|------|--------------|
| 持有到期（final_return）| {raw_return:.2f}% |
| 严格执行规则（v3）| {shadow_return:.2f}% |
| **提升** | **{shadow_return - raw_return:+.2f}%** |
"""
        )
    print(f"\n报告已写入: {report_path}")
    return sim_df


if __name__ == "__main__":
    run_simulation()
