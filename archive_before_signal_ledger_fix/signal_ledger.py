"""信号生命周期账本（掘金信号日报 v1.2 新增）

职责（仅记录能力，不参与任何信号计算与交易动作）：
- 为每个进入雷达或正式触发的行业生成唯一 signal_id
- 维护生命周期状态：RADAR / TRIGGERED / HOLD / CLOSED
- 记录信号产生后的实际表现：signal_date + D+1/D+5/D+20 收益（宽度）
- 记录行业 ETF 可执行性（trade_available）

signal_id 格式：YYYYMMDD_行业名_序号  （如 20260711_医疗保健_001）
状态仅使用：RADAR / TRIGGERED / HOLD / CLOSED（不新增其他状态）

持久化：reports/observation/signal_ledger.csv
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

# 生命周期状态（唯一允许集合）
STATUS_RADAR = "RADAR"
STATUS_TRIGGERED = "TRIGGERED"
STATUS_HOLD = "HOLD"
STATUS_CLOSED = "CLOSED"
ACTIVE_STATUSES = {STATUS_RADAR, STATUS_TRIGGERED, STATUS_HOLD}

LEDGER_COLS = [
    "signal_id", "industry", "first_observation_date", "signal_date",
    "current_status", "current_phase",
    "d1_ret", "d1_breadth", "d5_ret", "d5_breadth", "d20_ret", "d20_breadth",
    "trade_available", "etf_code", "close_reason", "updated_at",
]


def _ledger_path() -> Path:
    from .config import DATA_DIR
    p = DATA_DIR.parent / "reports" / "observation"
    p.mkdir(parents=True, exist_ok=True)
    return p / "signal_ledger.csv"


def _read_ledger() -> pd.DataFrame:
    p = _ledger_path()
    if not p.exists():
        return pd.DataFrame(columns=LEDGER_COLS)
    try:
        df = pd.read_csv(str(p), dtype=str)
        for c in LEDGER_COLS:
            if c not in df.columns:
                df[c] = ""
        return df[LEDGER_COLS]
    except Exception:
        return pd.DataFrame(columns=LEDGER_COLS)


def _write_ledger_atomic(df: pd.DataFrame) -> None:
    p = _ledger_path()
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".csv", dir=str(p.parent))
    os.close(tmp_fd)
    try:
        write_df = df.copy()
        for col in write_df.columns:
            write_df[col] = write_df[col].astype(str)
        write_df.to_csv(tmp_path, index=False, encoding="utf-8")
        os.replace(tmp_path, str(p))
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise


def _next_seq(ledger_df: pd.DataFrame, date_compact: str) -> int:
    """当日已用的最大序号 + 1（幂等：重跑同日期不会跳号）"""
    if ledger_df.empty:
        return 1
    prefix = date_compact + "_"
    max_seq = 0
    for sid in ledger_df["signal_id"].tolist():
        if isinstance(sid, str) and sid.startswith(prefix):
            parts = sid.split("_")
            if len(parts) >= 3:
                try:
                    max_seq = max(max_seq, int(parts[-1]))
                except (ValueError, TypeError):
                    pass
    return max_seq + 1


def _make_signal_id(date_compact: str, industry: str, seq: int) -> str:
    return f"{date_compact}_{industry}_{seq:03d}"


def _trading_day_offset(industry_daily: pd.DataFrame, industry: str, start_date: str, offset: int):
    """返回行业在 start_date 之后第 offset 个交易日的整行（含指标），不足返回 None"""
    if industry_daily is None or industry_daily.empty:
        return None
    sub = industry_daily[
        (industry_daily["industry"] == industry) &
        (industry_daily["trade_date"] > str(start_date))
    ].sort_values("trade_date")
    if len(sub) < offset:
        return None
    return sub.iloc[offset - 1]


def _count_trading_days_since(industry_daily: pd.DataFrame, industry: str, start_date: str,
                              as_of: str) -> int | None:
    if industry_daily is None or industry_daily.empty:
        return None
    sub = industry_daily[
        (industry_daily["industry"] == industry) &
        (industry_daily["trade_date"] >= str(start_date)) &
        (industry_daily["trade_date"] <= str(as_of))
    ]
    return int(len(sub))


def _compute_signal_outcome(industry_daily: pd.DataFrame, industry: str, start_date: str) -> dict:
    """计算 signal_date 之后 D+1/D+5/D+20 的复利收益与 MA20 宽度。

    返回 {1: {ret, breadth}, 5: {...}, 20: {...}}，数据不足时 ret/breadth 为 None。
    """
    out: dict[int, dict] = {}
    for p in (1, 5, 20):
        row = _trading_day_offset(industry_daily, industry, start_date, p)
        if row is None:
            out[p] = {"ret": None, "breadth": None}
            continue
        # 区间日收益复利
        sub = industry_daily[
            (industry_daily["industry"] == industry) &
            (industry_daily["trade_date"] > str(start_date)) &
            (industry_daily["trade_date"] <= str(row["trade_date"]))
        ].sort_values("trade_date")
        if "avg_ret1" in sub.columns and len(sub) >= p:
            daily = sub["avg_ret1"].astype(float)
            cum = float(np.prod(1 + daily) - 1)
            ret = round(cum, 4)
        else:
            ret = None
        breadth = None
        if "breadth_ma20" in row and row.get("breadth_ma20") not in (None, "", "nan"):
            try:
                breadth = round(float(row["breadth_ma20"]), 4)
            except (ValueError, TypeError):
                breadth = None
        out[p] = {"ret": ret, "breadth": breadth}
    return out


def _phase_label(days_since: int | None) -> str:
    if days_since is None:
        return "D0"
    if days_since < 5:
        return "D0"
    if days_since < 20:
        return "D+5"
    return "D+20"


def _trade_available(industry: str) -> tuple[str, str]:
    """返回 (trade_available, etf_code)。trade_available ∈ {YES, NO}"""
    try:
        from .etf_matcher import match_industry_to_etfs
        r = match_industry_to_etfs(industry, top_n=2)
        if r.get("best"):
            return "YES", str(r["best"].get("code", ""))
    except Exception:
        pass
    return "NO", ""


def _retreat_map(industry_daily: pd.DataFrame, signal_data_date: str) -> dict[str, str]:
    """行业 → retreat_action（仅读取，不触发任何动作）"""
    try:
        from .retreat_signal import build_retreat_cross_section, classify_retreat_action
        df = build_retreat_cross_section(industry_daily, signal_data_date)
        if df is None or df.empty:
            return {}
        return {str(r.get("industry", "")): classify_retreat_action(r.get("stage"))
                for _, r in df.iterrows()}
    except Exception:
        return {}


def update_signal_ledger(
    radar_candidates: list[dict],
    today_signals: pd.DataFrame,
    industry_daily: pd.DataFrame,
    signal_data_date: str,
    open_positions: list[dict] | None = None,
) -> pd.DataFrame:
    """每日更新信号生命周期账本。

    状态解析（每个活跃信号每天重算）：
      1. 退潮确认        → CLOSED（close_reason=退潮信号出现）
      2. 有对应开放持仓  → HOLD
      3. 已正式触发      → TRIGGERED
      4. 否则            → RADAR

    signal_id 首次创建后跨日复用（同一行业同一次生命周期只一个 id）；
    行业退出后重新进入（此前已 CLOSED）会生成新的 signal_id。
    """
    df = _read_ledger()
    date_compact = signal_data_date.replace("-", "")
    sd_dt = pd.to_datetime(signal_data_date)

    # 当日正式信号（仅当日）
    if not today_signals.empty and "signal_date" in today_signals.columns:
        try:
            sig_dates = pd.to_datetime(today_signals["signal_date"])
            today_only = today_signals[sig_dates == sd_dt].copy()
        except Exception:
            today_only = today_signals
    else:
        today_only = today_signals if not today_signals.empty else pd.DataFrame()

    # 开放持仓来源行业集合（用于 HOLD 判定）
    open_industries = set()
    if open_positions:
        for p in open_positions:
            si = p.get("source_industry")
            if si:
                open_industries.add(si)

    retreat = _retreat_map(industry_daily, signal_data_date)

    # 索引活跃 signal_id（按行业）
    active_by_industry: dict[str, int] = {}
    if not df.empty:
        for idx in df.index:
            if df.at[idx, "current_status"] in ACTIVE_STATUSES:
                active_by_industry.setdefault(df.at[idx, "industry"], idx)

    # 标记本日出现过的行业（雷达 or 触发）
    seen_industries: set[str] = set()

    def _ensure_row(industry: str, status: str) -> int:
        """返回该行业在账本中的行索引（复用活跃 id 或新建）"""
        if industry in active_by_industry:
            idx = active_by_industry[industry]
            return idx
        # 新建
        seq = _next_seq(df, date_compact)
        sid = _make_signal_id(date_compact, industry, seq)
        new_idx = len(df)
        row = {c: "" for c in LEDGER_COLS}
        row["signal_id"] = sid
        row["industry"] = industry
        row["first_observation_date"] = signal_data_date
        row["current_status"] = status
        row["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        df.loc[new_idx] = row
        active_by_industry[industry] = new_idx
        return new_idx

    # 1) 雷达候选 → 确保有行（状态 RADAR，除非已触发）
    for cand in (radar_candidates or []):
        ind = cand["industry"]
        seen_industries.add(ind)
        idx = _ensure_row(ind, STATUS_RADAR)
        # 若已 triggered/hold（之前触发），保持，不回退为 RADAR

    # 2) 正式信号 → 确保有行并置 TRIGGERED + signal_date
    for _, sig in today_only.iterrows():
        ind = sig.get("industry", "")
        if not ind:
            continue
        seen_industries.add(ind)
        idx = _ensure_row(ind, STATUS_TRIGGERED)
        df.at[idx, "signal_date"] = signal_data_date

    # 3) 逐行解析最终状态 + 计算 D+N + ETF
    for idx in df.index:
        ind = df.at[idx, "industry"]
        status = df.at[idx, "current_status"]
        if status == STATUS_CLOSED:
            # 已关闭：仅刷新 D+N（若有新数据）不再改状态
            _refresh_outcome(df, idx, industry_daily, ind)
            continue

        # 状态解析
        if retreat.get(ind) == "CONFIRMED_RETREAT":
            new_status = STATUS_CLOSED
            df.at[idx, "close_reason"] = "退潮信号出现"
        elif ind in open_industries:
            new_status = STATUS_HOLD
        elif df.at[idx, "signal_date"]:
            new_status = STATUS_TRIGGERED
        else:
            new_status = STATUS_RADAR
        df.at[idx, "current_status"] = new_status

        # 阶段（锚点：触发日用 signal_date，否则用首次观察日）
        anchor = df.at[idx, "signal_date"] or df.at[idx, "first_observation_date"]
        ds = _count_trading_days_since(industry_daily, ind, anchor, signal_data_date)
        df.at[idx, "current_phase"] = _phase_label(ds)

        # D+N 表现
        sig_date = df.at[idx, "signal_date"]
        if sig_date:
            _refresh_outcome(df, idx, industry_daily, ind)
        else:
            # 未触发：等待观察
            for p in (1, 5, 20):
                df.at[idx, f"d{p}_ret"] = ""
                df.at[idx, f"d{p}_breadth"] = ""

        # ETF 可执行性
        ta, code = _trade_available(ind)
        df.at[idx, "trade_available"] = ta
        df.at[idx, "etf_code"] = code

        df.at[idx, "updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    _write_ledger_atomic(df)
    return df


def _refresh_outcome(df: pd.DataFrame, idx: int, industry_daily: pd.DataFrame, industry: str) -> None:
    sig_date = df.at[idx, "signal_date"]
    if not sig_date:
        return
    outcome = _compute_signal_outcome(industry_daily, industry, sig_date)
    for p in (1, 5, 20):
        o = outcome.get(p, {"ret": None, "breadth": None})
        df.at[idx, f"d{p}_ret"] = "" if o["ret"] is None else str(o["ret"])
        df.at[idx, f"d{p}_breadth"] = "" if o["breadth"] is None else str(o["breadth"])


def get_recent_ledger(n: int = 5) -> pd.DataFrame:
    """获取最近 N 条信号档案（按更新时间倒序）"""
    df = _read_ledger()
    if df.empty:
        return df
    return df.tail(n)
