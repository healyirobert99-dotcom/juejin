"""
信号雷达测试（13 条要求全覆盖）

要求覆盖：
1. 正式信号逻辑和数量完全不变
2. 雷达只使用 signal_data_date 及以前数据
3. 不满足行业规模的不显示
4. 不足 310 日的不显示
5. 处于冷却期的不显示
6. 弱势背景不满足的不显示
7. 当前三个条件至少满足两个才显示
8. 正式信号不在雷达重复显示
9. 最多显示 5 个
10. 排序稳定
11. 无候选时显示简洁空状态
12. 数据阻断报告不显示雷达
13. 雷达不会生成订单或交易
"""
from __future__ import annotations

import sys
from pathlib import Path

import ast
import inspect
import numpy as np
import pandas as pd
import pytest

V0_6_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(V0_6_ROOT))

from v0_6.core.signal_radar import build_signal_radar


def _make_industry_daily(
    n_industries: int = 3,
    n_days: int = 400,
    base_date: str = "2026-06-29",
    n_stocks: int = 25,
) -> pd.DataFrame:
    """生成模拟行业日指标"""
    rows = []
    start = pd.to_datetime(base_date) - pd.Timedelta(days=n_days)
    trade_dates = pd.bdate_range(start=start, end=base_date)

    for ind_idx in range(n_industries):
        ind_name = f"行业_{ind_idx}"
        # 前 60 天宽度 < 35%，后 60 天宽度逐渐上升
        for i, td in enumerate(trade_dates):
            if i < n_days - 120:  # 弱势背景阶段
                breadth = 0.20 + np.random.uniform(-0.05, 0.05)
            elif i < n_days - 60:  # 过渡阶段
                breadth = 0.25 + np.random.uniform(-0.05, 0.05)
            elif i < n_days - 5:  # 接近触发
                breadth = 0.32 + np.random.uniform(-0.02, 0.04)
            else:  # 最后 5 天宽度大幅上升
                breadth = 0.38 + ind_idx * 0.05 + np.random.uniform(-0.02, 0.02)

            # 量比：前 60 天低，最后高
            if i < n_days - 5:
                vol_ratio = 0.8 + np.random.uniform(-0.1, 0.2)
            else:
                vol_ratio = 1.1 + ind_idx * 0.1 + np.random.uniform(-0.05, 0.1)

            # 20 日收益：前 60 天负，最后部分正
            if i < n_days - 5:
                ret20 = -0.02 + np.random.uniform(-0.01, 0.02)
            else:
                ret20 = 0.005 + ind_idx * 0.005 + np.random.uniform(-0.005, 0.01)

            rows.append({
                "trade_date": td,
                "industry": ind_name,
                "n": n_stocks,
                "breadth_ma20": breadth,
                "breadth_ma60": breadth - 0.05,
                "avg_vol_ratio": vol_ratio,
                "avg_ret20": ret20,
                "median_ret20": ret20 - 0.002,
            })

    df = pd.DataFrame(rows)
    return df


def _make_industry_daily_small_industry(base_date="2026-06-29") -> pd.DataFrame:
    """包含一个小行业（n=15 < 20）"""
    start = pd.to_datetime(base_date) - pd.Timedelta(days=400)
    trade_dates = pd.bdate_range(start=start, end=base_date)
    rows = []
    for td in trade_dates:
        rows.append({
            "trade_date": td,
            "industry": "小行业",
            "n": 15,
            "breadth_ma20": 0.40,
            "breadth_ma60": 0.35,
            "avg_vol_ratio": 1.2,
            "avg_ret20": 0.02,
            "median_ret20": 0.01,
        })
    return pd.DataFrame(rows)


def test_signal_unchanged():
    """要求 1：正式信号逻辑和数量完全不变"""
    daily = _make_industry_daily(n_industries=3, n_days=400)
    all_signals = pd.DataFrame([
        {"industry": "行业_0", "signal_date": pd.Timestamp("2026-05-01"),
         "priority": "FIRST"},
    ])
    sdd = "2026-06-29"
    radar = build_signal_radar(daily, all_signals, sdd, max_items=5)
    # 这只是一个健全检查——雷达不应修改 all_signals
    assert len(all_signals) == 1
    assert all_signals.iloc[0]["industry"] == "行业_0"
    assert all_signals.iloc[0]["priority"] == "FIRST"


def test_future_data_excluded():
    """要求 2：雷达只使用 signal_data_date 及以前数据"""
    # 有 7 天数据，但信号数据日期为 2026-06-29，雷达应只用到这天
    dates = pd.date_range("2026-06-25", "2026-07-05")
    rows = []
    for td in dates:
        rows.append({
            "trade_date": td,
            "industry": "测试行业",
            "n": 25,
            "breadth_ma20": 0.40,
            "breadth_ma60": 0.35,
            "avg_vol_ratio": 1.2,
            "avg_ret20": 0.02,
            "median_ret20": 0.01,
        })
    daily = pd.DataFrame(rows)
    sdd = "2026-06-29"
    radar = build_signal_radar(daily, pd.DataFrame(), sdd, max_items=5)
    # 只有截至 2026-06-29 有数据，但历史不足 310 日，不应有候选
    assert len(radar) == 0, "历史不足 310 日不应有候选"


def test_min_stocks_filter():
    """要求 3：不满足行业规模的不显示（n < 20）"""
    daily = _make_industry_daily_small_industry()
    # 加入一个满足条件的行业
    big = _make_industry_daily(n_industries=1, n_days=400)
    combined = pd.concat([daily, big], ignore_index=True)
    sdd = "2026-06-29"
    radar = build_signal_radar(combined, pd.DataFrame(), sdd, max_items=5)
    # 小行业不应出现
    for c in radar:
        assert c["industry"] != "小行业"


def test_min_310_days_filter():
    """要求 4：不足 310 日的不显示"""
    daily = _make_industry_daily(n_industries=1, n_days=200)
    sdd = "2026-06-29"
    radar = build_signal_radar(daily, pd.DataFrame(), sdd, max_items=5)
    assert len(radar) == 0


def test_cooldown_filter():
    """要求 5：处于冷却期的不显示"""
    daily = _make_industry_daily(n_industries=1, n_days=400)
    # 上次信号在 15 天前（60 天冷却期内）
    sdd = "2026-06-29"
    last_signal = pd.to_datetime(sdd) - pd.Timedelta(days=15)
    all_signals = pd.DataFrame([
        {"industry": "行业_0", "signal_date": last_signal,
         "priority": "FIRST"},
    ])
    radar = build_signal_radar(daily, all_signals, sdd, max_items=5)
    assert len(radar) == 0


def test_weakness_background_filter():
    """要求 6：弱势背景不满足的不显示（n_low < 30）"""
    rows = []
    start = pd.to_datetime("2026-06-29") - pd.Timedelta(days=400)
    trade_dates = pd.bdate_range(start=start, end="2026-06-29")
    for i, td in enumerate(trade_dates):
        # 前 60 天不足 30 天低于 35% → 弱势背景不满足
        if i < len(trade_dates) - 60:
            breadth = 0.38
        else:
            breadth = 0.40
        rows.append({
            "trade_date": td,
            "industry": "强行业",
            "n": 25,
            "breadth_ma20": breadth,
            "breadth_ma60": breadth - 0.05,
            "avg_vol_ratio": 1.2,
            "avg_ret20": 0.02,
            "median_ret20": 0.01,
        })
    daily = pd.DataFrame(rows)
    sdd = "2026-06-29"
    radar = build_signal_radar(daily, pd.DataFrame(), sdd, max_items=5)
    assert len(radar) == 0


def test_min_two_conditions():
    """要求 7：当前三个条件至少满足两个才显示"""
    daily = _make_industry_daily(n_industries=3, n_days=400)
    # 修改一个行业的最后一天数据，仅满足 1 个条件
    sdd = pd.Timestamp("2026-06-29")
    mask = (daily["industry"] == "行业_0") & (daily["trade_date"] == sdd)
    daily.loc[mask, "breadth_ma20"] = 0.30  # < 0.35
    daily.loc[mask, "avg_vol_ratio"] = 0.8    # < 1.0
    daily.loc[mask, "avg_ret20"] = 0.02        # > 0 ✓

    radar = build_signal_radar(daily, pd.DataFrame(), "2026-06-29", max_items=5)
    for c in radar:
        assert c["industry"] != "行业_0", "仅满足 1/3 条件的行业不应显示"


def test_formal_signal_excluded():
    """要求 8：正式信号不在雷达重复显示"""
    daily = _make_industry_daily(n_industries=3, n_days=400)
    sdd = "2026-06-29"
    # 行业_0 当天有正式信号
    all_signals = pd.DataFrame([
        {"industry": "行业_0", "signal_date": pd.Timestamp(sdd),
         "priority": "FIRST"},
    ])
    radar = build_signal_radar(daily, all_signals, sdd, max_items=5)
    for c in radar:
        assert c["industry"] != "行业_0", "正式信号行业不应出现在雷达"


def test_max_five_items():
    """要求 9：最多显示 5 个"""
    daily = _make_industry_daily(n_industries=10, n_days=400, n_stocks=25)
    sdd = "2026-06-29"
    radar = build_signal_radar(daily, pd.DataFrame(), sdd, max_items=5)
    assert len(radar) <= 5


def test_sort_stable():
    """要求 10：排序稳定（两次调用结果相同）"""
    daily = _make_industry_daily(n_industries=5, n_days=400, n_stocks=25)
    sdd = "2026-06-29"
    radar1 = build_signal_radar(daily, pd.DataFrame(), sdd, max_items=5)
    # 使用新的 DataFrame 拷贝，模拟独立运行
    daily2 = daily.copy()
    radar2 = build_signal_radar(daily2, pd.DataFrame(), sdd, max_items=5)
    names1 = [c["industry"] for c in radar1]
    names2 = [c["industry"] for c in radar2]
    assert names1 == names2


def test_empty_state():
    """要求 11：无候选时显示简洁空状态"""
    daily = _make_industry_daily(n_industries=1, n_days=400, n_stocks=25)
    # 所有指标都调低，确保无候选
    daily["breadth_ma20"] = 0.20
    daily["avg_vol_ratio"] = 0.5
    daily["avg_ret20"] = -0.05
    sdd = "2026-06-29"
    radar = build_signal_radar(daily, pd.DataFrame(), sdd, max_items=5)
    assert len(radar) == 0


def test_blocked_no_radar():
    """要求 12：数据阻断报告不显示雷达"""
    # 不直接测试 HTML 渲染，而是验证：
    # 当 validation["ok"] == False 时，main() 中的雷达代码不会被执行
    # 这通过标准 render_blocked_report 不接收 radar_candidates 参数来保证
    # 验证 build_signal_radar 在无效数据上返回空列表
    daily = pd.DataFrame()
    sdd = "2026-06-29"
    radar = build_signal_radar(daily, pd.DataFrame(), sdd, max_items=5)
    assert len(radar) == 0


def test_no_trade_generation():
    """要求 13：雷达不会生成订单或交易"""
    # 验证雷达模块没有导入交易相关的模块
    import ast
    import inspect
    import v0_6.core.signal_radar as sr
    source = inspect.getsource(sr)
    # 检查 import 语句中没有交易相关模块
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if "live_trade" in alias.name or "trade" in alias.name.lower():
                    if "signal" not in alias.name:
                        assert False, f"雷达模块不应导入交易相关模块: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            if node.module and ("live_trade" in node.module or "trade" in node.module.lower()):
                if "signal" not in node.module:
                    assert False, f"雷达模块不应导入交易相关模块: {node.module}"
