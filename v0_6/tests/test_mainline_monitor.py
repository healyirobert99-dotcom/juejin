"""
主线结构 + 相对强度关键测试
"""
import sys
from pathlib import Path
V0_6_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(V0_6_ROOT))

import numpy as np
import pandas as pd
import pytest

from v0_6.core.mainline_monitor import (
    classify_mainline_structure,
    compute_relative_strength,
    compute_trend_continuation,
)


def _make_industry_daily(struct="broad", n_days=30):
    """构造 industry_daily 测试数据"""
    np.random.seed(42)
    dates = pd.date_range("2026-06-01", periods=n_days, freq="B")
    data = []
    for i, d in enumerate(dates):
        if struct == "broad":
            ret20 = 0.08 + np.random.normal(0, 0.01)
            ret20_median = ret20 - 0.02
            above20 = 0.60 + np.random.normal(0, 0.02)
        elif struct == "concentrated":
            ret20 = 0.15 + np.random.normal(0, 0.01)
            ret20_median = 0.02 + np.random.normal(0, 0.005)
            above20 = 0.45 + np.random.normal(0, 0.02)
        elif struct == "weakening":
            ret20 = -0.02 + 0.001 * i  # 逐渐转弱
            ret20_median = -0.04 + 0.001 * i  # 中位数转弱
            above20 = 0.40 - 0.010 * i  # 宽度连续大幅下降
        else:
            ret20 = 0.05
            ret20_median = 0.04
            above20 = 0.50

        data.append({
            "trade_date": d.strftime("%Y-%m-%d"),
            "industry": "测试行业",
            "n": 30,
            "avg_ret20": ret20,
            "median_ret20": ret20_median,
            "avg_ret60": ret20 * 1.5,
            "breadth_ma20": max(0.1, above20),
            "breadth_ma60": max(0.1, above20 * 0.85),
        })
    return pd.DataFrame(data)


# ═══════════════════ 主线结构 ═══════════════════

def test_broad_advance_classification():
    df = _make_industry_daily("broad")
    result = classify_mainline_structure(df, "测试行业", "2026-07-09")
    assert result["structure"] == "BROAD_ADVANCE"
    assert "扩散上涨" in result["structure_label"]
    assert len(result["supporting_evidence"]) > 0


def test_concentrated_classification():
    df = _make_industry_daily("concentrated")
    result = classify_mainline_structure(df, "测试行业", "2026-07-09")
    assert result["structure"] == "CONCENTRATED"
    assert "抱团" in result["structure_label"]


def test_internal_weakening_classification():
    df = _make_industry_daily("weakening")
    result = classify_mainline_structure(df, "测试行业", "2026-07-09")
    assert result["structure"] == "INTERNAL_WEAKENING"
    assert len(result["risk_evidence"]) > 0


def test_missing_industry_returns_unclear():
    df = _make_industry_daily("broad")
    result = classify_mainline_structure(df, "不存在行业", "2026-07-09")
    assert result["structure"] == "UNCLEAR"


def test_mainline_does_not_modify_trade_action():
    """主线结构不产生交易动作（只返回解释）"""
    df = _make_industry_daily("weakening")
    result = classify_mainline_structure(df, "测试行业", "2026-07-09")
    assert "action" not in result
    assert "clear" not in result.get("holding_guidance", "").lower()
    assert "减仓" not in result.get("holding_guidance", "")


# ═══════════════════ 相对强度 ═══════════════════

def test_relative_strength_ranking():
    """相对强度排名正确"""
    df = _make_industry_daily("broad")
    # 添加更多行业用于排名比较
    data = []
    dates = pd.date_range("2026-06-01", periods=30, freq="B")
    for i, d in enumerate(dates):
        for j in range(10):
            data.append({
                "trade_date": d.strftime("%Y-%m-%d"),
                "industry": f"行业{j}",
                "n": 30,
                "avg_ret20": np.random.normal(0, 0.05),
                "avg_ret60": np.random.normal(0, 0.10),
                "breadth_ma20": 0.5,
            })
    multi = pd.DataFrame(data)
    result = compute_relative_strength(multi, "行业5", "2026-07-09")
    assert result["strength"] in ("STRENGTHENING", "STABLE_STRONG", "WEAKENING", "DATA_INSUFFICIENT")
    assert result["ret20_rank"] is not None or result["strength"] == "DATA_INSUFFICIENT"


def test_relative_strength_no_trade_action():
    """相对强度不产生交易动作"""
    df = _make_industry_daily("broad")
    result = compute_relative_strength(df, "测试行业", "2026-07-09")
    assert "action" not in result
    assert "clear" not in result.get("explanation", "").lower()
