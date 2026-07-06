"""
v0.6 主线结构监控与相对强度

职责：
1. 主线结构分类（扩散上涨 / 集中抱团 / 内部转弱 / 暂不明确）
2. 相对强度排名（行业横截面 20/60 日排名 + 变化）
3. 趋势延续（仅作为次要信息）

所有输出只用于解释持仓状态，不产生交易动作。
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from .config import RULES


# ═══════════════════ 主线结构 ═══════════════════

def classify_mainline_structure(
    industry_daily: pd.DataFrame,
    industry: str,
    as_of_date: str,
) -> dict:
    """对单个持仓来源行业进行主线结构分类

    参数：
        industry_daily: compute_industry_daily_metrics() 的输出（多日截面）
        industry: 行业名称
        as_of_date: 分析日期 YYYY-MM-DD

    返回：{structure, structure_label, summary, holding_guidance,
           key_metrics, supporting_evidence, risk_evidence}
    """
    # 取行业数据
    ind = industry_daily[industry_daily["industry"] == industry]
    if ind.empty:
        return _unclear("行业中无此数据")

    # 取最近 20 个交易日数据
    ind_rec = ind[ind["trade_date"] <= as_of_date].sort_values("trade_date")
    if len(ind_rec) < 10:
        return _unclear("数据不足（少于 10 个交易日）")

    today = ind_rec.iloc[-1]
    min_stocks = RULES.get("signal", {}).get("min_stocks_per_industry", 20)
    n_stocks = int(today.get("n", 0))
    if n_stocks < min_stocks:
        return _unclear(f"行业股票数不足（{n_stocks} < {min_stocks}）")

    # 关键指标（使用真实字段名）
    required = ["avg_ret20", "median_ret20", "breadth_ma20", "n"]
    missing = [c for c in required if c not in today.index]
    if missing:
        return _unclear(f"缺少必要字段: {', '.join(missing)}")

    ret20_mean = float(today.get("avg_ret20", 0))
    ret20_median = float(today.get("median_ret20", 0))
    above20 = float(today.get("breadth_ma20", 0))
    if "breadth_ma60" in today.index:
        above60 = float(today["breadth_ma60"])

    # 宽度变化：从历史 breadth_ma20 计算
    if len(ind_rec) >= 5:
        above20_5d_ago = float(ind_rec.iloc[-5].get("breadth_ma20", above20))
    else:
        above20_5d_ago = above20
    breadth_change_5d = above20 - above20_5d_ago

    # 上涨股票比例估算（用 breadth_ma20 近似）
    up_ratio = above20

    # 强股与中位数差距
    mean_median_gap = abs(ret20_mean - ret20_median) if ret20_mean and ret20_median else 0

    metrics = {
        "ret20_mean": round(ret20_mean, 4),
        "ret20_median": round(ret20_median, 4),
        "breadth_ma20": round(above20, 3),
        "breadth_change_5d": round(breadth_change_5d, 3),
        "mean_median_gap": round(mean_median_gap, 4),
        "n_stocks": n_stocks,
    }

    # ── 三段判断 ──

    # 扩散上涨
    if (
        ret20_mean > 0
        and ret20_median > 0
        and breadth_change_5d >= -0.05
        and mean_median_gap < 0.08
        and up_ratio >= 0.45
    ):
        return {
            "structure": "BROAD_ADVANCE",
            "structure_label": "扩散上涨",
            "summary": "行业上涨由较多成分股共同推动，结构较为扩散。",
            "holding_guidance": "继续持有",
            "key_metrics": metrics,
            "supporting_evidence": _build_evidence(
                _e(ret20_mean > 0, f"行业 20 日收益为正（{ret20_mean:.1%}）"),
                _e(ret20_median > 0, f"中位数收益为正（{ret20_median:.1%}）"),
                _e(breadth_change_5d >= -0.05, f"宽度近期稳定（变化 {breadth_change_5d:+.0%}）"),
                _e(mean_median_gap < 0.08, "平均与中位数收益差距较小"),
            ),
            "risk_evidence": [],
        }

    # 内部转弱
    if (
        breadth_change_5d < -0.05
        or (ret20_median < 0 and above20 < 0.40)
        or up_ratio < 0.30
    ):
        risk = _build_evidence(
            _e(breadth_change_5d < -0.05, f"MA20 宽度连续下降（{breadth_change_5d:+.0%}）"),
            _e(ret20_median < 0 and above20 < 0.40, "中位数转弱且宽度不足"),
            _e(up_ratio < 0.30, "上涨股票比例偏低"),
        )
        return {
            "structure": "INTERNAL_WEAKENING",
            "structure_label": "内部转弱",
            "summary": "行业表面可能仍维持强势，但内部参与度正在下降。",
            "holding_guidance": "重点观察退潮信号",
            "key_metrics": metrics,
            "supporting_evidence": [],
            "risk_evidence": risk,
        }

    # 集中抱团
    if ret20_mean > 0 and mean_median_gap >= 0.08:
        return {
            "structure": "CONCENTRATED",
            "structure_label": "集中抱团",
            "summary": "行业趋势仍在，但收益主要集中于少数核心股票。",
            "holding_guidance": "继续持有，注意波动",
            "key_metrics": metrics,
            "supporting_evidence": _build_evidence(
                _e(ret20_mean > 0, f"行业整体收益仍明显为正（{ret20_mean:.1%}）"),
                _e(mean_median_gap >= 0.08, f"强股与中位数差距较大（{mean_median_gap:.1%}）"),
            ),
            "risk_evidence": _build_evidence(
                _e(mean_median_gap >= 0.08, "收益集中度偏高"),
                _e(breadth_change_5d < -0.02, "宽度略有收缩"),
            ),
        }

    return _unclear("无法确定结构类型")


def _unclear(reason: str) -> dict:
    return {
        "structure": "UNCLEAR",
        "structure_label": "暂不明确",
        "summary": f"当前结构证据不足，暂不下结论。",
        "holding_guidance": "按原策略持有",
        "key_metrics": {},
        "supporting_evidence": [],
        "risk_evidence": [reason],
    }


def _e(condition: bool, msg: str) -> str | None:
    return msg if condition else None


def _build_evidence(*items: str | None) -> list[str]:
    return [i for i in items if i][:4]


# ═══════════════════ 相对强度 ═══════════════════

def compute_relative_strength(
    industry_daily: pd.DataFrame,
    industry: str,
    as_of_date: str,
) -> dict:
    """行业横截面相对强度排名

    返回：{strength, strength_label, ret20_rank, ret60_rank,
           rank_change_10d, pct_20d, pct_60d, explanation}
    """
    day = industry_daily[industry_daily["trade_date"] == as_of_date]
    if day.empty or "avg_ret20" not in day.columns:
        return _rs_insufficient("当日截面数据不足")

    # 在全行业截面中排名
    day = day.copy()
    day["ret20_rank"] = day["avg_ret20"].rank(pct=True)

    # 60 日排名：如果有 avg_ret60 就计算，否则标记为暂无
    has_ret60 = "avg_ret60" in day.columns and day["avg_ret60"].notna().sum() > 3
    if has_ret60:
        day["ret60_rank"] = day["avg_ret60"].rank(pct=True)

    target = day[day["industry"] == industry]
    if target.empty:
        return _rs_insufficient("该行业不在当日截面中")

    ret20_rank = float(target["ret20_rank"].iloc[0])
    ret60_rank = float(target["ret60_rank"].iloc[0]) if has_ret60 else None
    n_industries = len(day)

    # 10 日前排名变化
    prev_date_row = _find_prev_date(industry_daily, as_of_date, 10)
    rank_change = None
    if prev_date_row is not None:
        prev = prev_date_row.copy()
        if "avg_ret20" in prev.columns:
            prev["prev_rank"] = prev["avg_ret20"].rank(pct=True)
            prev_target = prev[prev["industry"] == industry]
            if not prev_target.empty:
                prev_ret20_rank = float(prev_target["prev_rank"].iloc[0])
                rank_change = round(ret20_rank - prev_ret20_rank, 3)

    # 判断强度状态
    is_strong = ret20_rank > 0.50  # 处于行业前 50%
    is_improving = rank_change is not None and rank_change > 0.03
    is_weakening = rank_change is not None and rank_change < -0.03

    if is_strong and is_improving:
        strength, label = "STRENGTHENING", "强度增强"
        explanation = f"行业 20 日收益处于全市场前 {int((1-ret20_rank)*100)}%，近 10 日排名上升。"
    elif is_strong and not is_weakening:
        strength, label = "STABLE_STRONG", "强度稳定"
        explanation = f"行业排名持续处于强势区间（前 {int((1-ret20_rank)*100)}%）。"
    elif is_weakening:
        strength, label = "WEAKENING", "强度减弱"
        explanation = f"行业排名近 10 日明显下滑（变化 {rank_change:+.0%}）。"
    elif is_strong:
        strength, label = "STABLE_STRONG", "强度稳定"
        explanation = f"行业排名维持在前 {int((1-ret20_rank)*100)}% 区间。"
    else:
        strength, label = "WEAKENING", "强度减弱"
        explanation = f"行业 20 日收益排名偏后（前 {int((1-ret20_rank)*100)}%）。"

    return {
        "strength": strength,
        "strength_label": label,
        "ret20_rank": round(ret20_rank, 3),
        "ret60_rank": round(ret60_rank, 3) if ret60_rank is not None else None,
        "rank_change_10d": rank_change,
        "pct_20d": int((1 - ret20_rank) * 100),
        "pct_60d": int((1 - ret60_rank) * 100) if ret60_rank is not None else None,
        "n_industries": n_industries,
        "explanation": explanation,
    }


def _rs_insufficient(reason: str) -> dict:
    return {
        "strength": "DATA_INSUFFICIENT",
        "strength_label": "暂不可判断",
        "ret20_rank": None, "ret60_rank": None,
        "rank_change_10d": None,
        "pct_20d": None, "pct_60d": None,
        "n_industries": 0,
        "explanation": reason,
    }


def _find_prev_date(industry_daily: pd.DataFrame, as_of_date: str, days_back: int):
    """找 N 个交易日前的日期"""
    dates = sorted(industry_daily["trade_date"].unique())
    try:
        idx = list(dates).index(as_of_date)
        if idx >= days_back:
            return industry_daily[industry_daily["trade_date"] == dates[idx - days_back]]
    except (ValueError, IndexError):
        pass
    return None


# ═══════════════════ 趋势延续（次要） ═══════════════════

def compute_trend_continuation(
    industry_daily: pd.DataFrame,
    industry: str,
    as_of_date: str,
    signal_date: Optional[str] = None,
) -> dict:
    """信号触发后的趋势延续评估"""
    if not signal_date:
        return {"status": "data_insufficient", "label": "无信号日期"}

    ind = industry_daily[industry_daily["industry"] == industry].sort_values("trade_date")
    if ind.empty or len(ind) < 5:
        return {"status": "data_insufficient", "label": "数据不足"}

    # 信号日之后的交易日
    post = ind[ind["trade_date"] >= signal_date]
    days_since = len(post)
    if days_since < 3:
        return {"status": "too_early", "label": "观察期不足", "days_since": days_since}

    # 关键变化
    sig_row = post.iloc[0] if not post.empty else ind.iloc[-1]
    now_row = post.iloc[-1]

    breadth_change = float(now_row.get("breadth_ma20", 0)) - float(sig_row.get("breadth_ma20", 0))
    ret_change = float(now_row.get("median_ret20", 0)) - float(sig_row.get("median_ret20", 0))

    # 判断
    if breadth_change >= 0 and ret_change >= 0:
        status, label = "good", "延续良好"
    elif breadth_change >= -0.05 and ret_change >= -0.02:
        status, label = "moderate", "延续一般"
    else:
        status, label = "weak", "延续偏弱"

    return {
        "status": status,
        "label": label,
        "days_since": days_since,
        "breadth_change": round(breadth_change, 3),
        "ret_change": round(ret_change, 4),
        "signal_date": signal_date,
    }
