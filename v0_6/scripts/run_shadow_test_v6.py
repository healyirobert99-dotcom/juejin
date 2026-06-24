"""
实盘影子测试 v6 — 进度过滤

核心思路：B 方案事件在不同进度（涨幅起点）上质量差异巨大
- 0-15% 极早期：质量差（容易假突破），但上行空间大
- 15-30% 早期：质量中等
- 30-50% 中期：质量较高
- 50%+ 晚期：质量最高但上行空间小

v6 规则：
- 按 progress 分桶用不同止损/止盈阈值
- 极早期：放宽止损到 18%，30% 止盈
- 早期：标准 v3 规则（15% 止损 + 30% 止盈）
- 中期：收紧止损到 12%，25% 止盈
- 晚期：持有到期即可（不再加止损/止盈）

进度定义：入场价相对 60 日最低点的涨幅
- progress < 0.10: 极早期
- 0.10 <= progress < 0.30: 早期
- 0.30 <= progress < 0.50: 中期
- progress >= 0.50: 晚期
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


def progress_bucket(progress: float) -> str:
    """分桶"""
    if progress < 0.10:
        return "极早期"
    elif progress < 0.30:
        return "早期"
    elif progress < 0.50:
        return "中期"
    else:
        return "晚期"


def simulate_v6(progress: float, max_return: float, min_return: float,
                final_return: float, position_pct: float) -> dict:
    """v6 进度过滤模拟"""
    bucket = progress_bucket(progress)

    # 各桶规则
    if bucket == "极早期":
        stop_threshold = -0.18
        take_profit = 0.30
    elif bucket == "早期":
        stop_threshold = -0.15
        take_profit = 0.30
    elif bucket == "中期":
        stop_threshold = -0.12
        take_profit = 0.25
    else:  # 晚期
        stop_threshold = -0.08
        take_profit = 0.20

    # 规则 1: 止损
    if min_return <= stop_threshold:
        return {
            "actual_return": stop_threshold * position_pct,
            "exit_reason": f"STOP_{bucket}",
            "bucket": bucket,
        }

    # 规则 2: 止盈
    if max_return >= take_profit:
        tier1 = take_profit
        tier2 = final_return
        weighted = tier1 * 0.333 + tier2 * 0.667
        return {
            "actual_return": weighted * position_pct,
            "exit_reason": f"TAKE_PROFIT_{bucket}",
            "bucket": bucket,
        }

    # 默认: 持有到期
    return {
        "actual_return": final_return * position_pct,
        "exit_reason": f"HOLD_{bucket}",
        "bucket": bucket,
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

    # 分桶分布
    events["bucket"] = events["progress"].apply(progress_bucket)
    print(f"\n  进度分桶分布:")
    for b, n in events["bucket"].value_counts().sort_index().items():
        print(f"    {b}: {n} ({n/len(events)*100:.1f}%)")

    results = []
    for _, event in events.iterrows():
        position_pct = 0.12 if event["is_repeat"] else 0.08
        sim = simulate_v6(
            event["progress"],
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
            "progress": event["progress"],
            "bucket": sim["bucket"],
            "actual_return": sim["actual_return"],
            "exit_reason": sim["exit_reason"],
        })

    sim_df = pd.DataFrame(results)
    sim_df.to_csv(REPORTS_DIR / "shadow_trades_v6.csv", index=False, encoding="utf-8-sig")
    print(f"\n模拟交易明细: {REPORTS_DIR / 'shadow_trades_v6.csv'}")

    print("\n" + "=" * 60)
    print("实盘影子测试 v6 — 进度过滤")
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

    print(f"\n4. 各进度桶对比:")
    for bucket in ["极早期", "早期", "中期", "晚期"]:
        sub = sim_df[sim_df["bucket"] == bucket]
        if len(sub) == 0:
            continue
        wr = (sub["actual_return"] > 0).mean() * 100
        avg = sub["actual_return"].mean() * 100
        print(f"   {bucket} ({len(sub)} 笔):")
        print(f"     胜率: {wr:.1f}%")
        print(f"     平均: {avg:.2f}%")

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

    report_path = REPORTS_DIR / "SHADOW_TEST_REPORT_V6.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(
            f"""# 实盘影子测试 v6 — 进度过滤

**测试时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
**核心创新**: 按 progress 分桶用不同规则

## 0. v3 vs v6 对比

| 指标 | v3（15% 机械）| v6（进度过滤）|
|------|---------------|----------------|
| 胜率 | 37.3% | {win_rate:.1f}% |
| 平均收益 | +0.25% | {avg_return:.2f}% |
| 中位收益 | -1.20% | {median_return:.2f}% |
| 累计收益 | +134.39% | {total_return:.2f}% |
| 最大亏损 | -1.80% | {max_loss:.2f}% |

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

## 4. 各进度桶对比

| 进度桶 | 笔数 | 胜率 | 平均收益 |
|--------|------|------|----------|
"""
        )
        for bucket in ["极早期", "早期", "中期", "晚期"]:
            sub = sim_df[sim_df["bucket"] == bucket]
            if len(sub) == 0:
                continue
            wr = (sub["actual_return"] > 0).mean() * 100
            avg = sub["actual_return"].mean() * 100
            f.write(f"| {bucket} | {len(sub)} | {wr:.1f}% | {avg:.2f}% |\n")

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
| 严格执行规则（v6）| {shadow_return:.2f}% |
| **提升** | **{shadow_return - raw_return:+.2f}%** |
"""
        )
    print(f"\n报告已写入: {report_path}")
    return sim_df


if __name__ == "__main__":
    run_simulation()
