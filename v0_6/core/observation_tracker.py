"""轻量观察跟踪与案例账本

职责：
- 雷达连续天数和强观察标签（持久化为 radar_state.csv）
- signal_cases.csv 自动创建/更新/补齐
- 后续收益计算（行业等权复利）

不参与任何信号计算和交易动作。
"""

from __future__ import annotations

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

# ── 内存缓存（仅用于测试，真实运行以文件为准） ──────────

_radar_history: dict[str, dict] = {}


def _obs_dir() -> Path:
    from .config import DATA_DIR
    p = DATA_DIR.parent / "reports" / "observation"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _cases_path() -> Path:
    return _obs_dir() / "signal_cases.csv"


def _radar_state_path() -> Path:
    return _obs_dir() / "radar_state.csv"


def _case_id(industry: str, first_date: str) -> str:
    return f"{industry}__{first_date}"


# ── 雷达状态持久化 ─────────────────────────────────────

def _read_radar_state() -> pd.DataFrame:
    p = _radar_state_path()
    if not p.exists():
        return pd.DataFrame(columns=[
            "industry", "first_radar_date", "last_radar_date",
            "consecutive_radar_days", "last_missing_key", "last_missing_text",
            "last_breadth", "last_vol_ratio", "last_avg_ret20",
            "last_watch_level", "updated_at",
        ])
    try:
        return pd.read_csv(str(p), dtype=str)
    except Exception:
        return pd.DataFrame(columns=[
            "industry", "first_radar_date", "last_radar_date",
            "consecutive_radar_days", "last_missing_key", "last_missing_text",
            "last_breadth", "last_vol_ratio", "last_avg_ret20",
            "last_watch_level", "updated_at",
        ])


def _write_radar_state(df: pd.DataFrame) -> None:
    p = _radar_state_path()
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".csv", dir=str(p.parent))
    os.close(tmp_fd)
    try:
        df.to_csv(tmp_path, index=False, encoding="utf-8")
        os.replace(tmp_path, str(p))
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise


def _get_prev_trading_day(industry_daily: pd.DataFrame, date: str) -> str | None:
    """获取指定日期之前的最近一个交易日"""
    if industry_daily is None or industry_daily.empty:
        return None
    dates = sorted(industry_daily["trade_date"].unique().tolist())
    if date not in dates:
        return None
    idx = dates.index(date)
    if idx == 0:
        return None
    return dates[idx - 1]


# ── 雷达连续跟踪 ──────────────────────────────────────

def compute_radar_streak(
    radar_candidates: list[dict],
    signal_data_date: str,
    industry_daily: pd.DataFrame | None = None,
) -> list[dict]:
    """为每个雷达候选增加连续天数和强观察标签

    从 radar_state.csv 读取持久化状态，根据当天 radar_candidates 更新。
    radar_candidates 不变，返回增强后的副本。
    """
    today = signal_data_date
    prev_trading_day = _get_prev_trading_day(industry_daily, today) if industry_daily is not None else None

    # 读取持久化状态
    radar_df = _read_radar_state()
    state_map: dict[str, dict] = {}
    if not radar_df.empty:
        for _, row in radar_df.iterrows():
            state_map[row["industry"]] = {
                "first_radar_date": row.get("first_radar_date", today),
                "last_radar_date": row.get("last_radar_date", ""),
                "consecutive_radar_days": int(float(row.get("consecutive_radar_days", 0) or 0)),
                "last_missing_key": row.get("last_missing_key", ""),
                "last_missing_text": row.get("last_missing_text", ""),
                "last_breadth": float(row.get("last_breadth", 0) or 0),
                "last_vol_ratio": float(row.get("last_vol_ratio", 0) or 0),
                "last_avg_ret20": float(row.get("last_avg_ret20", 0) or 0),
                "last_watch_level": row.get("last_watch_level", "WATCH"),
            }

    enhanced = []
    new_states = []

    for cand in radar_candidates:
        ind = cand["industry"]
        prev = state_map.get(ind, {})

        # 判断是否连续：last_radar_date == 上一个交易日
        prev_date = prev.get("last_radar_date", "")
        is_consecutive = False
        if prev_trading_day and prev_date == prev_trading_day:
            is_consecutive = True

        if is_consecutive:
            streak = prev.get("consecutive_radar_days", 0) + 1
            first_date = prev.get("first_radar_date", today)
        else:
            streak = 1
            first_date = today

        # 判断强观察
        watch_level = "WATCH"
        missing = cand.get("missing", [])
        if streak >= RADAR_WATCH_CONFIG["strong_min_streak"]:
            watch_level = "STRONG_WATCH"
        else:
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
        prev_breadth = prev.get("last_breadth")
        distance_change = None
        if prev_breadth is not None and missing_key == "breadth":
            distance_change = round((cand.get("breadth", 0) - prev_breadth), 4)

        is_improving = False
        if distance_change is not None and distance_change > 0:
            is_improving = True

        # 记录新状态
        new_states.append({
            "industry": ind,
            "first_radar_date": first_date,
            "last_radar_date": today,
            "consecutive_radar_days": str(streak),
            "last_missing_key": missing_key,
            "last_missing_text": missing_text,
            "last_breadth": str(cand.get("breadth", "")),
            "last_vol_ratio": str(cand.get("vol_ratio", "")),
            "last_avg_ret20": str(cand.get("avg_ret20", "")),
            "last_watch_level": watch_level,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })

        # 同步内存缓存（向后兼容测试）
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

    # 不在当天雷达列表中的行业不自动清除，让其自然过期
    # （下次进入雷达时如果日期不连续会自动重置 streak）

    # 持久化写入
    new_df = pd.DataFrame(new_states)
    _write_radar_state(new_df)

    return enhanced


def reset_radar_for_triggered(industry: str) -> None:
    """正式信号触发后，清除该行业的雷达跟踪状态（持久化）"""
    radar_df = _read_radar_state()
    if not radar_df.empty and industry in radar_df["industry"].values:
        radar_df = radar_df[radar_df["industry"] != industry]
        _write_radar_state(radar_df)
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

    if "avg_ret1" in ind_data.columns:
        ret_col = "avg_ret1"
    elif "avg_ret5" in ind_data.columns:
        ret_col = "avg_ret5"
    else:
        return {f"forward_ret_{p}d": None for p in periods}

    divisor = 1.0 if ret_col == "avg_ret1" else 5.0

    results: dict[str, float | None] = {}
    for p in periods:
        sub = ind_data.head(p)
        if len(sub) < p:
            results[f"forward_ret_{p}d"] = None
        else:
            daily_rets = sub[ret_col].astype(float) / divisor
            cum = np.prod(1 + daily_rets) - 1
            results[f"forward_ret_{p}d"] = round(float(cum), 4)

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

    1. 识别新雷达周期 → 创建案例（基于持久化 first_radar_date）
    2. 正式触发 → 只处理当日信号，记录触发日期
    3. 补齐到期结果
    """
    existing = _read_cases()
    existing_ids = set(existing["case_id"].tolist()) if not existing.empty else set()

    # ── Fix 4: 只处理当日正式信号 ──
    sd_dt = pd.to_datetime(signal_data_date)
    if not today_signals.empty and "signal_date" in today_signals.columns:
        try:
            sig_dates = pd.to_datetime(today_signals["signal_date"])
        except Exception:
            sig_dates = None
        if sig_dates is not None:
            today_only = today_signals[sig_dates == sd_dt].copy()
        else:
            today_only = today_signals
    else:
        today_only = today_signals

    new_rows = []

    # 1) 雷达新周期（基于持久化 first_radar_date）
    for cand in radar_candidates:
        first_date = cand.get("first_radar_date", signal_data_date)
        cid = _case_id(cand["industry"], first_date)
        if cid in existing_ids:
            # 已存在，更新雷达最长连续天数
            streak = cand.get("consecutive_radar_days", 1)
            existing_mask = existing["case_id"] == cid
            if existing_mask.any():
                idx_val = existing[existing_mask].index[0]
                existing.at[idx_val, "radar_streak_max"] = str(max(
                    int(float(existing.at[idx_val, "radar_streak_max"] or 0)),
                    int(streak),
                ))
                existing.at[idx_val, "last_seen_date"] = signal_data_date
            continue

        new_rows.append({
            "case_id": cid,
            "industry": cand["industry"],
            "industry_group": "",
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

    # 2) 正式信号触发 → 只处理当日信号
    if not today_only.empty:
        for _, sig in today_only.iterrows():
            ind = sig.get("industry", "")
            if not existing.empty:
                ind_cases = existing[existing["industry"] == ind].copy()
                if not ind_cases.empty:
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
            ind = existing.at[idx, "industry"]
            first_date = existing.at[idx, "first_radar_date"]
            formal_date = existing.at[idx, "formal_signal_date"]
            status = existing.at[idx, "current_status"]

            calc_start = formal_date or first_date
            if not calc_start:
                continue

            fwd = _compute_forward_returns(industry_daily, ind, calc_start)

            for p in [5, 10, 20]:
                col = f"forward_ret_{p}d"
                val = existing.at[idx, col]
                new_val = fwd.get(col)
                if new_val is not None:
                    # 只在有实际值时才覆盖
                    existing.at[idx, col] = str(new_val)
                elif isinstance(val, str) and val == "":
                    # 保持空
                    pass

            existing.at[idx, "max_return_20d"] = fwd.get("max_return_20d", "")
            existing.at[idx, "max_drawdown_20d"] = fwd.get("max_drawdown_20d", "")

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
