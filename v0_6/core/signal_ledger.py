"""信号生命周期账本（掘金信号日报 v1.2 修复版）

职责（仅记录能力，不参与任何信号计算与交易动作）：
- 为每个进入雷达或正式触发的行业生成唯一 signal_id
- 维护生命周期状态：RADAR / TRIGGERED / HOLD / CLOSED
- 记录信号产生后的实际表现：signal_date + D+1/D+5/D+20 收益（宽度）
- 记录行业 ETF 可执行性（trade_available + 匹配状态）
- 雷达退出时关闭生命周期，重新进入生成新 ID

signal_id 格式：YYYYMMDD_行业名_序号  （如 20260711_医疗保健_001）
状态仅使用：RADAR / TRIGGERED / HOLD / CLOSED（不新增其他状态）

本模块不参与任何交易决策。退潮分析不直接关闭生命周期，仅当实际平仓（closed_positions）
传入时才关闭。永不回退状态。

持久化：reports/observation/signal_ledger.csv
"""
from __future__ import annotations

import os
import tempfile
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

# ── 生命周期状态（唯一允许集合） ──
STATUS_RADAR = "RADAR"
STATUS_TRIGGERED = "TRIGGERED"
STATUS_HOLD = "HOLD"
STATUS_CLOSED = "CLOSED"
ACTIVE_STATUSES = {STATUS_RADAR, STATUS_TRIGGERED, STATUS_HOLD}

# 状态层级（用于防止回退）：数值越大越"后期"
_STATUS_ORDER: dict[str, int] = {
    STATUS_RADAR: 0,
    STATUS_TRIGGERED: 1,
    STATUS_HOLD: 2,
    STATUS_CLOSED: 99,
}

LEDGER_COLS = [
    "signal_id", "industry", "first_observation_date", "signal_date",
    "current_status", "current_phase", "elapsed_trading_days",
    "d1_ret", "d1_breadth", "d5_ret", "d5_breadth", "d20_ret", "d20_breadth",
    "trade_available", "etf_code", "etf_match_status", "etf_match_note",
    "close_reason", "updated_at",
]


# ═══════════════════════════════════════════════════════════════════════
# 空值处理（第 4 节）
# ═══════════════════════════════════════════════════════════════════════

def _has_value(value) -> bool:
    """统一判断字段是否有有效值。

    禁止在代码中使用裸 `if df.at[idx, "signal_date"]:` 判断。
    """
    if value is None:
        return False
    try:
        if pd.isna(value):
            return False
    except (TypeError, ValueError):
        pass
    s = str(value).strip()
    return s not in ("", "nan", "None", "NaT", "NaN")


def _safe_str(value) -> str:
    """将任意值转换为统一空字符串表示。"""
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    s = str(value).strip()
    if s in ("nan", "None", "NaT", "NaN"):
        return ""
    return s


# ═══════════════════════════════════════════════════════════════════════
# 文件路径与 I/O（第 4.1 / 11 / 14 节）
# ═══════════════════════════════════════════════════════════════════════

def _ledger_path() -> Path:
    from .config import DATA_DIR
    p = DATA_DIR.parent / "reports" / "observation"
    p.mkdir(parents=True, exist_ok=True)
    return p / "signal_ledger.csv"


def _read_ledger() -> pd.DataFrame:
    """读取账本 CSV。空值统一为空字符串，损坏文件抛异常（不静默清空）。"""
    p = _ledger_path()
    if not p.exists():
        return pd.DataFrame(columns=LEDGER_COLS)
    file_size = p.stat().st_size
    try:
        df = pd.read_csv(
            str(p),
            dtype=str,
            keep_default_na=False,
        )
    except Exception as exc:
        raise RuntimeError(
            f"读取信号账本失败: {p}: {exc}"
        ) from exc
    # 损坏文件检测：非空文件但只读出了极少的列
    if file_size > 0 and len(df.columns) < 3:
        raise RuntimeError(
            f"读取信号账本失败: {p}: 文件可能已损坏（列数 {len(df.columns)} < 3）"
        )
    for c in LEDGER_COLS:
        if c not in df.columns:
            df[c] = ""
    # 统一空值
    for c in LEDGER_COLS:
        df[c] = df[c].apply(lambda x: _safe_str(x))
    return df[LEDGER_COLS]


def _write_ledger_atomic(df: pd.DataFrame) -> None:
    """原子写入 + 简单并发锁（第 14 节）。"""
    p = _ledger_path()
    lock_path = Path(str(p) + ".lock")

    if lock_path.exists():
        try:
            age = time.time() - lock_path.stat().st_mtime
        except OSError:
            age = 0
        if age < 30:
            raise RuntimeError(
                f"信号账本正在被另一个进程写入（锁 {lock_path}），请稍后重试"
            )
        lock_path.unlink(missing_ok=True)

    try:
        lock_path.touch()
        # 写入前重新读取最新账本，防止覆盖并发写入
        try:
            latest = pd.read_csv(str(p), dtype=str, keep_default_na=False) if p.exists() else None
        except Exception:
            latest = None
        if latest is not None and len(latest) > 0:
            # 保留 latest 中比 df 新的行（按 updated_at）- 简化合并：
            # 若 latest 行数多于 df 且 updated_at 不同，用 latest
            if len(latest) > len(df):
                df = latest.copy()
                for c in LEDGER_COLS:
                    if c not in df.columns:
                        df[c] = ""

        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".csv", dir=str(p.parent))
        os.close(tmp_fd)
        try:
            write_df = df.copy()
            for col in write_df.columns:
                write_df[col] = write_df[col].fillna("").astype(str).replace(
                    {"nan": "", "None": "", "NaT": "", "NaN": ""}
                )
            write_df.to_csv(tmp_path, index=False, encoding="utf-8")
            os.replace(tmp_path, str(p))
        except Exception:
            Path(tmp_path).unlink(missing_ok=True)
            raise
    finally:
        lock_path.unlink(missing_ok=True)


# ═══════════════════════════════════════════════════════════════════════
# signal_id 生成（第 13 节）
# ═══════════════════════════════════════════════════════════════════════

def _next_seq(ledger_df: pd.DataFrame, date_compact: str) -> int:
    """当日已用的最大序号 + 1（幂等：重跑同日期不会跳号）。"""
    if ledger_df.empty:
        return 1
    prefix = date_compact + "_"
    max_seq = 0
    for sid in ledger_df["signal_id"].tolist():
        if isinstance(sid, str) and sid.startswith(prefix):
            parts = sid.rsplit("_", 1)
            if len(parts) == 2:
                try:
                    max_seq = max(max_seq, int(parts[-1]))
                except (ValueError, TypeError):
                    pass
    return max_seq + 1


def _make_signal_id(date_compact: str, industry: str, seq: int) -> str:
    return f"{date_compact}_{industry}_{seq:03d}"


# ═══════════════════════════════════════════════════════════════════════
# 交易日工具（第 5 / 6 节）
# ═══════════════════════════════════════════════════════════════════════

def _trading_day_offset(
    industry_daily: pd.DataFrame,
    industry: str,
    start_date: str,
    offset: int,
    as_of_date: str,
):
    """返回行业在 start_date 之后第 offset 个交易日的整行（截至 as_of_date）。

    trade_date > start_date 且 trade_date <= as_of_date，不足返回 None。
    """
    if industry_daily is None or industry_daily.empty:
        return None
    sub = industry_daily[
        (industry_daily["industry"] == industry) &
        (industry_daily["trade_date"] > str(start_date)) &
        (industry_daily["trade_date"] <= str(as_of_date))
    ].sort_values("trade_date")
    if len(sub) < offset:
        return None
    return sub.iloc[offset - 1]


def _count_trading_days_since(
    industry_daily: pd.DataFrame,
    industry: str,
    start_date: str,
    as_of: str,
) -> int | None:
    """返回 start_date 之后（不含当天）到 as_of 之间的交易日数。
    start_date 为信号触发日，统计不含信号日本身。
    """
    if industry_daily is None or industry_daily.empty:
        return None
    sub = industry_daily[
        (industry_daily["industry"] == industry) &
        (industry_daily["trade_date"] > str(start_date)) &
        (industry_daily["trade_date"] <= str(as_of))
    ]
    return int(len(sub))


def _compute_signal_outcome(
    industry_daily: pd.DataFrame,
    industry: str,
    start_date: str,
    as_of_date: str,
) -> dict:
    """计算 signal_date 之后 D+1/D+5/D+20 的复利收益与 MA20 宽度。

    关键：使用 as_of_date 截断，不得读取未来数据（第 5 节）。

    返回 {1: {ret, breadth}, 5: {...}, 20: {...}}，数据不足时 ret/breadth 为 None。
    """
    out: dict[int, dict] = {}
    for p in (1, 5, 20):
        row = _trading_day_offset(industry_daily, industry, start_date, p, as_of_date)
        if row is None:
            out[p] = {"ret": None, "breadth": None}
            continue
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
        if "breadth_ma20" in row:
            val = row.get("breadth_ma20")
            if val not in (None, "") and str(val).strip() not in ("nan", "None", "NaT"):
                try:
                    breadth = round(float(val), 4)
                except (ValueError, TypeError):
                    breadth = None
        out[p] = {"ret": ret, "breadth": breadth}
    return out


def _phase_label(days_since: int | None) -> str:
    """阶段标识（不含信号日）"""
    if days_since is None:
        return "D0"
    if days_since < 5:
        return "D0"
    if days_since < 20:
        return "D+5"
    return "D+20"


# ═══════════════════════════════════════════════════════════════════════
# ETF 可执行性（第 12 节）
# ═══════════════════════════════════════════════════════════════════════

def _trade_available(industry: str) -> tuple[str, str, str, str]:
    """返回 (trade_available, etf_code, etf_match_status, etf_match_note)。

    match_status: MATCHED / NOT_FOUND / ERROR
    """
    try:
        from .etf_matcher import match_industry_to_etfs
        r = match_industry_to_etfs(industry, top_n=2)
    except Exception as exc:
        return "NO", "", "ERROR", f"ETF 匹配异常: {exc}"

    if r is None:
        return "NO", "", "ERROR", "ETF 匹配器返回 None"

    try:
        if r.get("best"):
            code = str(r["best"].get("code", ""))
            note = r.get("note", "")
            return ("YES", code, "MATCHED", note)
        else:
            note = r.get("note", "未找到对应 ETF")
            return ("NO", "", "NOT_FOUND", note)
    except Exception as exc:
        return "NO", "", "ERROR", f"ETF 匹配结果解析异常: {exc}"


# ═══════════════════════════════════════════════════════════════════════
# 退潮信息（仅读取，不产生交易动作 — 第 10 节）
# ═══════════════════════════════════════════════════════════════════════

def _retreat_map(industry_daily: pd.DataFrame, signal_data_date: str) -> dict[str, str]:
    """行业 → retreat_action（仅读取，不触发任何动作）。

    退潮分析结果不得直接关闭生命周期。
    """
    try:
        from .retreat_signal import build_retreat_cross_section, classify_retreat_action
        df = build_retreat_cross_section(industry_daily, signal_data_date)
        if df is None or df.empty:
            return {}
        return {str(r.get("industry", "")): classify_retreat_action(r.get("stage"))
                for _, r in df.iterrows()}
    except Exception:
        return {}


# ═══════════════════════════════════════════════════════════════════════
# 核心：信号生命周期更新（第 7-10 节）
# ═══════════════════════════════════════════════════════════════════════

def update_signal_ledger(
    radar_candidates: list[dict],
    today_signals: pd.DataFrame,
    industry_daily: pd.DataFrame,
    signal_data_date: str,
    open_positions: list[dict] | None = None,
    closed_positions: list[dict] | None = None,
) -> pd.DataFrame:
    """每日更新信号生命周期账本。

    状态解析顺序：
      1. closed_positions → CLOSED（明确平仓）
      2. open_positions → HOLD（有持仓）
      3. 已有 signal_date → TRIGGERED
      4. 否则 → RADAR

    雷达退出：今日未出现在 radar_candidates 且未触发且不在持仓的 RADAR → CLOSED。
    TRIGGERED / HOLD 不会因未出现在 radar 而关闭。
    CLOSED 不可回退，HOLD 不可回退到 TRIGGERED。

    signal_id 首次创建后跨日复用（同一行业同一次生命周期只一个 id）；
    行业退出后重新进入（此前已 CLOSED）会生成新的 signal_id。
    """
    df = _read_ledger()
    date_compact = signal_data_date.replace("-", "")
    sd_dt = pd.to_datetime(signal_data_date)

    # ── 当日正式信号（仅当日） ──
    if not today_signals.empty and "signal_date" in today_signals.columns:
        try:
            sig_dates = pd.to_datetime(today_signals["signal_date"])
            today_only = today_signals[sig_dates == sd_dt].copy()
        except Exception:
            today_only = today_signals
    else:
        today_only = today_signals if not today_signals.empty else pd.DataFrame()

    # ── 当日信号行业集合 ──
    today_signal_industries: set[str] = set()
    for _, sig in today_only.iterrows():
        ind = sig.get("industry", "")
        if ind:
            today_signal_industries.add(ind)

    # ── 开放持仓来源行业集合 ──
    open_industries: set[str] = set()
    if open_positions:
        for p in open_positions:
            si = p.get("source_industry")
            if si:
                open_industries.add(si)

    # ── 平仓记录（优先按 signal_id，fallback 按行业） ──
    closed_by_signal_id: dict[str, str] = {}   # signal_id → close_reason
    closed_by_industry: dict[str, str] = {}     # industry → close_reason
    if closed_positions:
        for cp in closed_positions:
            sid = cp.get("signal_id", "")
            ind = cp.get("source_industry", "")
            reason = cp.get("close_reason", "OTHER")
            if sid:
                closed_by_signal_id[sid] = reason
            elif ind:
                closed_by_industry[ind] = reason

    # ── 退潮信息（仅记录，不关闭 — 第 10 节） ──
    # _retreat_map 依然调用，可用于日报展示层的 risk 标注，但不在账本中直接关闭
    _retreat_map(industry_daily, signal_data_date)

    # ── 索引活跃信号 ──
    active_by_industry: dict[str, int] = {}
    if not df.empty:
        for idx in df.index:
            status = df.at[idx, "current_status"]
            if status in ACTIVE_STATUSES:
                ind = df.at[idx, "industry"]
                # 如果同行业有多条活跃记录，保持最早的（或最新的？保持最早一致）
                if ind not in active_by_industry:
                    active_by_industry[ind] = idx

    # ── 本日出现过的行业（雷达 or 触发） ──
    seen_industries: set[str] = set()

    def _ensure_row(industry: str, status: str) -> int:
        """返回该行业在账本中的行索引（复用活跃 id 或新建）。"""
        if industry in active_by_industry:
            return active_by_industry[industry]
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

    # ── 1) 雷达候选 → 确保有行 ──
    for cand in (radar_candidates or []):
        ind = cand["industry"]
        seen_industries.add(ind)
        _ensure_row(ind, STATUS_RADAR)

    # ── 2) 正式信号 → 确保有行 + signal_date（不覆盖已有 — 第 8 节） ──
    for _, sig in today_only.iterrows():
        ind = sig.get("industry", "")
        if not ind:
            continue
        seen_industries.add(ind)
        idx = _ensure_row(ind, STATUS_TRIGGERED)
        # 只在尚无 signal_date 时设置，不覆盖原日期
        # （REPEAT 信号不重置 D+N 锚点 — 第 8.2 节）
        if not _has_value(df.at[idx, "signal_date"]):
            df.at[idx, "signal_date"] = signal_data_date

    # ── 3) 雷达退出检测（第 7 节） ──
    # 对每一条当前为 RADAR 且今日未出现的 → CLOSED
    for idx in df.index:
        status = df.at[idx, "current_status"]
        if status != STATUS_RADAR:
            continue
        ind = df.at[idx, "industry"]
        if ind in seen_industries or ind in open_industries:
            continue
        # 该行业今日既不在雷达列表，也不在正式信号列表 → 雷达退出
        df.at[idx, "current_status"] = STATUS_CLOSED
        df.at[idx, "close_reason"] = "雷达退出"
        df.at[idx, "updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # 从活跃索引中移除
        active_by_industry.pop(ind, None)

    # ── 4) 逐行解析最终状态 + 计算 D+N / ETF / Phase ──
    for idx in df.index:
        ind = df.at[idx, "industry"]
        old_status = df.at[idx, "current_status"]

        if old_status == STATUS_CLOSED:
            # 已关闭：仅刷新 D+N（若有信号日期且新数据到达）不改变状态
            sig_date = df.at[idx, "signal_date"]
            if _has_value(sig_date):
                _refresh_outcome(df, idx, industry_daily, ind, signal_data_date)
            continue

        # ── 确定新状态（不可回退 — 第 9.3 节） ──
        new_status: str | None = None

        # 检查是否在平仓列表中
        sid = df.at[idx, "signal_id"]
        if sid and sid in closed_by_signal_id:
            new_status = STATUS_CLOSED
            reason = closed_by_signal_id[sid]
            if not _has_value(df.at[idx, "close_reason"]):
                df.at[idx, "close_reason"] = reason
        elif ind in closed_by_industry:
            new_status = STATUS_CLOSED
            reason = closed_by_industry[ind]
            if not _has_value(df.at[idx, "close_reason"]):
                df.at[idx, "close_reason"] = reason

        if new_status is None:
            if ind in open_industries:
                new_status = STATUS_HOLD
            elif _has_value(df.at[idx, "signal_date"]):
                # 检查是否有对应的活跃持仓（通过 source_industry）
                new_status = STATUS_TRIGGERED
            else:
                new_status = STATUS_RADAR

        # ── 防回退：只允许状态前进 ──
        old_order = _STATUS_ORDER.get(old_status, 0)
        new_order = _STATUS_ORDER.get(new_status, 0)
        if new_order < old_order and old_status != STATUS_CLOSED:
            # 不允许回退；保持原状态
            new_status = old_status

        df.at[idx, "current_status"] = new_status

        # ── 阶段（锚点：触发日用 signal_date，否则用首次观察日） ──
        anchor = df.at[idx, "signal_date"] if _has_value(df.at[idx, "signal_date"]) else df.at[idx, "first_observation_date"]
        ds = _count_trading_days_since(industry_daily, ind, anchor, signal_data_date)
        df.at[idx, "current_phase"] = _phase_label(ds)
        df.at[idx, "elapsed_trading_days"] = str(ds) if ds is not None else ""

        # ── D+N 表现（第 5 节：as_of_date 截断） ──
        sig_date = df.at[idx, "signal_date"]
        if _has_value(sig_date):
            _refresh_outcome(df, idx, industry_daily, ind, signal_data_date)
        else:
            for p in (1, 5, 20):
                df.at[idx, f"d{p}_ret"] = ""
                df.at[idx, f"d{p}_breadth"] = ""

        # ── ETF 可执行性（第 12 节） ──
        ta, code, ms, mn = _trade_available(ind)
        df.at[idx, "trade_available"] = ta
        df.at[idx, "etf_code"] = code
        df.at[idx, "etf_match_status"] = ms
        df.at[idx, "etf_match_note"] = mn

        df.at[idx, "updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    _write_ledger_atomic(df)
    return df


# ═══════════════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════════════

def _refresh_outcome(
    df: pd.DataFrame,
    idx: int,
    industry_daily: pd.DataFrame,
    industry: str,
    as_of_date: str,
) -> None:
    sig_date = df.at[idx, "signal_date"]
    if not _has_value(sig_date):
        return
    outcome = _compute_signal_outcome(industry_daily, industry, sig_date, as_of_date)
    for p in (1, 5, 20):
        o = outcome.get(p, {"ret": None, "breadth": None})
        df.at[idx, f"d{p}_ret"] = "" if o["ret"] is None else str(o["ret"])
        df.at[idx, f"d{p}_breadth"] = "" if o["breadth"] is None else str(o["breadth"])


def get_recent_ledger(n: int = 5) -> pd.DataFrame:
    """获取最近 N 条信号档案（按 updated_at 倒序）。"""
    df = _read_ledger()
    if df.empty:
        return df
    return (
        df.sort_values(
            ["updated_at", "signal_id"],
            ascending=[False, False],
        )
        .head(n)
        .reset_index(drop=True)
    )
