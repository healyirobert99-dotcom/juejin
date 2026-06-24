"""
实盘影子测试 v5 — 加仓规则

规则：
- 入场时建仓 8% / 12%（首次/重复）
- 跌到 -8% 时加仓 3%（摊低成本）
- 跌到 -15% 时再加仓 3%
- 总亏损达到 -20% 时清仓（总仓位 14% × 20% = 2.8%）
- 涨到 30% 减仓 1/3（同 v3）

加仓后加权成本计算：
- 初始 8% 仓位 @ P0
- -8% 加仓 3% @ P0 × 0.92
- -15% 加仓 3% @ P0 × 0.85
- 平均成本 = (8×P0 + 3×0.92×P0 + 3×0.85×P0) / (8+3+3) = 0.931×P0
- 整体浮亏 = (current - avg_cost) / avg_cost
- 触发 -20% 整体止损 = (current - avg_cost) / avg_cost ≤ -0.20
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


def simulate_v5(max_return: float, min_return: float, final_return: float,
                position_pct: float) -> dict:
    """
    v5 加仓规则模拟

    关键：max/min/final 是相对于初始入场价的累计涨跌幅
    加仓点：-8% / -15%（相对于初始入场价）
    整体止损：-20%（相对于加权平均成本）
    """
    # 加仓点
    add1_trigger = -0.08  # 跌 8% 加仓 3%
    add2_trigger = -0.15  # 跌 15% 再加 3%
    total_stop = -0.20    # 整体浮亏 20% 清仓

    # 加权平均成本（简化：假设加仓都按入场价 × 对应比例）
    # 初始 8% @ P0 → 投入 8
    # -8% 加仓 3% @ 0.92*P0 → 投入 3 * 0.92 = 2.76
    # -15% 加仓 3% @ 0.85*P0 → 投入 3 * 0.85 = 2.55
    # 累计投入 = 8 + 2.76 + 2.55 = 13.31
    # 累计份额 = 8/P0 + 3/(0.92*P0) + 3/(0.85*P0) = 8 + 3.26 + 3.53 = 14.79
    # 平均成本 = 13.31 / 14.79 = 0.90 * P0
    # 所以整体浮亏 20% = 价跌到 0.90 * 0.80 = 0.72 * P0 = -28% (相对 P0)
    # 翻译：-28% 相对入场价 = 整体 -20%

    # 简化计算：把整体止损转为"相对初始入场价"的下限
    avg_cost_ratio = 0.90  # 加权平均成本相对入场价的比例
    actual_stop = avg_cost_ratio * (1 + total_stop)  # = 0.72

    # 路径判断（简化为"先跌后涨" vs "一直跌" vs "一直涨"）
    if min_return <= actual_stop:
        # 触及整体止损
        return {
            "actual_return": total_stop * position_pct * 1.4,  # 14% 仓位的 20% 亏损
            "exit_reason": "TOTAL_STOP_20PCT",
            "add_count": 2,  # 触发 2 次加仓
        }

    if min_return <= add2_trigger:
        # 触发 2 次加仓但未触及整体止损
        if max_return >= 0.30:
            # 涨到 30% 时减仓 1/3
            # 加权平均成本 = 0.90 * P0
            # 30% 止盈 = 1.30 * P0
            # 整体收益 = 1.30/0.90 - 1 = 0.444 = 44.4%
            # 减仓 1/3 时收益：14% 仓位 × 44.4% × 0.333 + 14% × final × 0.667
            tier1 = (1.30 / avg_cost_ratio - 1) * 0.333
            tier2 = (final_return / avg_cost_ratio) * 0.667
            weighted = tier1 * 0.333 + tier2 * 0.667
            return {
                "actual_return": weighted * position_pct * 1.4,  # 加仓后仓位 × 1.4
                "exit_reason": "TAKE_PROFIT_30_AFTER_ADD",
                "add_count": 2,
            }
        # 未触发 30% 止盈，持有到期
        weighted = final_return / avg_cost_ratio
        return {
            "actual_return": weighted * position_pct * 1.4,
            "exit_reason": "HOLD_AFTER_ADD2",
            "add_count": 2,
        }

    if min_return <= add1_trigger:
        # 触发 1 次加仓
        # 加权成本：8 + 3*0.92 = 10.76 投入；份额 = 8 + 3.26 = 11.26
        # 平均成本 = 0.956 * P0
        avg_cost_1 = 0.956
        if max_return >= 0.30:
            tier1 = (1.30 / avg_cost_1 - 1) * 0.333
            tier2 = (final_return / avg_cost_1) * 0.667
            weighted = tier1 * 0.333 + tier2 * 0.667
            return {
                "actual_return": weighted * position_pct * 1.15,  # 加仓后仓位 × 1.15
                "exit_reason": "TAKE_PROFIT_30_AFTER_ADD1",
                "add_count": 1,
            }
        weighted = final_return / avg_cost_1
        return {
            "actual_return": weighted * position_pct * 1.15,
            "exit_reason": "HOLD_AFTER_ADD1",
            "add_count": 1,
        }

    # 未触发任何加仓
    if max_return >= 0.30:
        tier1 = 0.30 * 0.333
        tier2 = final_return * 0.667
        weighted = tier1 * 0.333 + tier2 * 0.667
        return {
            "actual_return": weighted * position_pct,
            "exit_reason": "TAKE_PROFIT_30",
            "add_count": 0,
        }
    return {
        "actual_return": final_return * position_pct,
        "exit_reason": "HOLD_TO_MATURITY",
        "add_count": 0,
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
        sim = simulate_v5(
            event["max_return"],
            event["min_return"],
            event["final_return"],
            position_pct,
        )
        results.append({
            "entity": event["entity"],
            "signal_date": event["signal_date"],
            "is_repeat": event["is_repeat"],
            "position_pct": position_pct,
            "max_return": event["max_return"],
            "min_return": event["min_return"],
            "final_return": event["final_return"],
            "actual_return": sim["actual_return"],
            "exit_reason": sim["exit_reason"],
            "add_count": sim["add_count"],
        })

    sim_df = pd.DataFrame(results)
    sim_df.to_csv(REPORTS_DIR / "shadow_trades_v5.csv", index=False, encoding="utf-8-sig")
    print(f"\n模拟交易明细: {REPORTS_DIR / 'shadow_trades_v5.csv'}")

    print("\n" + "=" * 60)
    print("实盘影子测试 v5 — 加仓规则")
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

    print(f"\n4. 加仓次数分布:")
    for cnt, num in sim_df["add_count"].value_counts().sort_index().items():
        print(f"   加仓 {cnt} 次: {num} 笔 ({num/n_total*100:.1f}%)")

    print(f"\n5. 首次 vs 重复触发对比:")
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
    print(f"\n6. 与'无规则持有到期'对比:")
    print(f"   持有到期 final 平均: {raw_return:.2f}%")
    print(f"   严格执行规则: {shadow_return:.2f}%")
    print(f"   规则提升/降低: {shadow_return - raw_return:+.2f}%")

    report_path = REPORTS_DIR / "SHADOW_TEST_REPORT_V5.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(
            f"""# 实盘影子测试 v5 — 加仓规则

**测试时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
**核心创新**: 入场后加仓摊低成本，整体浮亏 -20% 清仓

## 0. v3 vs v4 vs v5 对比

| 指标 | v3（15% 机械）| v4（20MA 行为）| v5（加仓 -20% 止损）|
|------|---------------|------------------|------------------------|
| 胜率 | 37.3% | 29.7% | {win_rate:.1f}% |
| 平均收益 | +0.25% | -0.05% | {avg_return:.2f}% |
| 中位收益 | -1.20% | -0.18% | {median_return:.2f}% |
| 累计收益 | +134.39% | -26.15% | {total_return:.2f}% |
| 最大亏损 | -1.80% | -5.03% | {max_loss:.2f}% |

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

## 4. 加仓次数分布

| 加仓次数 | 笔数 | 占比 |
|----------|------|------|
"""
        )
        for cnt, num in sim_df["add_count"].value_counts().sort_index().items():
            f.write(f"| 加仓 {cnt} 次 | {num} | {num/n_total*100:.1f}% |\n")

        f.write(
            f"""

## 5. 首次 vs 重复触发对比

| 维度 | 首次触发 | 重复触发 |
|------|----------|----------|
| 笔数 | {len(first)} | {len(repeat)} |
| 胜率 | {(first['actual_return'] > 0).mean()*100:.1f}% | {(repeat['actual_return'] > 0).mean()*100:.1f}% |
| 平均收益 | {first['actual_return'].mean()*100:.2f}% | {repeat['actual_return'].mean()*100:.2f}% |

## 6. 与'无规则'对比

| 策略 | 平均单笔收益 |
|------|--------------|
| 持有到期（final_return）| {raw_return:.2f}% |
| 严格执行规则（v5）| {shadow_return:.2f}% |
| **提升** | **{shadow_return - raw_return:+.2f}%** |
"""
        )
    print(f"\n报告已写入: {report_path}")
    return sim_df


if __name__ == "__main__":
    run_simulation()
