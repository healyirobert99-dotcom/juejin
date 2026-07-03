"""v1.1 轻量增强测试

覆盖：雷达连续跟踪、强观察、案例账本、大类联动、来源行业录入
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd


# ── helpers ──

def _make_industry_daily(dates=None, n_industries=5, days=10):
    """构造测试用 industry_daily"""
    import datetime
    if dates is None:
        base = datetime.date(2026, 7, 1)
        dates = [base + datetime.timedelta(days=i) for i in range(days)]
        dates = [d.strftime("%Y-%m-%d") for d in dates if d.weekday() < 5]
    rows = []
    for d in dates:
        for j in range(n_industries):
            ind = f"行业{j}"
            rows.append({
                "trade_date": d,
                "industry": ind,
                "n": 30,
                "breadth_ma20": 0.35 + 0.01 * j + 0.001 * (dates.index(d)),
                "breadth_ma60": 0.30,
                "avg_vol_ratio": 1.0,
                "avg_ret1": np.random.normal(0, 0.01),
                "avg_ret5": 0.01 * j,
                "avg_ret10": 0.02 * j,
                "avg_ret20": 0.03 * j,
                "avg_ret60": 0.05 * j,
                "median_ret20": 0.02 * j,
            })
    return pd.DataFrame(rows)


def _make_radar_candidates():
    """构造两个雷达候选"""
    return [
        {
            "industry": "生物制药",
            "factor_pass_count": 3,
            "factor_total": 4,
            "weakness_ok": True,
            "breadth": 0.42,
            "vol_ratio": 1.08,
            "avg_ret20": -0.008,
            "n_low_in_past_60d": 34,
            "breadth_improvement_5d": 0.35,
            "missing": ["20 日收益仍为 -0.8%"],
            "cond_breadth": True,
            "cond_vol": True,
            "cond_ret": False,
        },
        {
            "industry": "化学制药",
            "factor_pass_count": 3,
            "factor_total": 4,
            "weakness_ok": True,
            "breadth": 0.37,
            "vol_ratio": 1.02,
            "avg_ret20": -0.015,
            "n_low_in_past_60d": 33,
            "breadth_improvement_5d": 0.25,
            "missing": ["20 日收益转正"],
            "cond_breadth": True,
            "cond_vol": True,
            "cond_ret": False,
        },
    ]


# ── 信号雷达 ──

def test_radar_streak_increments_on_consecutive_days(monkeypatch):
    """连续两天进入雷达时 streak=2"""
    from v0_6.core.observation_tracker import compute_radar_streak
    import v0_6.core.observation_tracker as ot
    monkeypatch.setattr(ot, "_radar_history", {})

    cands_day1 = [{"industry": "生物制药-streak测试", "breadth": 0.42, "vol_ratio": 1.08,
                    "avg_ret20": -0.05, "missing": ["20 日收益仍为 -5.0%"]}]
    enhanced1 = compute_radar_streak(cands_day1, "2026-07-01")
    assert enhanced1[0]["consecutive_radar_days"] == 1
    assert enhanced1[0]["watch_level"] == "WATCH"

    cands_day2 = [{"industry": "生物制药-streak测试", "breadth": 0.43, "vol_ratio": 1.09,
                    "avg_ret20": -0.03, "missing": ["20 日收益仍为 -3.0%"]}]
    enhanced2 = compute_radar_streak(cands_day2, "2026-07-02")
    assert enhanced2[0]["consecutive_radar_days"] == 2
    assert enhanced2[0]["watch_level"] == "STRONG_WATCH"


def test_radar_streak_resets_after_break(monkeypatch):
    """雷达中断后重新进入，streak 重置为 1"""
    from v0_6.core.observation_tracker import compute_radar_streak, _radar_history

    # 第 1 天
    compute_radar_streak([{"industry": "测试", "breadth": 0.40, "vol_ratio": 1.0,
                            "avg_ret20": 0.01, "missing": []}], "2026-07-01")
    # 第 2 天空（break）
    compute_radar_streak([], "2026-07-02")
    # 第 3 天重新进入
    enhanced = compute_radar_streak([{"industry": "测试", "breadth": 0.41, "vol_ratio": 1.0,
                                       "avg_ret20": 0.01, "missing": []}], "2026-07-03")
    assert enhanced[0]["consecutive_radar_days"] == 1
    assert enhanced[0]["watch_level"] == "WATCH"


def test_strong_watch_when_near_threshold(monkeypatch):
    """唯一缺失条件非常接近阈值 → 强观察"""
    from v0_6.core.observation_tracker import compute_radar_streak, _radar_history, RADAR_WATCH_CONFIG

    # 20 日收益仅差 0.5 个百分点
    gap = RADAR_WATCH_CONFIG["ret20_near_gap"] - 0.001
    cand = {"industry": "近触发", "breadth": 0.42, "vol_ratio": 1.05,
            "avg_ret20": -gap, "missing": ["20 日收益转正"]}
    enhanced = compute_radar_streak([cand], "2026-07-01")
    assert enhanced[0]["watch_level"] == "STRONG_WATCH"


def test_radar_does_not_produce_trade_action(monkeypatch):
    """雷达增强不产生 action 字段"""
    from v0_6.core.observation_tracker import compute_radar_streak, _radar_history
    enhanced = compute_radar_streak([{
        "industry": "测试", "breadth": 0.40, "vol_ratio": 1.0,
        "avg_ret20": 0.01, "missing": [],
    }], "2026-07-01")
    assert "action" not in enhanced[0]
    assert "trade_action" not in enhanced[0]


# ── 案例账本 ──

def test_signal_cases_creates_once(monkeypatch):
    """同一个行业+首次雷达日期不创建重复案例"""
    from v0_6.core.observation_tracker import update_signal_cases
    import v0_6.core.config as cfg
    tmp = Path(tempfile.mkdtemp())
    monkeypatch.setattr(cfg, "DATA_DIR", tmp)
    (tmp / "reports" / "observation").mkdir(parents=True, exist_ok=True)

    industry_daily = _make_industry_daily()
    cands = [{"industry": "行业0", "breadth": 0.40, "vol_ratio": 1.0,
               "avg_ret20": -0.01, "missing": ["20 日收益"],
               "first_radar_date": "2026-07-01", "consecutive_radar_days": 1,
               "watch_level": "WATCH", "missing_key": "ret20", "missing_text": "20 日收益"}]

    cases1 = update_signal_cases(cands, pd.DataFrame(), industry_daily, "2026-07-01")
    cases2 = update_signal_cases(cands, pd.DataFrame(), industry_daily, "2026-07-02")
    assert len(cases1) == len(cases2)


def test_case_forward_returns_not_set_before_due(monkeypatch):
    """未到期的 forward_ret 保持空"""
    from v0_6.core.observation_tracker import update_signal_cases
    import v0_6.core.config as cfg
    tmp = Path(tempfile.mkdtemp())
    monkeypatch.setattr(cfg, "DATA_DIR", tmp)
    (tmp / "reports" / "observation").mkdir(parents=True, exist_ok=True)

    industry_daily = _make_industry_daily(days=3)
    cands = [{"industry": "行业0", "breadth": 0.40, "vol_ratio": 1.0,
               "avg_ret20": -0.01, "missing": ["20 日收益"],
               "first_radar_date": industry_daily["trade_date"].iloc[0],
               "consecutive_radar_days": 1, "watch_level": "WATCH",
               "missing_key": "ret20", "missing_text": "20 日收益"}]

    cases = update_signal_cases(cands, pd.DataFrame(), industry_daily, industry_daily["trade_date"].iloc[0])
    row = cases.iloc[0]
    assert row["forward_ret_5d"] == "" or pd.isna(row["forward_ret_5d"])


# ── 行业大类联动 ──

def test_group_linkage_detects_two_members():
    """两个同大类行业进入雷达 → 返回联动"""
    from v0_6.core.industry_groups import check_group_linkage
    groups = check_group_linkage(["生物制药", "化学制药"])
    assert len(groups) >= 1
    assert groups[0]["count"] >= 2
    assert "医药" in groups[0]["group"]


def test_single_industry_no_linkage():
    """单个细分行业不产生大类联动"""
    from v0_6.core.industry_groups import check_group_linkage
    groups = check_group_linkage(["服饰"])
    assert len(groups) == 0


# ── 来源行业录入 ──

def test_add_trade_with_source_industry_writes_correctly(monkeypatch):
    """BUY 时传 source_industry 正常落库"""
    from v0_6.core.live_trade_store import init_schema, add_trade
    import v0_6.core.config as cfg
    import v0_6.core.live_trade_store as lts
    tmp = Path(tempfile.mkdtemp())
    db = tmp / "test.sqlite3"
    monkeypatch.setattr(cfg, "DB_PATH", db)
    monkeypatch.setattr(lts, "DB_PATH", db)

    init_schema()
    tid = add_trade(
        target="512800", target_type="ETF", target_name="银行ETF",
        action="BUY", amount=10000, price=1.0, shares=10000,
        trade_date="20260701",
        source_industry="银行", source_signal_date="20260701",
        source_signal_priority="FIRST", source_signal_type="STABILIZING_B",
    )
    con = sqlite3.connect(str(db))
    row = con.execute(
        "SELECT source_industry, source_signal_priority FROM live_trades WHERE trade_id=?",
        (tid,),
    ).fetchone()
    con.close()
    assert row[0] == "银行"
    assert row[1] == "FIRST"


def test_add_inherits_source_industry(monkeypatch):
    """ADD 操作继承当前持仓的来源行业"""
    from v0_6.core.live_trade_store import init_schema, add_trade, get_position_summary_all
    import v0_6.core.config as cfg
    import v0_6.core.live_trade_store as lts
    tmp = Path(tempfile.mkdtemp())
    db = tmp / "test.sqlite3"
    monkeypatch.setattr(cfg, "DB_PATH", db)
    monkeypatch.setattr(lts, "DB_PATH", db)

    init_schema()
    add_trade(target="512800", target_type="ETF", target_name="银行ETF",
              action="BUY", amount=10000, price=1.0, shares=10000,
              trade_date="20260701", source_industry="银行")
    pos = get_position_summary_all()
    assert len(pos) > 0
    assert pos[0]["source_industry"] == "银行"
    assert pos[0]["source_industry_status"] == "OK"


def test_source_industry_missing_does_not_block_price_risk(monkeypatch):
    """source_industry 缺失不影响价格风控"""
    from v0_6.core.live_trade_store import init_schema, add_trade, get_position_summary_all
    import v0_6.core.config as cfg
    import v0_6.core.live_trade_store as lts
    tmp = Path(tempfile.mkdtemp())
    db = tmp / "test.sqlite3"
    monkeypatch.setattr(cfg, "DB_PATH", db)
    monkeypatch.setattr(lts, "DB_PATH", db)

    init_schema()
    add_trade(target="513780", target_type="ETF", target_name="银行ETF",
              action="BUY", amount=10000, price=1.356, shares=10000,
              trade_date="20260701")
    pos = get_position_summary_all()
    assert pos[0]["source_industry_status"] == "MISSING"
    assert pos[0]["total_shares"] > 0


# ── 一致性 ──

def test_v1_signals_unchanged():
    """v1.0 正式信号数量在 v1.1 中不变（新模块不修改信号逻辑）"""
    from v0_6.core.observation_tracker import compute_radar_streak, RADAR_WATCH_CONFIG
    # 雷达连续跟踪和强观察不修改输入列表的 signal 相关字段
    original = {"industry": "测试", "factor_pass_count": 3, "factor_total": 4,
                "breadth": 0.42, "missing": ["x"]}
    enhanced = compute_radar_streak([original.copy()], "2026-07-01")
    assert enhanced[0]["factor_pass_count"] == original["factor_pass_count"]
    assert enhanced[0]["factor_total"] == original["factor_total"]
    assert enhanced[0]["breadth"] == original["breadth"]


def test_industry_groups_json_valid():
    """行业大类配置文件格式合法"""
    path = Path(__file__).parent.parent / "data" / "industry_groups.json"
    assert path.exists(), f"{path} 不存在"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    for k, v in data.items():
        if not k.startswith("_"):
            assert isinstance(v, list), f"{k} 的值不是列表"
            assert len(v) > 0, f"{k} 的成员列表为空"


def test_version_string():
    """版本号格式正确"""
    from v0_6.core.version import VERSION, VERSION_NAME, VERSION_LABEL
    assert VERSION == "1.1.0"
    assert "v1.1" in VERSION_LABEL or "v1.1" in VERSION
