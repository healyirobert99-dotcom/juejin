"""
v0.6 B 方案信号检测
从 scripts/zhongqianqi/signals_b.py 复用核心逻辑
已验证：16 年 4 cutoff OOS 穿越牛熊
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from . import config

# 运行时引用（不早绑），回放引擎可在运行时切换 DB_PATH/STOCK_DATA_DB
def _stock_db():
    return config.STOCK_DATA_DB

def _proj_db():
    return config.DB_PATH

def _rules():
    return config.RULES


def load_raw_daily(start: str = "2010-01-01", end: str = "2026-06-30") -> pd.DataFrame:
    """从 stock_data.db 加载原始日线（16 年全量）"""
    con = sqlite3.connect(str(_stock_db()), uri=False)
    con_l = sqlite3.connect(str(_proj_db()), uri=False)
    sb = pd.read_sql_query("SELECT symbol, ts_code, industry FROM stock_basic", con_l)
    con_l.close()

    sd = pd.read_sql_query(
        f'SELECT ts_code, trade_date, close, vol FROM "daily_hfq" '
        f"WHERE trade_date >= '{start.replace('-', '')}' AND trade_date <= '{end.replace('-', '')}'",
        con,
        parse_dates=["trade_date"],
    )
    con.close()
    sd = sd.merge(sb[["ts_code", "industry"]], on="ts_code", how="left")
    sd = sd.dropna(subset=["industry"])
    sd = sd.sort_values(["ts_code", "trade_date"])
    return sd


def compute_per_stock_indicators(sd: pd.DataFrame) -> pd.DataFrame:
    """计算每只个股的核心指标"""
    g = sd.groupby("ts_code", sort=False)
    sd["ma20"] = g["close"].transform(lambda x: x.rolling(20, min_periods=10).mean())
    sd["ma60"] = g["close"].transform(lambda x: x.rolling(60, min_periods=30).mean())
    sd["above_ma20"] = sd["close"] >= sd["ma20"]
    sd["above_ma60"] = sd["close"] >= sd["ma60"]
    sd["vol_ma5"] = g["vol"].transform(lambda x: x.rolling(5, min_periods=3).mean())
    sd["vol_ma60"] = g["vol"].transform(lambda x: x.rolling(60, min_periods=30).mean())
    sd["vol_ratio_5_60"] = sd["vol_ma5"] / sd["vol_ma60"]

    # N 日收益率（用于退潮和主线分析）
    for nd in [5, 10, 20, 60]:
        sd[f"ret_{nd}d"] = g["close"].transform(lambda x: x.pct_change(nd))

    return sd


def compute_industry_daily_metrics(sd: pd.DataFrame) -> pd.DataFrame:
    """按 (date, industry) 聚合行业级指标"""
    daily = sd.groupby(["trade_date", "industry"]).agg(
        n=("close", "count"),
        breadth_ma20=("above_ma20", "mean"),
        breadth_ma60=("above_ma60", "mean"),
        avg_vol_ratio=("vol_ratio_5_60", "mean"),
        avg_ret5=("ret_5d", "mean"),
        avg_ret10=("ret_10d", "mean"),
        avg_ret20=("ret_20d", "mean"),
        avg_ret60=("ret_60d", "mean"),
        median_ret5=("ret_5d", "median"),
        median_ret10=("ret_10d", "median"),
        median_ret20=("ret_20d", "median"),
        median_ret60=("ret_60d", "median"),
    ).reset_index()
    daily = daily[daily["n"] >= _rules()["signal"]["min_stocks_per_industry"]].copy()
    return daily


def detect_stabilizing_b(
    daily: pd.DataFrame,
    lookback_days: int = 60,
    cooldown_days: int = 60,
    warmup_days: int = 250,
) -> pd.DataFrame:
    """
    B 方案 STABILIZING 信号检测（4 因子）

    触发条件（与 AND）：
    1. 行业宽度 ≥ 35%
    2. 5日均量 / 60日均量 ≥ 1.0
    3. 行业平均 20 日涨幅 > 0
    4. 过去 60 日内多数（≥30 日）宽度低于 35%
    5. 同一行业 60 日冷却
    6. 行业股票数 ≥ 20（小行业过滤）
    """
    rows = []
    breadth_threshold = _rules()["signal"]["breadth_threshold"]
    vol_ratio_threshold = _rules()["signal"]["vol_ratio_threshold"]
    for ind, g in daily.groupby("industry"):
        g = g.sort_values("trade_date").reset_index(drop=True)
        if len(g) < warmup_days + lookback_days:
            continue
        last_trigger = -999
        for i in range(lookback_days, len(g)):
            if i - last_trigger < cooldown_days:
                continue
            if g["breadth_ma20"].iloc[i] < breadth_threshold:
                continue
            if g["avg_vol_ratio"].iloc[i] < vol_ratio_threshold:
                continue
            if g["avg_ret20"].iloc[i] <= 0:
                continue
            past = g["breadth_ma20"].iloc[i - lookback_days : i]
            n_low = (past < breadth_threshold).sum()
            if n_low < lookback_days * 0.5:
                continue
            if g["breadth_ma20"].iloc[i - 1] >= breadth_threshold:
                continue
            rows.append(
                {
                    "industry": ind,
                    "signal_date": g["trade_date"].iloc[i],
                    "signal_type": "STABILIZING_B",
                    "breadth_at_signal": g["breadth_ma20"].iloc[i],
                    "vol_ratio_at_signal": g["avg_vol_ratio"].iloc[i],
                    "avg_ret20_at_signal": g["avg_ret20"].iloc[i],
                    "n_low_in_past_60d": int(n_low),
                    "n_stocks": int(g["n"].iloc[i]),
                }
            )
            last_trigger = i
    if not rows:
        return pd.DataFrame(columns=[
            "industry", "signal_date", "signal_type", "breadth_at_signal",
            "vol_ratio_at_signal", "avg_ret20_at_signal", "n_low_in_past_60d", "n_stocks",
        ])
    return pd.DataFrame(rows)


def mark_repeat_priority(signals: pd.DataFrame, lookback_days: int = 120) -> pd.DataFrame:
    """
    标记信号是否为"重复触发"
    120 日内同行业再次出现 → REPEAT
    否则 → FIRST
    """
    signals = signals.sort_values(["industry", "signal_date"]).reset_index(drop=True)
    signals["prev_signal_date"] = signals.groupby("industry")["signal_date"].shift(1)
    signals["days_since_prev"] = (signals["signal_date"] - signals["prev_signal_date"]).dt.days
    signals["priority"] = signals["days_since_prev"].apply(
        lambda x: "REPEAT" if pd.notna(x) and x <= lookback_days else "FIRST"
    )
    return signals
