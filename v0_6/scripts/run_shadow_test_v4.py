"""
实盘影子测试 v4 — 简化版（均线止损）

止损规则：跌破 20MA 后 3 日不重新站回 → 清仓
止盈规则：30% 单一止盈（与 v3 一致）
仓位规则：首次 8% / 重复 12%（与 v3 一致）

简化版：每天统计所有 B 方案事件"如果按 20MA 规则止损，结果如何"
- 用 stock_data.db 拉每个事件入场后 250 日的日线
- 计算 20MA
- 检测"跌破 20MA 后 3 日不站回"的时点
- 在该时点退出（按当日收盘价）

工时优化：
- 不对每只股票单独算行业指数（太慢）
- 用每只股票的中位数 close 作为"行业代理"——用该行业内所有股票中位数
- 实际：用 daily_hfq 拉行业内所有股票 + groupby trade_date 取中位数 + 计算 20MA
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

V0_6_ROOT = Path(__file__).resolve().parents[2]
PROJECT_V0_5 = Path("D:/zhuxian-catch_2026-06-24")
B_EVENTS_CSV = PROJECT_V0_5 / "reports" / "zhongqianqi" / "B_EVENTS.csv"
DB_PATH = V0_6_ROOT / "data" / "a_stock_selector.sqlite3"
STOCK_DATA_DB = V0_6_ROOT / "stock_data" / "stock_data.db"
RULES_PATH = V0_6_ROOT / "v0_6" / "data" / "rules.json"
REPORTS_DIR = V0_6_ROOT / "reports" / "shadow"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

with open(RULES_PATH, encoding="utf-8") as f:
    RULES = json.load(f)


def load_industry_members(industry: str) -> list:
    """从 daily-lite 库加载某个行业的所有股票代码"""
    con = sqlite3.connect(str(DB_PATH), uri=False)
    rows = con.execute(
        "SELECT DISTINCT ts_code FROM stock_basic WHERE industry = ?",
        (industry,),
    ).fetchall()
    con.close()
    return [r[0] for r in rows]


def get_industry_daily(industry: str, start_date: str, end_date: str) -> pd.DataFrame:
    """
    加载某行业在 [start_date, end_date] 的日线（行业内所有股票中位数 close）
    返回: trade_date / close
    """
    members = load_industry_members(industry)
    if not members:
        return pd.DataFrame()

    placeholders = ",".join("?" * len(members))
    start_dt = pd.to_datetime(start_date).strftime("%Y%m%d")
    end_dt = pd.to_datetime(end_date).strftime("%Y%m%d")

    con = sqlite3.connect(str(STOCK_DATA_DB), uri=False)
    sql = f"""
        SELECT trade_date, close FROM "daily_hfq"
        WHERE ts_code IN ({placeholders})
          AND trade_date >= ? AND trade_date <= ?
    """
    df = pd.read_sql_query(
        sql, con, params=tuple(members) + (start_dt, end_dt), parse_dates=["trade_date"]
    )
    con.close()

    if df.empty:
        return df

    # 行业内每日中位数（按 trade_date 分组）
    daily = df.groupby("trade_date")["close"].median().reset_index()
    daily = daily.sort_values("trade_date").reset_index(drop=True)
    return daily


def simulate_v4(entry_date: str, industry: str, position_pct: float) -> dict:
    """
    模拟单笔交易（均线止损 + 30% 止盈）

    返回: actual_return, exit_reason, exit_day
    """
    # 加载入场后 250 个交易日的价格
    entry_dt = pd.to_datetime(entry_date)
    start_dt = entry_dt - pd.Timedelta(days=30)  # 预热 30 日
    end_dt = entry_dt + pd.Timedelta(days=400)  # 多预留 150 日 buffer

    prices = get_industry_daily(
        industry,
        start_dt.strftime("%Y-%m-%d"),
        end_dt.strftime("%Y-%m-%d"),
    )
    if prices.empty:
        return {"actual_return": 0, "exit_reason": "NO_DATA", "exit_day": 0}

    prices["date"] = pd.to_datetime(prices["trade_date"])
    prices = prices.sort_values("date").reset_index(drop=True)
    prices["MA20"] = prices["close"].rolling(20).mean()

    # 找入场日
    prices_after = prices[prices["date"] >= entry_dt].reset_index(drop=True)
    if prices_after.empty:
        return {"actual_return": 0, "exit_reason": "NO_DATA_AFTER_ENTRY", "exit_day": 0}

    if len(prices_after) < 2:
        return {"actual_return": 0, "exit_reason": "INSUFFICIENT_DATA", "exit_day": 0}

    entry_close = prices_after["close"].iloc[0]

    # 规则 1: 30% 单一止盈
    take_profit_threshold = 0.30
    take_profit_exit_day = None
    take_profit_close = None
    for i, row in prices_after.iterrows():
        if i == 0:
            continue
        if row["close"] >= entry_close * (1 + take_profit_threshold):
            take_profit_exit_day = i
            take_profit_close = row["close"]
            break

    # 规则 2: 跌破 20MA 后 3 日不站回
    ma_break_exit_day = None
    ma_break_close = None
    if "MA20" in prices_after.columns:
        for i, row in prices_after.iterrows():
            if i == 0 or pd.isna(row["MA20"]):
                continue
            # 跌破 20MA
            if row["close"] < row["MA20"]:
                # 看后续 3 个交易日
                if i + 3 < len(prices_after):
                    next_3 = prices_after.iloc[i + 1 : i + 4]
                    # 3 个交易日都收盘在 20MA 之下
                    if all(
                        next_3_row["close"] < next_3_row["MA20"]
                        for _, next_3_row in next_3.iterrows()
                    ):
                        ma_break_exit_day = i + 3  # 第 3 日收盘
                        ma_break_close = prices_after.iloc[i + 3]["close"]
                        break

    # 决定最终退出
    if take_profit_exit_day is not None and ma_break_exit_day is not None:
        # 两个规则都触发，看哪个先
        if take_profit_exit_day <= ma_break_exit_day:
            exit_reason = "TAKE_PROFIT_30"
            exit_close = take_profit_close
            exit_day = take_profit_exit_day
        else:
            exit_reason = "MA20_BREAK_3D"
            exit_close = ma_break_close
            exit_day = ma_break_exit_day
    elif take_profit_exit_day is not None:
        exit_reason = "TAKE_PROFIT_30"
        exit_close = take_profit_close
        exit_day = take_profit_exit_day
    elif ma_break_exit_day is not None:
        exit_reason = "MA20_BREAK_3D"
        exit_close = ma_break_close
        exit_day = ma_break_exit_day
    else:
        # 持有到期
        exit_reason = "HOLD_TO_MATURITY"
        exit_close = prices_after["close"].iloc[-1]
        exit_day = len(prices_after) - 1

    actual_return = (exit_close - entry_close) / entry_close * position_pct
    return {
        "actual_return": actual_return,
        "exit_reason": exit_reason,
        "exit_day": exit_day,
    }


def run_simulation():
    """跑 v4 简化版影子测试"""
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

    # 模拟每笔交易
    results = []
    total = len(events)
    for idx, event in events.iterrows():
        if idx % 50 == 0:
            print(f"  进度: {idx}/{total} ({idx/total*100:.0f}%)")
        position_pct = 0.12 if event["is_repeat"] else 0.08
        sim = simulate_v4(event["signal_date"], event["entity"], position_pct)
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
                "exit_day": sim["exit_day"],
            }
        )

    sim_df = pd.DataFrame(results)
    sim_df.to_csv(REPORTS_DIR / "shadow_trades_v4.csv", index=False, encoding="utf-8-sig")
    print(f"\n模拟交易明细: {REPORTS_DIR / 'shadow_trades_v4.csv'}")

    print("\n" + "=" * 60)
    print("实盘影子测试 v4 — 均线止损 + 30% 止盈")
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

    report_path = REPORTS_DIR / "SHADOW_TEST_REPORT_V4.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(
            f"""# 实盘影子测试 v4 — 均线止损 + 30% 止盈

**测试时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
**核心创新**: 用"价格行为"（跌破 20MA 3 日不站回）替代"亏损比例"（15% 机械止损）

## 0. v3 vs v4 对比

| 指标 | v3（15% 机械止损）| v4（20MA 行为止损）|
|------|--------------------|---------------------|
| 胜率 | 37.3% | {win_rate:.1f}% |
| 平均收益 | +0.25% | {avg_return:.2f}% |
| 中位收益 | -1.20% | {median_return:.2f}% |
| 累计收益 | +134.39% | {total_return:.2f}% |
| 最大亏损 | -1.80% | {max_loss:.2f}% |
| 规则效果 | -5.85% | {shadow_return - raw_return:+.2f}% |

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
| 严格执行规则（v4）| {shadow_return:.2f}% |
| **提升** | **{shadow_return - raw_return:+.2f}%** |
"""
        )
    print(f"\n报告已写入: {report_path}")
    return sim_df


if __name__ == "__main__":
    run_simulation()
