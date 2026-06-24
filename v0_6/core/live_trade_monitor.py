"""
v0.6 实盘交易监控
5/10 日 min_return + 8% 机械止损 + 目标位监控
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

import pandas as pd

from .config import DB_PATH, RULES, STOCK_DATA_DB
from .live_trade_store import get_position_net, init_schema, list_open_positions


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
        SELECT ts_code, trade_date, close FROM "daily_hfq"
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


def evaluate_position(
    industry: str,
    entry_date: str,
    entry_price: float,
    today: str,
) -> dict:
    """
    评估一个持仓的监控状态

    返回：
    - current_price: 当前价
    - return_pct: 自入场以来涨跌幅
    - min_return_5d: 5 日内最大亏损
    - min_return_10d: 10 日内最大亏损
    - days_held: 持有天数
    - alerts: 触发的告警列表
    """
    prices = get_industry_price(industry, today)
    if prices.empty:
        # Fallback: 尝试找最近的有数据日（向前 7 日）
        for delta in range(1, 8):
            fallback_date = (pd.to_datetime(today) - pd.Timedelta(days=delta)).strftime("%Y-%m-%d")
            prices = get_industry_price(industry, fallback_date)
            if not prices.empty:
                today = fallback_date
                break
        if prices.empty:
            return {
                "industry": industry,
                "error": "no_price_data",
                "alerts": [],
            }
    prices["date"] = pd.to_datetime(prices["trade_date"])
    prices = prices.sort_values("date").reset_index(drop=True)

    entry_dt = pd.to_datetime(entry_date)
    today_dt = pd.to_datetime(today)
    prices_after = prices[prices["date"] >= entry_dt].reset_index(drop=True)
    if prices_after.empty:
        # 关键修复：entry 之后无数据（entry 在最近日期），用全部数据
        prices_after = prices.copy()

    current_price = prices_after["close"].iloc[-1]
    return_pct = (current_price - entry_price) / entry_price

    days_held = (today_dt - entry_dt).days
    cumret = prices_after["close"] / entry_price - 1.0
    cumret.index = prices_after["date"]

    min_return_5d = float(cumret.iloc[:5].min()) if len(cumret) >= 5 else float(cumret.min())
    min_return_10d = float(cumret.iloc[:10].min()) if len(cumret) >= 10 else float(cumret.min())
    max_return = float(cumret.max())

    alerts = []
    if days_held >= 5 and min_return_5d <= RULES["stop_loss"]["min_return_5d"]:
        alerts.append(
            {
                "rule": "5d_min_return",
                "threshold": RULES["stop_loss"]["min_return_5d"],
                "actual": round(min_return_5d, 4),
                "action": "REDUCE_HALF",
                "message": f"5 日内最大亏损 {min_return_5d*100:.1f}%，建议减半仓",
            }
        )
    if days_held >= 10 and min_return_10d <= RULES["stop_loss"]["min_return_10d"]:
        alerts.append(
            {
                "rule": "10d_min_return",
                "threshold": RULES["stop_loss"]["min_return_10d"],
                "actual": round(min_return_10d, 4),
                "action": "CLEAR",
                "message": f"10 日内最大亏损 {min_return_10d*100:.1f}%，建议清仓",
            }
        )
    if return_pct <= RULES["stop_loss"]["mechanical"]:
        alerts.append(
            {
                "rule": "mechanical_stop",
                "threshold": RULES["stop_loss"]["mechanical"],
                "actual": round(return_pct, 4),
                "action": "CLEAR",
                "message": f"机械止损触发，当前浮亏 {return_pct*100:.1f}%，建议清仓",
            }
        )
    if return_pct >= RULES["take_profit"]["tier1"]["pct"]:
        alerts.append(
            {
                "rule": "take_profit_tier1",
                "threshold": RULES["take_profit"]["tier1"]["pct"],
                "actual": round(return_pct, 4),
                "action": "REDUCE_1_3",
                "message": f"已达目标位 {return_pct*100:.1f}%，建议减 1/3 仓",
            }
        )
    if return_pct >= RULES["take_profit"]["tier2"]["pct"]:
        alerts.append(
            {
                "rule": "take_profit_tier2",
                "threshold": RULES["take_profit"]["tier2"]["pct"],
                "actual": round(return_pct, 4),
                "action": "REDUCE_1_2",
                "message": f"已达更高目标位 {return_pct*100:.1f}%，建议减 1/2 仓",
            }
        )

    return {
        "industry": industry,
        "entry_date": entry_date,
        "entry_price": entry_price,
        "current_price": round(float(current_price), 4),
        "return_pct": round(float(return_pct), 4),
        "min_return_5d": round(float(min_return_5d), 4) if pd.notna(min_return_5d) else None,
        "min_return_10d": round(float(min_return_10d), 4) if pd.notna(min_return_10d) else None,
        "max_return": round(float(max_return), 4),
        "days_held": days_held,
        "alerts": alerts,
    }


def monitor_all_positions(today: str) -> List[dict]:
    """监控所有未平仓持仓"""
    init_schema()
    positions = list_open_positions()
    if not positions:
        return []

    industry_first_buy = {}
    for t in positions:
        ind = t.get("target") or t.get("industry", "?")
        if ind not in industry_first_buy:
            industry_first_buy[ind] = {
                "entry_date": t["trade_date"],
                "entry_price": t["price"],
                "trade_ids": [t["trade_id"]],
                "actions": [t["action"]],
                "shares": t["shares"],
                "amount": t["amount"],
            }
        else:
            industry_first_buy[ind]["trade_ids"].append(t["trade_id"])
            industry_first_buy[ind]["actions"].append(t["action"])

    results = []
    for ind, info in industry_first_buy.items():
        eval_result = evaluate_position(
            industry=ind,
            entry_date=info["entry_date"],
            entry_price=info["entry_price"],
            today=today,
        )
        if "error" not in eval_result:
            eval_result["trade_count"] = len(info["trade_ids"])
            results.append(eval_result)
    return results
