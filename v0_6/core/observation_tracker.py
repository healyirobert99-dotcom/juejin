"""轻量观察跟踪与案例账本

职责：
- 雷达连续天数和强观察标签
- signal_cases.csv 自动创建/更新/补齐
- 后续收益计算（行业等权复利）

不参与任何信号计算和交易动作。
"""

from __future__ import annotations

import csv
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# ── 强观察阈值 ──────────────────────────────────────────

RADAR_WATCH_CONFIG = {
    "strong_min_streak": 2,        # 连续 2 天以上 → 强观察
    "breadth_near_gap": 0.03,      # 宽度距 35% 不超过 3 个百分点
    "vol_ratio_near_gap": 0.05,    # 量比距 1.00 不超过 0.05
    "ret20_near_gap": 0.01,        # 20 日收益距转正不超过 1 个百分点
}

# ── 最近 N 个交易日雷达状态（内存缓存） ────────────────

_radar_history: dict[str, dict] = {}


def _cases_path() -> Path:
    from .config import DATA_DIR
    p = DATA_DIR.parent / "reports" / "observation"
    p.mkdir(parents=True, exist_ok=True)
    return p / "signal_cases.csv"


def _case_id(industry: str, first_date: str) -> str:
    return f"{industry}__{first_date}"


# ── 雷达连续跟踪 ──────────────────────────────────────

def compute_radar_streak(
    radar_candidates: list[dict],
    signal_data_date: str,
) -> list[dict]:
    """为每个雷达候选增加连续天数和强观察标签

    radar_candidates 不变，返回增强后的副本。
    """
    enhanced = []
    today = signal_data_date

    for cand in radar_candidates:
        ind = cand["industry"]
        prev = _radar_history.get(ind, {})

        # 连续天数
        prev_date = prev.get("last_date", "")
        # 判断是否是连续交易日（简化：同一天或直接认为连续）
        if prev_date and prev.get("was_radar"):
            streak = prev.get("streak", 0) + 1
            first_date = prev.get("first_date", today)
        else:
            streak = 1
            first_date = today

        # 判断强观察
        watch_level = "WATCH"
        missing = cand.get("missing", [])
        if streak >= RADAR_WATCH_CONFIG["strong_min_streak"]:
            watch_level = "STRONG_WATCH"
        else:
            # 唯一缺失条件是否接近阈值
            if len(missing) == 1:
                m = missing[0]
                breadth = cand.get("breadth", 0)
                vol_ratio = cand.get("vol_ratio", 0)
                avg_ret20 = cand.get("avg_ret20", 0)
                if "宽度" in m and breadth >= 0.35 - RADAR_WATCH_CONFIG["breadth_near_gap"]:
                    watch_level = "STRONG_WATCH"
                elif "量比" in m and vol_ratio >= 1.00 - RADAR_WATCH_CONFIG["vol_ratio_near_gap"]:
                    watch_level = "STRONG_WATCH"
                elif "收益" in m and avg_ret20 >= -RADAR_WATCH_CONFIG["ret20_near_gap"]:
                    watch_level = "STRONG_WATCH"

        # 距离阈值信息
        missing_key = ""
        missing_text = ""
        distance_val = None
        distance_unit = ""
        if missing:
            missing_text = "；".join(missing)
            if "宽度" in missing_text:
                missing_key = "breadth"
                distance_val = round(max(0, 0.35 - cand.get("breadth", 0)), 4)
                distance_unit = "个百分点"
            elif "量比" in missing_text:
                missing_key = "vol_ratio"
                distance_val = round(max(0, 1.00 - cand.get("vol_ratio", 0)), 4)
                distance_unit = ""
            elif "收益" in missing_text:
                missing_key = "ret20"
                distance_val = round(abs(min(0, cand.get("avg_ret20", 0))), 4)
                distance_unit = "个百分点"

        # 较上一交易日变化
        prev_breadth = prev.get("breadth")
        distance_change = None
        if prev_breadth is not None and missing_key == "breadth":
            distance_change = round((cand.get("breadth", 0) - prev_breadth), 4)

        is_improving = False
        if distance_change is not None and distance_change > 0:
            is_improving = True

        # 更新历史
        _radar_history[ind] = {
            "last_date": today,
            "was_radar": True,
            "streak": streak,
            "first_date": first_date,
            "breadth": cand.get("breadth"),
            "vol_ratio": cand.get("vol_ratio"),
            "avg_ret20": cand.get("avg_ret20"),
        }

        enhanced.append({
            **cand,
            "watch_level": watch_level,
            "first_radar_date": first_date,
            "consecutive_radar_days": streak,
            "missing_key": missing_key,
            "missing_text": missing_text,
            "distance_to_threshold": distance_val,
            "distance_unit": distance_unit,
            "distance_change": distance_change,
            "is_improving": is_improving,
        })

    # 清理不在当天雷达列表中的行业
    today_industries = {c["industry"] for c in radar_candidates}
    for ind in list(_radar_history.keys()):
        if ind not in today_industries:
            _radar_history[ind]["was_radar"] = False

    return enhanced


def reset_radar_for_triggered(industry: str) -> None:
    """正式信号触发后，清除该行业的雷达跟踪状态"""
    _radar_history.pop(industry, None)


# ── 案例账本 ──────────────────────────────────────────

def _read_cases() -> pd.DataFrame:
    p = _cases_path()
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(str(p), dtype=str)
    except Exception:
        return pd.DataFrame()


def _write_cases_atomic(df: pd.DataFrame) -> None:
    """原子写入：先写临时文件再替换"""
    p = _cases_path()
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".csv", dir=str(p.parent))
    os.close(tmp_fd)
    try:
        # 将所有值转为字符串再写入
        write_df = df.copy()
        for col in write_df.columns:
            write_df[col] = write_df[col].astype(str)
        write_df.to_csv(tmp_path, index=False, encoding="utf-8")
        os.replace(tmp_path, str(p))
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise


def _compute_forward_returns(
    industry_daily: pd.DataFrame,
    industry: str,
    start_date: str,
    periods: list[int] = [5, 10, 20],
) -> dict[str, float | None]:
    """计算从 start_date 次日起 N 个交易日的复利收益"""
    ind_data = industry_daily[
        (industry_daily["industry"] == industry) &
        (industry_daily["trade_date"] > start_date)
    ].sort_values("trade_date")

    if ind_data.empty:
        return {f"forward_ret_{p}d": None for p in periods}

    # 使用 avg_ret1 或 avg_ret5 的日均值
    if "avg_ret1" in ind_data.columns:
        ret_col = "avg_ret1"
    elif "avg_ret5" in ind_data.columns:
        ret_col = "avg_ret5"
    else:
        return {f"forward_ret_{p}d": None for p in periods}

    # 如果使用 avg_ret5，需要除以 5 估算日均
    divisor = 1.0 if ret_col == "avg_ret1" else 5.0

    results: dict[str, float | None] = {}
    for p in periods:
        sub = ind_data.head(p)
        if len(sub) < p:
            results[f"forward_ret_{p}d"] = None
        else:
            daily_rets = sub[ret_col].astype(float) / divisor
            # 复利: ∏(1 + r) - 1
            cum = np.prod(1 + daily_rets) - 1
            results[f"forward_ret_{p}d"] = round(float(cum), 4)

        # 最大涨幅和最大回撤（同一窗口）
        max_ret = None
        max_dd = None
        if ret_col in ind_data.columns:
            sub_full = ind_data.head(p)
            if len(sub_full) >= p:
                daily_rets_full = sub_full[ret_col].astype(float) / divisor
                cum_series = (1 + daily_rets_full).cumprod()
                max_ret = round(float(cum_series.max() - 1), 4)
                max_dd = round(float((cum_series / cum_series.cummax() - 1).min()), 4)

        results[f"max_return_{p}d"] = max_ret
        results[f"max_drawdown_{p}d"] = max_dd

    return results


def update_signal_cases(
    radar_candidates: list[dict],
    today_signals: pd.DataFrame,
    industry_daily: pd.DataFrame,
    signal_data_date: str,
) -> pd.DataFrame:
    """每日更新案例账本

    1. 识别新雷达周期 → 创建案例
    2. 正式触发 → 记录触发日期
    3. 补齐到期结果
    """
    existing = _read_cases()
    existing_ids = set(existing["case_id"].tolist()) if not existing.empty else set()

    new_rows = []

    # 1) 雷达新周期
    for cand in radar_candidates:
        first_date = cand.get("first_radar_date", signal_data_date)
        cid = _case_id(cand["industry"], first_date)
        if cid in existing_ids:
            continue  # 已存在

        new_rows.append({
            "case_id": cid,
            "industry": cand["industry"],
            "industry_group": "",  # 后续由 industry_groups 填充
            "first_radar_date": first_date,
            "first_radar_watch_level": cand.get("watch_level", "WATCH"),
            "first_missing_key": cand.get("missing_key", ""),
            "first_missing_text": cand.get("missing_text", ""),
            "first_breadth": cand.get("breadth", ""),
            "first_vol_ratio": cand.get("vol_ratio", ""),
            "first_avg_ret20": cand.get("avg_ret20", ""),
            "formal_signal_date": "",
            "formal_signal_priority": "",
            "formal_triggered": "",
            "current_status": "RADAR_ACTIVE",
            "last_seen_date": signal_data_date,
            "radar_streak_max": str(cand.get("consecutive_radar_days", 1)),
            "forward_ret_5d": "",
            "forward_ret_10d": "",
            "forward_ret_20d": "",
            "max_return_20d": "",
            "max_drawdown_20d": "",
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })

    # 2) 正式信号触发（此前有雷达案例）
    if not today_signals.empty:
        for _, sig in today_signals.iterrows():
            ind = sig.get("industry", "")
            # 查找该行业最新的雷达案例
            if not existing.empty:
                ind_cases = existing[existing["industry"] == ind].copy()
                if not ind_cases.empty:
                    # 按 first_radar_date 排序，取最后一个
                    ind_cases = ind_cases.sort_values("first_radar_date", ascending=False)
                    for idx in ind_cases.index:
                        if existing.at[idx, "formal_triggered"] != "1":
                            existing.at[idx, "formal_signal_date"] = signal_data_date
                            existing.at[idx, "formal_signal_priority"] = sig.get("priority", "")
                            existing.at[idx, "formal_triggered"] = "1"
                            existing.at[idx, "current_status"] = "FORMAL_TRIGGERED"
                            existing.at[idx, "updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            break

    # 3) 更新已有案例状态
    if not existing.empty:
        for idx in existing.index:
            status = existing.at[idx, "current_status"]
            ind = existing.at[idx, "industry"]
            first_date = existing.at[idx, "first_radar_date"]
            formal_date = existing.at[idx, "formal_signal_date"]

            # 确定计算起点
            calc_start = formal_date or first_date
            if not calc_start:
                continue

            # 计算后续收益
            fwd = _compute_forward_returns(industry_daily, ind, calc_start)

            # 补齐到期结果
            for p in [5, 10, 20]:
                col = f"forward_ret_{p}d"
                if existing.at[idx, col] == "" or pd.isna(existing.at[idx, col]):
                    existing.at[idx, col] = fwd.get(col, "")

            existing.at[idx, "max_return_20d"] = fwd.get("max_return_20d", "")
            existing.at[idx, "max_drawdown_20d"] = fwd.get("max_drawdown_20d", "")

            # 更新状态
            if first_date:
                days_since = _count_trading_days_since(industry_daily, ind, first_date)
                if days_since is not None and days_since >= 20 and status not in ("FORMAL_TRIGGERED", "COMPLETED"):
                    existing.at[idx, "current_status"] = "COMPLETED"

            existing.at[idx, "last_seen_date"] = signal_data_date
            existing.at[idx, "updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 合并
    if new_rows:
        new_df = pd.DataFrame(new_rows)
        if existing.empty:
            existing = new_df
        else:
            existing = pd.concat([existing, new_df], ignore_index=True)

    # 填充 industry_group
    if not existing.empty and "industry_group" in existing.columns:
        from .industry_groups import get_group
        for idx in existing.index:
            if existing.at[idx, "industry_group"] == "":
                g = get_group(existing.at[idx, "industry"])
                if g:
                    existing.at[idx, "industry_group"] = g

    _write_cases_atomic(existing)
    return existing


def _count_trading_days_since(
    industry_daily: pd.DataFrame,
    industry: str,
    start_date: str,
) -> int | None:
    """计算从 start_date 起该行业经过了多少个交易日"""
    ind_data = industry_daily[industry_daily["industry"] == industry]
    if ind_data.empty:
        return None
    count = (ind_data["trade_date"] >= start_date).sum()
    return int(count)


def get_recent_cases(n: int = 3) -> pd.DataFrame:
    """获取最近 N 个案例"""
    existing = _read_cases()
    if existing.empty:
        return existing
    return existing.tail(n)
