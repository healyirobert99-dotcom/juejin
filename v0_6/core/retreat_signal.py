"""
v0.6 退潮信号检测

纯数据函数，不做数据库读写。

核心设计：
1. build_retreat_cross_section() — 当日全量行业横截面排名 + 退潮评分
2. compute_retreat_for_industry() — 从横截面中取出单个行业
3. classify_retreat_stage() — 阶段判定（原样迁移）
4. classify_retreat_action() — 三档业务映射

公式源自老系统 generate_daily_review.py（commit 357f6ee366ea2838faf2aa8ec0f9d2787c59c7af）
文件 SHA256: d4f2cecf6a2b319b9baf525040f67fc575d1c98e3320e623c2927ab0a60b563d
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

# 用于 rank 的原始字段列表（与老系统一致）
_RANK_COLS = [
    "ret5", "ret10", "ret20", "ret60",
    "excess20", "excess60",
    "above20",
    "amount5_60", "amount10_60", "amount20_60",
]

# 退潮项中各因子的得分上限
_RETREAT_WEIGHTS = {
    "ret5_rank": 20,
    "drawdown20_penalty": 25,
    "above20_rank": 20,
    "amount5_60_rank": 10,
    "midline_history_bonus": 25,  # 曾中线高分额外惩罚
}


def build_retreat_cross_section(
    industry_daily: pd.DataFrame,
    as_of_date: str,
    min_stocks_per_industry: int = 20,
) -> pd.DataFrame:
    """为当日所有合格行业构建退潮横截面

    步骤：
    1. 筛选当日数据（trade_date == as_of_date）
    2. 过滤股票数不足的行业
    3. 在当日全量横截面内计算百分位排名
    4. 计算 confirmed_score_raw / candidate_score_raw / warming_score_raw
    5. 计算 retreat_score / in_retreat_risk
    6. 判定阶段

    参数：
        industry_daily: compute_industry_daily_metrics() 的输出（多日）
        as_of_date: 分析日期 YYYY-MM-DD
        min_stocks_per_industry: 最小股票数过滤

    返回：当日横截面 DataFrame，含所有退潮相关字段
    """
    # 1. 取当日数据
    day = industry_daily[industry_daily["trade_date"] == as_of_date].copy()
    if day.empty:
        return pd.DataFrame()

    # 2. 过滤股票数不足的行业
    if "n" in day.columns:
        day = day[day["n"] >= min_stocks_per_industry].copy()

    if day.empty:
        return pd.DataFrame()

    # 3. 全量横截面百分位排名（pct=True → 0~1 百分位）
    for col in _RANK_COLS:
        if col in day.columns:
            day[col + "_rank"] = day[col].rank(pct=True)

    # 4. 计算评分（与老系统一致）
    # warming_score_raw
    if all(c in day.columns for c in ["ret5_rank", "ret10_rank", "ret20_rank",
                                        "excess20_rank", "ret60_rank", "excess60_rank",
                                        "amount5_60_rank", "above20_rank"]):
        day["warming_score_raw"] = (
            day["ret5_rank"] * 8
            + day["ret10_rank"] * 12
            + day["ret20_rank"] * 20
            + day["excess20_rank"] * 15
            + day["ret60_rank"] * 10
            + day["excess60_rank"] * 8
            + (day.get("outperform_days20", 0) / 20).clip(0, 1) * 10
            + day["amount5_60_rank"] * 10
            + day["above20_rank"] * 7
        )

    # candidate_score_raw
    if all(c in day.columns for c in ["ret5_rank", "ret20_rank", "excess20_rank",
                                        "ret60_rank", "excess60_rank",
                                        "above20_rank", "amount10_60_rank"]):
        day["candidate_score_raw"] = (
            day["ret5_rank"] * 5
            + day["ret20_rank"] * 25
            + day["excess20_rank"] * 15
            + day["ret60_rank"] * 15
            + day["excess60_rank"] * 10
            + (day.get("outperform_days20", 0) / 20).clip(0, 1) * 15
            + (day.get("top20_days20", 0) / 20).clip(0, 1) * 10
            + day["above20_rank"] * 15
            + day["amount10_60_rank"] * 5
            + (day["drawdown20"] > -0.10).astype(int) * 5
        )

    # confirmed_score_raw
    if all(c in day.columns for c in ["ret5_rank", "ret20_rank", "excess20_rank",
                                        "ret60_rank", "excess60_rank", "above20_rank"]):
        day["confirmed_score_raw"] = (
            day["ret5_rank"] * 5
            + day["ret20_rank"] * 25
            + day["excess20_rank"] * 10
            + day["ret60_rank"] * 20
            + day["excess60_rank"] * 10
            + (day.get("outperform_days30", 0) / 30).clip(0, 1) * 15
            + day["above20_rank"] * 15
        )

    # 惩罚逻辑
    retreat_condition = (
        (day.get("ret5", 0) < -0.08)
        | (day.get("drawdown20", 0) < -0.10)
        | (day.get("above20", 0) < 0.40)
    )
    retreat_penalty = retreat_condition.astype(int) * 25
    mild_penalty = (
        (day.get("ret20", 0) < -0.03).astype(int) * 10
        + (day.get("drawdown20", 0) < -0.07).astype(int) * 8
    )
    day["confirmed_score"] = (day.get("confirmed_score_raw", 0) - retreat_penalty - mild_penalty).clip(lower=0)
    day["candidate_score"] = (day.get("candidate_score_raw", 0) - retreat_condition.astype(int) * 12).clip(lower=0)
    day["warming_score"] = day.get("warming_score_raw", 0)

    # 5. retreat_score（五项因子，上限 100）
    ret_score = pd.Series(0.0, index=day.index)
    if "ret5_rank" in day.columns:
        ret_score += (1 - day["ret5_rank"]) * _RETREAT_WEIGHTS["ret5_rank"]
    if "drawdown20" in day.columns:
        ret_score += (day["drawdown20"].abs().clip(0, 0.25) / 0.25) * _RETREAT_WEIGHTS["drawdown20_penalty"]
    if "above20_rank" in day.columns:
        ret_score += (1 - day["above20_rank"]) * _RETREAT_WEIGHTS["above20_rank"]
    if "amount5_60_rank" in day.columns:
        ret_score += (1 - day["amount5_60_rank"]) * _RETREAT_WEIGHTS["amount5_60_rank"]
    midline_bonus = (
        (day.get("confirmed_score_raw", 0) > 65)
        | (day.get("candidate_score_raw", 0) > 65)
    ).astype(int) * _RETREAT_WEIGHTS["midline_history_bonus"]
    day["retreat_score"] = ret_score + midline_bonus
    day["in_retreat_risk"] = day["retreat_score"] >= 55

    # 6. 阶段判定
    day["stage"] = day.apply(classify_retreat_stage, axis=1)

    return day


def classify_retreat_stage(row: pd.Series) -> Optional[str]:
    """判断单个行业的退潮阶段（原样迁移）

    返回老系统的原始阶段名称：低频监控 / 企稳重估 / 确认后退潮 / 退潮风险 / None
    """
    fast_theme = row.get("speed_type") == "快主题"
    fast_retreat = fast_theme and (
        float(row.get("ret5", 0)) < -0.06
        or float(row.get("drawdown20", 0)) < -0.08
        or float(row.get("drawdown60", float(row.get("drawdown20", 0)))) < -0.10
        or float(row.get("above20_chg5", 0)) < -0.20
    )
    retreat = (
        float(row.get("ret5", 0)) < -0.08
        or float(row.get("drawdown20", 0)) < -0.10
        or float(row.get("above20", 0)) < 0.40
        or bool(row.get("in_retreat_risk", False))
        or fast_retreat
    )

    # 低频监控（优先级最高）
    if row.get("low_breadth10", 0) >= 8 and row.get("excess20_chg5", 0) < 0 and row.get("amount5_60", 0) < 0.90:
        return "低频监控"

    # 企稳重估（高于退潮优先级）
    if (
        float(row.get("retreat_score", 0)) >= 55
        and float(row.get("ret5", 0)) > 0
        and float(row.get("ret10", 0)) > 0
        and float(row.get("above20", 0)) >= 0.35
        and float(row.get("drawdown20", 0)) > -0.08
        and float(row.get("amount5_60", 0)) >= 0.90
    ):
        return "企稳重估"

    # 确认后退潮
    if float(row.get("confirmed_score_raw", 0)) >= 70 and retreat:
        return "确认后退潮"

    # 退潮风险
    if float(row.get("retreat_score", 0)) >= 60 and retreat:
        return "退潮风险"

    return None


def classify_retreat_action(stage: Optional[str]) -> str:
    """将老阶段映射为标准化业务结果"""
    if stage is None:
        return "NORMAL"
    if stage in ("确认后退潮", "退潮风险", "低频监控"):
        return "CONFIRMED_RETREAT"
    if stage == "企稳重估":
        return "NORMAL"
    return "NORMAL"


def compute_retreat_for_industry(
    cross_section: pd.DataFrame,
    industry: str,
) -> dict:
    """从横截面中提取单个行业的退潮结果

    参数：
        cross_section: build_retreat_cross_section() 的输出
        industry: 行业名称

    如果 industry 不在横截面中（如股票数不足被过滤），返回 NORMAL。
    """
    if cross_section.empty or industry not in cross_section["industry"].values:
        return {
            "retreat_score": 0.0,
            "in_retreat_risk": False,
            "retreat_stage": None,
            "retreat_action": "NORMAL",
            "retreat_components": {},
        }

    row = cross_section[cross_section["industry"] == industry].iloc[0]
    stage = row.get("stage") if "stage" in row.index else classify_retreat_stage(row)
    action = classify_retreat_action(stage)

    return {
        "retreat_score": float(row["retreat_score"]) if "retreat_score" in row.index else 0.0,
        "in_retreat_risk": bool(row.get("in_retreat_risk", False)),
        "retreat_stage": stage,
        "retreat_action": action,
        "retreat_components": {
            "ret5": float(row.get("ret5", 0)),
            "drawdown20": float(row.get("drawdown20", 0)),
            "above20": float(row.get("above20", 0)),
            "amount5_60": float(row.get("amount5_60", 0)),
            "ret5_rank": float(row.get("ret5_rank", 0)),
            "above20_rank": float(row.get("above20_rank", 0)),
            "amount5_60_rank": float(row.get("amount5_60_rank", 0)),
            "confirmed_score_raw": float(row.get("confirmed_score_raw", 0)),
            "candidate_score_raw": float(row.get("candidate_score_raw", 0)),
        },
        "retreat_penalties": {
            "retreat_condition": bool(
                float(row.get("ret5", 0)) < -0.08
                or float(row.get("drawdown20", 0)) < -0.10
                or float(row.get("above20", 0)) < 0.40
            ),
            "retreat_penalty": 25 if bool(
                float(row.get("ret5", 0)) < -0.08
                or float(row.get("drawdown20", 0)) < -0.10
                or float(row.get("above20", 0)) < 0.40
            ) else 0,
            "midline_history_bonus": float(25 if (
                float(row.get("confirmed_score_raw", 0)) > 65
                or float(row.get("candidate_score_raw", 0)) > 65
            ) else 0),
        },
    }
