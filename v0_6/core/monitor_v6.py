"""
v6 实盘监控模块 — 掘金信号持仓监控

核心功能：
1. 评估每笔持仓的浮盈浮亏
2. 检查止损/止盈触发条件
3. 检查进度桶切换
4. 给出操作建议（持有/预警/止损/止盈）
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

import pandas as pd

from .config import DB_PATH, STOCK_DATA_DB
from .gold_signal_v6 import get_bucket, PROGRESS_BUCKETS
from .live_trade_store import list_open_positions


def get_industry_price(industry: str, end_date: str) -> pd.DataFrame:
    """获取某个行业的日线价格（中位数 close）"""
    end_dt = pd.to_datetime(end_date)
    start_dt = end_dt - pd.Timedelta(days=180)
    con = sqlite3.connect(str(STOCK_DATA_DB), uri=False)
    con_l = sqlite3.connect(str(DB_PATH), uri=False)
    sb = pd.read_sql_query("SELECT ts_code, industry FROM stock_basic", con_l)
    con_l.close()
    sd = pd.read_sql_query(
        f"""
        SELECT trade_date, ts_code, close FROM "daily_hfq"
        WHERE trade_date >= '{start_dt.strftime("%Y%m%d")}'
          AND trade_date <= '{end_dt.strftime("%Y%m%d")}'
        """,
        con,
        parse_dates=["trade_date"],
    )
    con.close()
    sd = sd.merge(sb, on="ts_code", how="left")
    sd = sd[sd["industry"] == industry]
    if sd.empty:
        return pd.DataFrame()
    daily = sd.groupby("trade_date")["close"].median().reset_index()
    daily = daily.sort_values("trade_date").reset_index(drop=True)
    return daily


def evaluate_position_v6(
    industry: str,
    entry_date: str,
    entry_price: float,
    today: str,
    progress: float,
    stop_threshold: float = None,
    take_profit_threshold: float = None,
    progress_bucket: str = None,
    reduced_1_3: bool = False,
) -> dict:
    """
    v6 规则评估单笔持仓

    返回：
    - current_price: 当前价
    - current_progress: 当前 progress
    - current_bucket: 当前进度桶
    - return_pct: 自入场以来涨跌幅
    - stop_price: 触发止损价
    - take_profit_price: 触发止盈价
    - distance_to_stop: 距止损的距离（正数）
    - distance_to_tp: 距止盈的距离（正数）
    - alerts: 触发的告警列表
    - action: 操作建议
    """
    # 默认值
    if stop_threshold is None:
        stop_threshold = get_bucket(progress)["stop"]
    if take_profit_threshold is None:
        take_profit_threshold = get_bucket(progress)["take_profit"]
    if progress_bucket is None:
        progress_bucket = get_bucket(progress)["name"]

    # 拉价格
    prices = get_industry_price(industry, today)
    if prices.empty:
        # Fallback: 找最近的有数据日
        for delta in range(1, 8):
            fallback_date = (pd.to_datetime(today) - pd.Timedelta(days=delta)).strftime("%Y-%m-%d")
            prices = get_industry_price(industry, fallback_date)
            if not prices.empty:
                today = fallback_date
                break
        if prices.empty:
            return {"industry": industry, "error": "no_price_data", "alerts": [], "action": "HOLD"}

    prices["date"] = pd.to_datetime(prices["trade_date"])
    prices = prices.sort_values("date").reset_index(drop=True)
    entry_dt = pd.to_datetime(entry_date)
    today_dt = pd.to_datetime(today)

    # 入场后的价格
    prices_after = prices[prices["date"] >= entry_dt].reset_index(drop=True)
    if prices_after.empty:
        prices_after = prices.copy()
        today_dt = prices["date"].iloc[-1]
        actual_today = today_dt.strftime("%Y-%m-%d")
    else:
        actual_today = today

    current_price = prices_after["close"].iloc[-1]
    return_pct = (current_price - entry_price) / entry_price

    # 计算 60 日最低
    prices_with_ma = prices.copy()
    prices_with_ma["low_60d"] = prices_with_ma["close"].rolling(60, min_periods=20).min()
    recent = prices_with_ma[prices_with_ma["date"] >= entry_dt]
    if not recent.empty and pd.notna(recent["low_60d"].iloc[-1]):
        current_60d_low = recent["low_60d"].iloc[-1]
        current_progress = (current_price - current_60d_low) / current_60d_low
    else:
        current_progress = progress
    current_bucket = get_bucket(current_progress)["name"]

    # 触发价格
    stop_price = entry_price * (1 + stop_threshold)
    take_profit_price = entry_price * (1 + take_profit_threshold)

    # 距离
    distance_to_stop = (current_price - stop_price) / current_price  # 正数=安全
    distance_to_tp = (take_profit_price - current_price) / current_price  # 正数=未到

    # 告警
    alerts = []
    action = "HOLD"
    priority = "GREEN"

    # 检查 1: 触发止损
    if return_pct <= stop_threshold:
        alerts.append({
            "type": "STOP_LOSS",
            "message": f"触发止损（{return_pct*100:.1f}% ≤ 阈值 {stop_threshold*100:.0f}%）",
            "action": "CLEAR",
            "priority": "RED",
        })
        action = "CLEAR"
        priority = "RED"
    # 检查 2: 触发止盈
    elif return_pct >= take_profit_threshold and not reduced_1_3:
        alerts.append({
            "type": "TAKE_PROFIT",
            "message": f"触发止盈（{return_pct*100:.1f}% ≥ 阈值 {take_profit_threshold*100:.0f}%）",
            "action": "REDUCE_1_3",
            "priority": "RED",
        })
        action = "REDUCE_1_3"
        priority = "RED"
    else:
        # 检查 3: 接近止盈（≥ 80% 距离）
        if return_pct >= take_profit_threshold * 0.8 and not reduced_1_3:
            alerts.append({
                "type": "APPROACHING_TP",
                "message": f"接近止盈（{return_pct*100:.1f}% / 目标 {take_profit_threshold*100:.0f}%）",
                "action": "WATCH",
                "priority": "YELLOW",
            })
            action = "WATCH"
            priority = "YELLOW"
        # 检查 4: 接近止损（≤ 80% 距离，即亏损超过止损的 80%）
        if return_pct <= stop_threshold * 0.8 and return_pct > stop_threshold:
            alerts.append({
                "type": "APPROACHING_STOP",
                "message": f"接近止损（{return_pct*100:.1f}% / 阈值 {stop_threshold*100:.0f}%）",
                "action": "WATCH",
                "priority": "YELLOW",
            })
            action = "WATCH"
            priority = "YELLOW"

    # 检查 5: 进度桶切换
    if current_bucket != progress_bucket:
        new_stop = get_bucket(current_progress)["stop"]
        new_tp = get_bucket(current_progress)["take_profit"]
        alerts.append({
            "type": "BUCKET_CHANGE",
            "message": f"进度桶从 {progress_bucket} 切到 {current_bucket}，新止损 = {new_stop*100:.0f}%，新止盈 = {new_tp*100:.0f}%",
            "action": "ADJUST",
            "priority": "YELLOW",
        })
        if action == "HOLD":
            action = "ADJUST"
            priority = "YELLOW"

    return {
        "industry": industry,
        "entry_date": entry_date,
        "today": actual_today,
        "entry_price": entry_price,
        "current_price": current_price,
        "current_progress": current_progress,
        "current_bucket": current_bucket,
        "progress_at_entry": progress,
        "progress_bucket_at_entry": progress_bucket,
        "return_pct": return_pct,
        "stop_threshold": stop_threshold,
        "take_profit_threshold": take_profit_threshold,
        "stop_price": stop_price,
        "take_profit_price": take_profit_price,
        "distance_to_stop": distance_to_stop,
        "distance_to_tp": distance_to_tp,
        "alerts": alerts,
        "action": action,
        "priority": priority,
        "reduced_1_3": reduced_1_3,
    }


def monitor_all_positions_v6(today: str = None) -> List[dict]:
    """监控所有未平仓持仓（v6 规则）"""
    from .live_trade_store import list_open_positions

    if today is None:
        today = datetime.now().strftime("%Y-%m-%d")

    positions = list_open_positions()
    results = []
    for pos in positions:
        # 按行业聚合（多笔同行业 → 取最早一笔的入场信息）
        if pos.get("action") not in ("BUY", "ADD"):
            continue
        result = evaluate_position_v6(
            industry=pos.get("target") or pos.get("industry", "?"),
            entry_date=pos["trade_date"],
            entry_price=pos["price"],
            today=today,
            progress=pos.get("progress_at_entry") or 0.05,
            stop_threshold=pos.get("stop_threshold"),
            take_profit_threshold=pos.get("take_profit_threshold"),
            progress_bucket=pos.get("progress_bucket"),
            reduced_1_3=bool(pos.get("reduced_1_3", 0)),
        )
        result["trade_id"] = pos["trade_id"]
        results.append(result)
    return results
