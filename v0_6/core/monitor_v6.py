"""
v6 实盘监控模块 — 掘金信号持仓监控

核心功能：
1. 评估每笔持仓的浮盈浮亏
2. 检查止损/止盈触发条件
3. 检查进度桶切换
4. 给出操作建议（持有/预警/止损/止盈）

价格路由原则：
  target_type=ETF  → 读取 etf_daily 表（ETF 自身收盘价）
  target_type=STOCK → 读取 daily_hfq 表（个股后复权价）
  target_type=INDUSTRY → 不计算真实盈亏（行业不可执行）
  target_type=未知  → 返回明确错误
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


def get_target_price(target: str, target_type: str, end_date: str) -> pd.DataFrame:
    """按标的类型获取价格序列

    ETF   → 查 etf_daily 表（ETF 自身收盘价）
    STOCK → 查 daily_hfq 表（个股后复权价）
    INDUSTRY → 不返回价格（行业不可执行）

    返回 DataFrame(columns=[trade_date, close])，按日期升序。
    查不到数据时返回空 DataFrame。
    """
    if target_type == "INDUSTRY":
        return pd.DataFrame()

    if target_type not in ("ETF", "STOCK"):
        return pd.DataFrame()

    end_dt = pd.to_datetime(end_date)
    start_dt = end_dt - pd.Timedelta(days=180)
    con = sqlite3.connect(str(STOCK_DATA_DB), uri=False)

    # 构造候选代码列表（兼容 6 位无后缀、带 .SH/.SZ 等格式）
    candidates = [target]
    if "." not in target:
        if target.startswith(("5", "1")):
            candidates.insert(0, target + ".SH")
            candidates.append(target + ".SZ")
    elif not target.endswith((".SH", ".SZ")):
        candidates = [target]

    rows = None
    for c in candidates:
        if target_type == "ETF":
            rows = con.execute(
                "SELECT trade_date, close FROM etf_daily "
                "WHERE ts_code = ? AND trade_date >= ? AND trade_date <= ? "
                "ORDER BY trade_date ASC",
                (c, start_dt.strftime("%Y%m%d"), end_dt.strftime("%Y%m%d")),
            ).fetchall()
        else:  # STOCK
            rows = con.execute(
                'SELECT trade_date, close FROM "daily_hfq" '
                "WHERE ts_code = ? AND trade_date >= ? AND trade_date <= ? "
                "ORDER BY trade_date ASC",
                (c, start_dt.strftime("%Y%m%d"), end_dt.strftime("%Y%m%d")),
            ).fetchall()
        if rows:
            break
    con.close()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=["trade_date", "close"])
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.sort_values("trade_date").reset_index(drop=True)
    return df


def evaluate_position_v6(
    target: str,
    target_type: str,
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

    参数：
      target: 标的代码（ETF 代码如 "512800"，或个股 ts_code）
      target_type: 标的类型（"ETF" / "STOCK" / "INDUSTRY"）

    返回：
      - target: 标的代码
      - target_type: 标的类型
      - current_price: 当前价（INDUSTRY 返回 None）
      - error: 错误信息（无数据时）
      - action: 操作建议
    """
    # 默认值
    if stop_threshold is None:
        stop_threshold = get_bucket(progress)["stop"]
    if take_profit_threshold is None:
        take_profit_threshold = get_bucket(progress)["take_profit"]
    if progress_bucket is None:
        progress_bucket = get_bucket(progress)["name"]

    # — 行业不可执行 —
    if target_type == "INDUSTRY":
        return {
            "target": target,
            "target_type": target_type,
            "entry_date": entry_date,
            "error": "industry_not_executable",
            "action": "ERROR",
            "priority": "YELLOW",
            "alerts": [{
                "type": "INDUSTRY_NOT_EXECUTABLE",
                "message": f"行业 {target} 不可作为交易标的，请通过 ETF 执行",
                "action": "ERROR",
                "priority": "YELLOW",
            }],
        }

    # — 未知类型 —
    if target_type not in ("ETF", "STOCK"):
        return {
            "target": target,
            "target_type": target_type or "unknown",
            "entry_date": entry_date,
            "error": "unknown_target_type",
            "action": "ERROR",
            "priority": "YELLOW",
            "alerts": [{
                "type": "UNKNOWN_TARGET_TYPE",
                "message": f"未知标的类型 '{target_type}'，需要人工确认历史持仓类型",
                "action": "ERROR",
                "priority": "YELLOW",
            }],
        }

    # — ETF / STOCK：拉标的自身价格 —
    prices = get_target_price(target, target_type, today)
    if prices.empty:
        # Fallback: 找最近的有数据日
        for delta in range(1, 8):
            fallback_date = (pd.to_datetime(today) - pd.Timedelta(days=delta)).strftime("%Y-%m-%d")
            prices = get_target_price(target, target_type, fallback_date)
            if not prices.empty:
                today = fallback_date
                break
        if prices.empty:
            return {
                "target": target,
                "target_type": target_type,
                "entry_date": entry_date,
                "error": "no_price_data",
                "alerts": [],
                "action": "HOLD",
            }

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
        # 检查 3: 接近止盈
        if return_pct >= take_profit_threshold * 0.8 and not reduced_1_3:
            alerts.append({
                "type": "APPROACHING_TP",
                "message": f"接近止盈（{return_pct*100:.1f}% / 目标 {take_profit_threshold*100:.0f}%）",
                "action": "WATCH",
                "priority": "YELLOW",
            })
            action = "WATCH"
            priority = "YELLOW"
        # 检查 4: 接近止损
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
        "target": target,
        "target_type": target_type,
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
    if today is None:
        today = datetime.now().strftime("%Y-%m-%d")

    positions = list_open_positions()
    results = []
    for pos in positions:
        if pos.get("action") not in ("BUY", "ADD"):
            continue

        target = pos.get("target") or pos.get("industry", "?")
        target_type = pos.get("target_type")  # 保留 None/空，不猜测成 INDUSTRY

        result = evaluate_position_v6(
            target=target,
            target_type=target_type,
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
