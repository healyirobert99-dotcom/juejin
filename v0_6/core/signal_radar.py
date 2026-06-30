"""
信号雷达 · 接近触发

仅观察、不交易。

观察正式策略 4 因子中当前已满足 2/3 条件的行业。
严格使用与正式策略相同的数据和行业指标。
不与 signals / live_trades / ETF 等系统交互。
"""
from __future__ import annotations

import pandas as pd
import numpy as np

from .config import RULES


def build_signal_radar(
    industry_daily: pd.DataFrame,
    all_signals: pd.DataFrame,
    signal_data_date: str,
    max_items: int = 5,
) -> list[dict]:
    """构建信号雷达候选列表。

    参数
    ----
    industry_daily : pd.DataFrame
        compute_industry_daily_metrics 的输出（行业每日指标）。空时返回空列表。
    all_signals : pd.DataFrame
        detect_stabilizing_b + mark_repeat_priority 的输出（全部历史信号）。
    signal_data_date : str
        严格数据截止日期（YYYY-MM-DD）。
    max_items : int
        最多候选数，默认 5。

    返回
    ----
    list[dict] :
        每个元素含行业名称、触发进度、指标值、弱势背景、近5日宽度变化、尚缺条件。
        无候选时返回空列表。
    """
    if industry_daily.empty:
        return []
    cfg = RULES["signal"]
    breadth_threshold = cfg["breadth_threshold"]  # 0.35
    vol_ratio_threshold = cfg["vol_ratio_threshold"]  # 1.0
    min_stocks = cfg["min_stocks_per_industry"]  # 20
    warmup_days = RULES["signal"].get("warmup_days", 250)
    lookback_days = cfg["lookback_days"]  # 60
    cooldown_days = RULES["cooldown"]["industry_cooldown_days"]  # 60
    min_weakness_days = int(lookback_days * 0.5)  # 30

    sd_dt = pd.to_datetime(signal_data_date)

    # 筛选截止日当天及以前的数据
    daily = industry_daily[industry_daily["trade_date"] <= sd_dt].copy()
    if daily.empty:
        return []

    # 取得当日数据（该日期必须有数据）
    today_df = daily[daily["trade_date"] == sd_dt]
    if today_df.empty:
        # 如果 signal_data_date 本身没有行业数据（非交易日），
        # 取最近一个交易日
        last_date = daily["trade_date"].max()
        today_df = daily[daily["trade_date"] == last_date]
        if today_df.empty:
            return []
        # 更新 signal_data_date
        sd_dt = last_date

    # 取得正式信号今日触发行业（用于排除）
    signals_dt = pd.to_datetime(signal_data_date)
    # 确保 all_signals["signal_date"] 是 datetime
    if not all_signals.empty and "signal_date" in all_signals.columns:
        if not pd.api.types.is_datetime64_any_dtype(all_signals["signal_date"]):
            all_signals["signal_date"] = pd.to_datetime(all_signals["signal_date"])
        today_formal_signals = set(
            all_signals[all_signals["signal_date"] == sd_dt]["industry"].unique()
        )
    else:
        today_formal_signals = set()

    # 预计算每个行业的冷却期
    if not all_signals.empty:
        all_signals_sorted = all_signals.sort_values(["industry", "signal_date"])
        last_signal_date = (
            all_signals_sorted.groupby("industry")["signal_date"]
            .last()
            .to_dict()
        )
    else:
        last_signal_date = {}

    candidates = []

    for ind, g in daily.groupby("industry"):
        g = g.sort_values("trade_date").reset_index(drop=True)

        # --- 硬性资格 ---

        # 1. 行业股票数 >= 20（由 compute_industry_daily_metrics 已过滤，
        #    但这里仍显式检查当天值）
        ind_today = today_df[today_df["industry"] == ind]
        if ind_today.empty:
            continue
        row = ind_today.iloc[0]
        if row["n"] < min_stocks:
            continue

        # 2. 行业历史日记录 >= warmup_days + lookback_days (310)
        if len(g) < warmup_days + lookback_days:
            continue

        # 3. 不在 60 日信号冷却期
        if ind in last_signal_date:
            days_since = (sd_dt - last_signal_date[ind]).days
            if days_since < cooldown_days:
                continue

        # 4. 弱势背景：过去 60 个行业日宽度 < 35% 的天数 >= 30
        idx = g[g["trade_date"] == sd_dt].index
        if idx.empty:
            # 尝试取最近日期
            continue  # 不应发生
        i = idx[0]
        if i < lookback_days:
            continue
        past = g["breadth_ma20"].iloc[i - lookback_days : i]
        n_low = (past < breadth_threshold).sum()
        if n_low < min_weakness_days:
            continue

        # 5. 前一个行业交易日宽度 < 35%
        if i < 1:
            continue
        prev_breadth = g["breadth_ma20"].iloc[i - 1]
        if prev_breadth >= breadth_threshold:
            continue

        # --- 当前三个触发条件 ---
        curr_breadth = g["breadth_ma20"].iloc[i]
        curr_vol_ratio = g["avg_vol_ratio"].iloc[i]
        curr_ret20 = g["avg_ret20"].iloc[i]

        cond_breadth = bool(curr_breadth >= breadth_threshold)
        cond_vol = bool(curr_vol_ratio >= vol_ratio_threshold)
        cond_ret = bool(curr_ret20 > 0)
        conds_met = sum([cond_breadth, cond_vol, cond_ret])

        # 必须满足至少 2 个
        if conds_met < 2:
            continue

        # 排除已产生正式信号的行业
        if ind in today_formal_signals:
            continue

        # --- 计算近 5 日宽度变化 ---
        breadth_improvement = 0.0
        if i >= 5:
            breadth_5d_ago = g["breadth_ma20"].iloc[i - 4]  # 5 个行业日之前（含当天共5个点）
            breadth_improvement = curr_breadth - breadth_5d_ago
        elif i >= 1:
            breadth_improvement = curr_breadth - g["breadth_ma20"].iloc[0]
        # 实际上近 5 个行业日 = 当前日向前数 4 个交易日（含当前共 5 天）
        # 取最早的那个
        lookback_5_start = max(0, i - 4)
        breadth_5d_ago = g["breadth_ma20"].iloc[lookback_5_start]
        breadth_improvement = curr_breadth - breadth_5d_ago

        # --- 构建尚缺条件列表 ---
        missing = []
        if not cond_breadth:
            gap = (breadth_threshold - curr_breadth) * 100
            missing.append(f"宽度 {curr_breadth*100:.1f}%，距离 {breadth_threshold*100:.0f}% 还差 {gap:.1f} 个百分点")
        if not cond_vol:
            gap = vol_ratio_threshold - curr_vol_ratio
            missing.append(f"量比 {curr_vol_ratio:.2f}，距离 {vol_ratio_threshold:.1f} 还差 {gap:.2f}")
        if not cond_ret:
            missing.append(f"20 日收益仍为 {curr_ret20*100:+.1f}%")

        candidates.append({
            "industry": ind,
            "conditions_met": conds_met,
            "breadth": float(curr_breadth),
            "vol_ratio": float(curr_vol_ratio),
            "avg_ret20": float(curr_ret20),
            "n_low_in_past_60d": int(n_low),
            "breadth_improvement_5d": float(breadth_improvement),
            "missing": missing,
            "cond_breadth": cond_breadth,
            "cond_vol": cond_vol,
            "cond_ret": cond_ret,
        })

    # --- 排序 ---
    # 1. 条件通过数量，降序
    # 2. 近 5 日宽度改善幅度，降序
    # 3. 当前宽度，降序
    # 4. 行业名称，升序（稳定性）
    candidates.sort(
        key=lambda c: (-c["conditions_met"], -c["breadth_improvement_5d"], -c["breadth"], c["industry"])
    )

    # 截断到 max_items
    return candidates[:max_items]
