"""
回归测试：行业最少股票数 = 20 的小行业过滤规则

覆盖：
- 配置值必须为 20（防止误改）
- 19 只股票的行业必须被过滤
- 20 只股票的行业必须被保留
- 边界 19 vs 20 区分

测试数据完全在代码中临时构造，不连接任何真实数据库。
"""
import sys
from pathlib import Path

import pandas as pd
import numpy as np

V0_6_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(V0_6_ROOT))

from v0_6.core.config import RULES
from v0_6.core.signal_b import compute_per_stock_indicators, compute_industry_daily_metrics


def _make_stock_data(n_stocks: int, n_days: int = 60, industry: str = "测试行业") -> pd.DataFrame:
    """构造模拟股票日线 DataFrame

    为 n_stocks 只股票生成 n_days 个交易日的数据，
    确保 compute_per_stock_indicators 的滚动计算能产出非 NaN 值。
    """
    rows = []
    # 生成连续交易日序列
    trade_dates = pd.date_range("2026-01-01", periods=n_days, freq="B")
    for stock_id in range(n_stocks):
        ts_code = f"TEST{stock_id:04d}.SZ"
        base_price = 10.0 + stock_id * 0.5
        for day_idx, trade_date in enumerate(trade_dates):
            price = base_price + day_idx * 0.05
            rows.append({
                "ts_code": ts_code,
                "trade_date": trade_date,
                "close": price,
                "vol": 1000000 + stock_id * 10000,
                "industry": industry,
            })
    df = pd.DataFrame(rows)
    df = df.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    return df


def test_rules_uses_20():
    """配置值必须为 20"""
    assert RULES["signal"]["min_stocks_per_industry"] == 20, (
        f"rules.json min_stocks_per_industry 应为 20，实际为 "
        f"{RULES['signal']['min_stocks_per_industry']}"
    )


def test_industry_with_19_stocks_is_filtered():
    """19 只股票的行业不通过 compute_industry_daily_metrics 的小行业过滤"""
    n = 19
    sd = _make_stock_data(n, n_days=60, industry="过滤行业_19")
    sd_ind = compute_per_stock_indicators(sd)
    daily = compute_industry_daily_metrics(sd_ind)

    matched = daily[daily["industry"] == "过滤行业_19"]
    assert matched.empty, (
        f"行业'过滤行业_19'有 {n} 只股票（< 20），应被过滤，"
        f"但出现在聚合结果中（n={matched['n'].values[0] if not matched.empty else 'N/A'}）"
    )


def test_industry_with_20_stocks_is_kept():
    """20 只股票的行业通过 compute_industry_daily_metrics 的小行业过滤"""
    n = 20
    sd = _make_stock_data(n, n_days=60, industry="保留行业_20")
    sd_ind = compute_per_stock_indicators(sd)
    daily = compute_industry_daily_metrics(sd_ind)

    matched = daily[daily["industry"] == "保留行业_20"]
    assert not matched.empty, (
        f"行业'保留行业_20'有 {n} 只股票（>= 20），应被保留，"
        "但未出现在聚合结果中"
    )
    actual_n = int(matched["n"].iloc[0])
    assert actual_n == n, (
        f"保留行业的股票计数字段应为 {n}，实际为 {actual_n}"
    )


def test_boundary_19_vs_20():
    """边界验证：同时构造 19 和 20 的两个行业，验证过滤/保留分界线正确"""
    sd_19 = _make_stock_data(19, n_days=60, industry="边界过滤_19")
    sd_20 = _make_stock_data(20, n_days=60, industry="边界保留_20")
    sd = pd.concat([sd_19, sd_20], ignore_index=True)
    sd = sd.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)

    sd_ind = compute_per_stock_indicators(sd)
    daily = compute_industry_daily_metrics(sd_ind)

    # 19 应过滤
    filtered = daily[daily["industry"] == "边界过滤_19"]
    assert filtered.empty, "19 只股票的行业应被过滤"

    # 20 应保留
    kept = daily[daily["industry"] == "边界保留_20"]
    assert not kept.empty, "20 只股票的行业应被保留"
    assert int(kept["n"].iloc[0]) == 20, f"保留行业 n 应为 20，实际为 {int(kept['n'].iloc[0])}"


if __name__ == "__main__":
    print("\n=== 行业最少股票数=20 回归测试 ===\n")
    test_rules_uses_20()
    print("  ✅ 配置值等于 20")
    test_industry_with_19_stocks_is_filtered()
    print("  ✅ 19 只股票被过滤")
    test_industry_with_20_stocks_is_kept()
    print("  ✅ 20 只股票被保留")
    test_boundary_19_vs_20()
    print("  ✅ 19 vs 20 边界正确")
    print("\n✅ 全部通过\n")
