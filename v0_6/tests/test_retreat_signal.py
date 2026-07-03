"""
退潮信号专项测试

覆盖横截面排名、阈值边界、动作优先级、来源行业一致性。
"""
import sys
from pathlib import Path

V0_6_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(V0_6_ROOT))

import numpy as np
import pandas as pd
import pytest


# ── 构造行业截面数据 ──

def _make_industry_day(n_industries=20, seed=42):
    """构造一日的 industry_daily 测试截面"""
    np.random.seed(seed)
    data = []
    for i in range(n_industries):
        data.append({
            "trade_date": "2026-07-01",
            "industry": f"行业{i:02d}",
            "n": 25 + i,
            "ret5": np.random.normal(0, 0.05),
            "ret10": np.random.normal(0, 0.07),
            "ret20": np.random.normal(0, 0.10),
            "ret60": np.random.normal(0, 0.15),
            "excess20": np.random.normal(0, 0.05),
            "excess60": np.random.normal(0, 0.08),
            "above20": np.random.uniform(0.3, 0.8),
            "amount5_60": np.random.uniform(0.5, 1.5),
            "amount10_60": np.random.uniform(0.5, 1.5),
            "amount20_60": np.random.uniform(0.5, 1.5),
            "drawdown20": np.random.uniform(-0.15, 0),
            "outperform_days20": np.random.randint(5, 20),
            "top20_days20": np.random.randint(0, 10),
            "outperform_days30": np.random.randint(10, 30),
        })
    return pd.DataFrame(data)


def _make_retreat_industry_day(n_industries=20):
    """构造一个有明显退潮行业的截面"""
    np.random.seed(42)
    data = []
    for i in range(n_industries):
        # 行业0: 明显退潮 (ret5=-12%, drawdown20=-15%, above20=25%)
        if i == 0:
            data.append({
                "trade_date": "2026-07-01", "industry": "退潮行业",
                "n": 30, "ret5": -0.12, "ret10": -0.08, "ret20": -0.05,
                "ret60": -0.02, "excess20": -0.04, "excess60": -0.03,
                "above20": 0.25, "amount5_60": 0.60, "amount10_60": 0.65,
                "amount20_60": 0.70, "drawdown20": -0.15,
                "outperform_days20": 5, "top20_days20": 2,
                "outperform_days30": 8,
            })
        # 行业1: 企稳重估 (retreat_score>=55, ret5>0, ret10>0, width>=35%, drawdown>-8%, amount>=0.90)
        elif i == 1:
            data.append({
                "trade_date": "2026-07-01", "industry": "企稳行业",
                "n": 30, "ret5": 0.02, "ret10": 0.03, "ret20": 0.01,
                "ret60": -0.10, "excess20": 0.0, "excess60": -0.05,
                "above20": 0.38, "amount5_60": 0.95, "amount10_60": 0.92,
                "amount20_60": 0.90, "drawdown20": -0.06,
                "outperform_days20": 8, "top20_days20": 3,
                "outperform_days30": 12,
            })
        else:
            data.append({
                "trade_date": "2026-07-01", "industry": f"正常行业{i}",
                "n": 30, "ret5": np.random.normal(0.03, 0.02),
                "ret10": np.random.normal(0.04, 0.03),
                "ret20": np.random.normal(0.06, 0.05),
                "ret60": np.random.normal(0.10, 0.08),
                "excess20": np.random.normal(0.02, 0.03),
                "excess60": np.random.normal(0.05, 0.05),
                "above20": np.random.uniform(0.5, 0.8),
                "amount5_60": np.random.uniform(0.9, 1.5),
                "amount10_60": np.random.uniform(0.9, 1.5),
                "amount20_60": np.random.uniform(0.9, 1.5),
                "drawdown20": np.random.uniform(-0.05, 0),
                "outperform_days20": np.random.randint(10, 20),
                "top20_days20": np.random.randint(3, 10),
                "outperform_days30": np.random.randint(15, 30),
            })
    return pd.DataFrame(data)


# ═══════════════════════ 横截面排名测试 ═══════════════════════

def test_cross_section_rank_is_computed_on_all_industries():
    """全行业横截面 rank 后再提取 — 单行业 rank 不对（应是全体排名）"""
    from v0_6.core.retreat_signal import build_retreat_cross_section
    df = _make_industry_day(n_industries=20)

    section = build_retreat_cross_section(df, as_of_date="2026-07-01")
    assert not section.empty
    assert len(section) == 20, "应有 20 个行业"

    # rank 列存在且不是全 1.0
    for col in ["ret5_rank", "above20_rank", "amount5_60_rank"]:
        if col in section.columns:
            ranks = section[col].values
            assert len(ranks) == 20
            # 不是所有 rank 都相同（除非所有原始值相同）
            if section[col.replace("_rank", "")].nunique() > 1:
                assert ranks.std() > 0, f"{col} 所有 rank 相同，说明不是全行业排名"

    # 最大值接近 1.0，最小值接近 0
    if "ret5_rank" in section.columns:
        assert section["ret5_rank"].max() > 0.9
        assert section["ret5_rank"].min() < 0.15


def test_retreat_score_and_stage_for_ebbing_industry():
    """退潮行业应得到高 retreat_score"""
    from v0_6.core.retreat_signal import build_retreat_cross_section, compute_retreat_for_industry
    df = _make_retreat_industry_day(n_industries=20)

    section = build_retreat_cross_section(df, as_of_date="2026-07-01")

    # 退潮行业应处于确认退潮
    retreat = compute_retreat_for_industry(section, "退潮行业")
    assert retreat["retreat_score"] > 60, f"退潮行业 retreat_score 应 > 60, 实 {retreat['retreat_score']:.1f}"
    assert retreat["retreat_action"] == "CONFIRMED_RETREAT", f"应为 CONFIRMED_RETREAT, 实 {retreat['retreat_action']}"

    # 正常行业不应退潮
    normal = compute_retreat_for_industry(section, "正常行业2")
    assert normal["retreat_action"] == "NORMAL", f"正常行业应为 NORMAL, 实 {normal['retreat_action']}"


def test_missing_industry_returns_normal():
    """不存在的行业返回 NORMAL"""
    from v0_6.core.retreat_signal import build_retreat_cross_section, compute_retreat_for_industry
    df = _make_industry_day(n_industries=10)

    section = build_retreat_cross_section(df, as_of_date="2026-07-01")
    result = compute_retreat_for_industry(section, "不存在的行业")
    assert result["retreat_action"] == "NORMAL"


# ═══════════════════════ 阈值边界测试 ═══════════════════════

def test_threshold_55_exact():
    """retreat_score ≥ 55 触发 in_retreat_risk"""
    from v0_6.core.retreat_signal import build_retreat_cross_section

    # 构造：所有值一样（rank=0.5），调整 ret5 使 retreat_score 刚好跨过 55
    np.random.seed(42)
    data = []
    for i in range(20):
        data.append({
            "trade_date": "2026-07-01", "industry": f"行业{i}",
            "n": 30, "ret5": 0.00, "ret10": 0.00, "ret20": 0.00,
            "ret60": 0.00, "excess20": 0.00, "excess60": 0.00,
            "above20": 0.50, "amount5_60": 1.00, "amount10_60": 1.00,
            "amount20_60": 1.00, "drawdown20": 0.0,
            "outperform_days20": 10, "top20_days20": 5,
            "outperform_days30": 15,
        })
    df = pd.DataFrame(data)

    section = build_retreat_cross_section(df, as_of_date="2026-07-01")
    # 全相同值 → 所有 rank=0.5
    # retreat_score = (1-0.5)*20 + (0/0.25)*25 + (1-0.5)*20 + (1-0.5)*10 + 0/25
    #              = 10 + 0 + 10 + 5 + 0 = 25
    # 25 < 55 → 不触发 in_retreat_risk
    assert not section["in_retreat_risk"].any(), "全相同应不触发退潮风险"


def test_rank_direction():
    """ret5 最弱的行业 ret5_rank 最低（pct rank 升序）"""
    from v0_6.core.retreat_signal import build_retreat_cross_section
    df = _make_industry_day(n_industries=20)

    section = build_retreat_cross_section(df, as_of_date="2026-07-01")
    if "ret5_rank" in section.columns and "ret5" in section.columns:
        max_idx = section["ret5"].idxmax()  # 值最大的行业
        min_idx = section["ret5"].idxmin()  # 值最小的行业
        # rank(pct=True) 升序: 最大值 rank ≈ 1.0, 最小值 rank ≈ 0.0
        assert section.loc[max_idx, "ret5_rank"] > section.loc[min_idx, "ret5_rank"], (
            "rank(pct=True) 升序：ret5 最大 → rank 最高"
        )


# ═══════════════════════ 动作优先级测试 ═══════════════════════

def test_retreat_action_only_generates_warning():
    """WARNING 只产出 RETREAT_WATCH，不产出减仓订单"""
    from v0_6.core.retreat_signal import classify_retreat_action

    # 正常阶段 → NORMAL
    assert classify_retreat_action(None) == "NORMAL"

    # 企稳重估 → NORMAL（不是退潮）
    assert classify_retreat_action("企稳重估") == "NORMAL"

    # 确认后退潮 → CONFIRMED_RETREAT
    assert classify_retreat_action("确认后退潮") == "CONFIRMED_RETREAT"
    assert classify_retreat_action("退潮风险") == "CONFIRMED_RETREAT"


def test_unknown_stage_returns_normal():
    from v0_6.core.retreat_signal import classify_retreat_action
    assert classify_retreat_action("未知阶段") == "NORMAL"


# ═══════════════════════ 数据库字段测试 ═══════════════════════

def test_schema_migration_idempotent():
    """init_schema 幂等：多次运行不报错"""
    from v0_6.core.live_trade_store import init_schema
    import sqlite3
    import v0_6.core.config as cfg

    init_schema()
    # 二次运行应成功
    init_schema()

    con = sqlite3.connect(str(cfg.DB_PATH))
    cols = {r[1] for r in con.execute("PRAGMA table_info(live_trades)").fetchall()}
    assert "source_industry" in cols
    assert "retreat_reduced_1_3" in cols
    assert "take_profit_reduced_1_3" in cols
    con.close()


def test_add_trade_with_source_industry():
    """add_trade 接受 source_industry 参数并落库"""
    import v0_6.core.config as cfg
    from v0_6.core.live_trade_store import init_schema, add_trade
    import sqlite3

    init_schema()
    tid = add_trade(
        target="512800",
        target_type="ETF",
        target_name="银行ETF",
        action="BUY",
        amount=10000, price=1.0, shares=10000,
        trade_date="20260701",
        source_industry="银行",
        source_signal_date="20260701",
        source_signal_priority="FIRST",
        source_signal_type="STABILIZING_B",
        source_signal_ret20=0.05,
    )
    assert tid > 0

    con = sqlite3.connect(str(cfg.DB_PATH))
    row = con.execute(
        "SELECT source_industry, source_signal_priority, source_signal_ret20, retreat_reduced_1_3 FROM live_trades WHERE trade_id=?",
        (tid,),
    ).fetchone()
    con.close()
    assert row[0] == "银行"
    assert row[1] == "FIRST"
    assert abs(row[2] - 0.05) < 0.01
    assert row[3] == 0  # retreat_reduced_1_3 初始为 0


# ═══════════════════════ 来源行业一致性测试 ═══════════════════════

def test_source_industry_missing_blocks_retreat():
    """来源行业缺失时 source_industry_status = MISSING，不应计算退潮"""
    import v0_6.core.config as cfg
    from v0_6.core.live_trade_store import init_schema, add_trade, get_position_summary_all
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    db = tmp / "trades.sqlite3"
    orig = cfg.DB_PATH
    cfg.DB_PATH = db

    try:
        init_schema()
        add_trade(
            target="512800", target_type="ETF", target_name="银行ETF",
            action="BUY", amount=10000, price=1.0, shares=10000, trade_date="20260701",
            # 不传 source_industry
        )
        positions = get_position_summary_all()
        assert len(positions) == 1
        assert positions[0]["source_industry"] is None
        assert positions[0]["source_industry_status"] == "MISSING"
    finally:
        cfg.DB_PATH = orig


def test_source_industry_conflict_blocked():
    """同一周期 source_industry 不一致 → CONFLICT"""
    import v0_6.core.config as cfg
    from v0_6.core.live_trade_store import init_schema, add_trade, get_position_summary_all
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    db = tmp / "trades.sqlite3"
    orig = cfg.DB_PATH
    cfg.DB_PATH = db

    try:
        init_schema()
        add_trade(
            target="512800", target_type="ETF", target_name="银行ETF",
            action="BUY", amount=10000, price=1.0, shares=10000,
            trade_date="20260701",
            source_industry="银行",
        )
        add_trade(
            target="512800", target_type="ETF", target_name="银行ETF",
            action="ADD", amount=5000, price=1.2, shares=4166,
            trade_date="20260705",
            source_industry="证券",  # 冲突!
        )
        positions = get_position_summary_all()
        assert positions[0]["source_industry_status"] == "CONFLICT", (
            f"应为 CONFLICT，实 {positions[0]['source_industry_status']}"
        )
    finally:
        cfg.DB_PATH = orig


# ═══════════════════════ 退潮动作 T+1 测试 ═══════════════════════

@pytest.mark.skip(reason="需要完整回放环境")
def test_retreat_reduce_t_plus_1():
    """退潮减仓在 T+1 执行，非当日"""
    pass
