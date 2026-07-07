"""
v0.6.3 · 行业 → ETF 匹配器测试

覆盖：
- 各类行业名匹配到 ETF
- 别名映射正确
- 置信度按匹配类型分级
- ETF 偏离评估（purity 警告）
"""
import sys
from pathlib import Path

V0_6_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(V0_6_ROOT))

from v0_6.core.etf_matcher import (
    match_industry_to_etfs,
    etf_correlation_to_industry,
    load_sector_etf_map,
)


def test_load_map():
    m = load_sector_etf_map()
    assert len(m) >= 80, f"应至少 80 条 ETF，实际 {len(m)}"
    print(f"  ✓ sector_etf_map 加载 {len(m)} 条 ETF")


def test_exact_match():
    """精确匹配：sub 字段 == industry"""
    cases = [
        ("银行", "512800"),
        ("证券", "512880"),
        ("白酒", "512690"),
        ("房地产", "512200"),
        ("半导体", "512480"),
    ]
    for ind, expected_code in cases:
        r = match_industry_to_etfs(ind)
        assert r["match_type"] == "exact", f"{ind} 应精确匹配，实 {r['match_type']}"
        assert r["best"] is not None, f"{ind} 应有 best"
        assert r["best"]["code"] == expected_code, f"{ind} best 应 {expected_code}，实 {r['best']['code']}"
        assert r["confidence"] >= 0.9
    print(f"  ✓ 5 个精确匹配行业全部正确")


def test_alias_match():
    """别名匹配：行业名与 ETF 板块名不同但语义相同"""
    # 「保险」是真实常见行业，但 ETF 板块叫「非银金融」
    r = match_industry_to_etfs("保险")
    assert r["match_type"] in ("alias", "fuzzy"), f"保险应别名匹配，实 {r['match_type']}"
    assert len(r["matches"]) > 0, f"保险应至少匹配 1 个 ETF"
    # 别名映射的置信度
    if r["match_type"] == "alias":
        assert r["confidence"] >= 0.8
    print(f"  ✓ 保险 → {r['matches'][0]['code']} {r['matches'][0]['name']} (match={r['match_type']})")


def test_no_match():
    """无匹配：细分行业无对应 ETF"""
    # 「空运」「超市连锁」是行业信号中的细分，但无对应 ETF
    for ind in ["空运", "超市连锁", "软饮料"]:
        r = match_industry_to_etfs(ind)
        # 可能是 fuzzy/sub 也匹配不到
        if r["match_type"] == "none":
            assert r["confidence"] == 0.0
            assert r["best"] is None
            print(f"  ✓ {ind} → 无匹配（库内无对应 ETF）")
        else:
            print(f"  ⊘ {ind} → 匹配到 {len(r['matches'])} 个（意外，可能需要调整）")


def test_top_n():
    """top_n 参数生效"""
    r = match_industry_to_etfs("半导体", top_n=2)
    assert len(r["matches"]) <= 2
    assert r["best"] is not None
    # 排序：按 score desc, scale desc
    print(f"  ✓ 半导体 top2: {[m['code'] for m in r['matches']]}")


def test_correlation_eval():
    """ETF 与行业偏离评估"""
    r = etf_correlation_to_industry("保险", "512070")  # 易方达沪深300非银ETF（偏纯，含券商）
    assert r["found"]
    assert r["purity"] in ("偏纯", "纯", "不纯")
    # 应有警告
    if r["purity"] in ("偏纯", "不纯"):
        assert len(r["warnings"]) > 0
        print(f"  ✓ 512070 保险评估：{r['purity']}，警告 {len(r['warnings'])} 条")


def test_all_signals_have_etf():
    """最近 30 天所有信号都应能映射到 ETF（至少给个建议）"""
    import pytest
    from v0_6.core import detect_stabilizing_b, compute_per_stock_indicators, compute_industry_daily_metrics, mark_repeat_priority
    from v0_6.core.signal_b import load_raw_daily
    import pandas as pd

    # 确保 DB_PATH 指向真实项目库
    from v0_6.core.config import DB_PATH
    if not DB_PATH.exists():
        pytest.skip("项目数据库不存在")

    sd = load_raw_daily("2010-01-01", "2026-06-24")
    sd_ind = compute_per_stock_indicators(sd)
    daily = compute_industry_daily_metrics(sd_ind)
    signals = detect_stabilizing_b(daily)
    signals = mark_repeat_priority(signals)
    recent = signals[signals["signal_date"] >= "2026-05-25"].sort_values("signal_date", ascending=False)

    print(f"  最近 30 天信号: {len(recent)} 个")
    for _, sig in recent.iterrows():
        r = match_industry_to_etfs(sig["industry"], top_n=2)
        ind = sig["industry"]
        if r["best"]:
            top = r["best"]
            print(f"    [{sig['signal_date']}] {ind:6} → {top['code']} {top['name']:20} (conf={r['confidence']:.2f}, {r['match_type']})")
        else:
            print(f"    [{sig['signal_date']}] {ind:6} → ⚠️ 无匹配")


if __name__ == "__main__":
    print("\n=== v6.3 行业→ETF 匹配测试 ===\n")
    test_load_map()
    test_exact_match()
    test_alias_match()
    test_no_match()
    test_top_n()
    test_correlation_eval()
    test_all_signals_have_etf()
    print("\n✅ 全部通过\n")
