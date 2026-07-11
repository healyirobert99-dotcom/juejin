"""v1.1 最小验收修复测试

新增：雷达持久化、交易日连续、案例去重、信号日期过滤、表单来源行业
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd


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
                "breadth_ma20": 0.35 + 0.01 * j,
                "breadth_ma60": 0.30,
                "avg_vol_ratio": 1.0,
                "avg_ret1": np.random.normal(0, 0.01),
                "avg_ret5": 0.01 * j,
                "avg_ret20": 0.03 * j,
                "median_ret20": 0.02 * j,
            })
    return pd.DataFrame(rows)


# ── 雷达持久化 ──

def test_radar_state_csv_persistence(monkeypatch):
    """radar_state.csv 持久化连续天数"""
    from v0_6.core.observation_tracker import (
        compute_radar_streak, _read_radar_state, _radar_state_path,
    )
    import v0_6.core.config as cfg
    tmp = Path(tempfile.mkdtemp())
    monkeypatch.setattr(cfg, "DATA_DIR", tmp / "data")

    ind_daily = _make_industry_daily(days=10)
    dates = sorted(ind_daily["trade_date"].unique().tolist())

    # Day 1
    cands = [{"industry": "测试行业-persist", "breadth": 0.42, "vol_ratio": 1.05,
               "avg_ret20": -0.05, "missing": ["20 日收益"]}]
    e1 = compute_radar_streak(cands, dates[0], ind_daily)
    assert e1[0]["consecutive_radar_days"] == 1

    state = _read_radar_state()
    assert "测试行业-persist" in state["industry"].values

    # Day 2 (consecutive)
    cands2 = [{"industry": "测试行业-persist", "breadth": 0.43, "vol_ratio": 1.06,
                "avg_ret20": -0.03, "missing": ["20 日收益"]}]
    e2 = compute_radar_streak(cands2, dates[1], ind_daily)
    assert e2[0]["consecutive_radar_days"] == 2


def test_consecutive_3_days_only_one_case(monkeypatch):
    """同一行业连续 3 个交易日进入雷达 → signal_cases.csv 只创建 1 条案例"""
    from v0_6.core.observation_tracker import (
        compute_radar_streak, update_signal_cases, _read_cases,
    )
    import v0_6.core.config as cfg
    tmp = Path(tempfile.mkdtemp())
    monkeypatch.setattr(cfg, "DATA_DIR", tmp / "data")

    ind_daily = _make_industry_daily(days=10)
    dates = sorted(ind_daily["trade_date"].unique().tolist())

    industry = "测试-唯一案例"

    for i, d in enumerate(dates[:3]):
        cands = [{"industry": industry, "breadth": 0.42 + 0.01 * i, "vol_ratio": 1.05,
                   "avg_ret20": -0.05 + 0.01 * i, "missing": ["20 日收益"]}]
        enhanced = compute_radar_streak(cands, d, ind_daily)
        update_signal_cases(enhanced, pd.DataFrame(), ind_daily, d)

    cases = _read_cases()
    ind_cases = cases[cases["industry"] == industry]
    assert len(ind_cases) == 1, f"连续 3 天应只创建 1 条案例，实际 {len(ind_cases)}"
    assert int(float(ind_cases.iloc[0]["radar_streak_max"])) >= 3


def test_streak_reset_creates_new_case(monkeypatch):
    """雷达中断后重新进入 → 创建新案例"""
    from v0_6.core.observation_tracker import (
        compute_radar_streak, update_signal_cases, _read_cases,
    )
    import v0_6.core.config as cfg
    tmp = Path(tempfile.mkdtemp())
    monkeypatch.setattr(cfg, "DATA_DIR", tmp / "data")

    ind_daily = _make_industry_daily(days=10)
    dates = sorted(ind_daily["trade_date"].unique().tolist())

    industry = "测试-中断"

    # 第 1 天
    cands = [{"industry": industry, "breadth": 0.42, "vol_ratio": 1.05,
               "avg_ret20": -0.05, "missing": ["20 日收益"]}]
    enhanced = compute_radar_streak(cands, dates[0], ind_daily)
    update_signal_cases(enhanced, pd.DataFrame(), ind_daily, dates[0])

    # 第 2 天中断（不在雷达）
    compute_radar_streak([], dates[1], ind_daily)

    # 第 3 天重新进入（中间跳过 dates[1]，不连续）
    cands3 = [{"industry": industry, "breadth": 0.44, "vol_ratio": 1.06,
                "avg_ret20": -0.02, "missing": ["20 日收益"]}]
    enhanced3 = compute_radar_streak(cands3, dates[2], ind_daily)
    assert enhanced3[0]["consecutive_radar_days"] == 1
    update_signal_cases(enhanced3, pd.DataFrame(), ind_daily, dates[2])

    cases = _read_cases()
    ind_cases = cases[cases["industry"] == industry]
    assert len(ind_cases) == 2, f"中断后重新进入应创建新案例，实际 {len(ind_cases)}"


# ── 信号日期过滤 ──

def test_only_today_signals_update_formal_date(monkeypatch):
    """today_signals 含 T-2/T-1/T，只有 T 的信号更新 formal_signal_date"""
    from v0_6.core.observation_tracker import (
        compute_radar_streak, update_signal_cases, _read_cases,
    )
    import v0_6.core.config as cfg
    tmp = Path(tempfile.mkdtemp())
    monkeypatch.setattr(cfg, "DATA_DIR", tmp / "data")

    ind_daily = _make_industry_daily(days=10)
    dates = sorted(ind_daily["trade_date"].unique().tolist())
    industry = "测试-信号过滤"

    # 创建雷达案例
    cands = [{"industry": industry, "breadth": 0.42, "vol_ratio": 1.05,
               "avg_ret20": -0.05, "missing": ["20 日收益"]}]
    enhanced = compute_radar_streak(cands, dates[0], ind_daily)
    update_signal_cases(enhanced, pd.DataFrame(), ind_daily, dates[0])

    # 模拟 today_signals 含 3 天数据
    all_signals = pd.DataFrame([
        {"signal_date": dates[2], "industry": industry, "priority": "FIRST"},
        {"signal_date": dates[0], "industry": industry, "priority": "FIRST"},  # 旧信号
        {"signal_date": dates[1], "industry": industry, "priority": "REPEAT"},  # 旧信号
    ])
    all_signals["signal_date"] = pd.to_datetime(all_signals["signal_date"])

    update_signal_cases(enhanced, all_signals, ind_daily, dates[2])

    cases = _read_cases()
    ind_cases = cases[cases["industry"] == industry]
    row = ind_cases.iloc[0]
    assert row["formal_triggered"] == "1"
    # formal_signal_date 必须是 dates[2]，不是 dates[0] 或 dates[1]
    assert row["formal_signal_date"] == dates[2], (
        f"formal_signal_date 应为 {dates[2]}，实际 {row['formal_signal_date']}"
    )


# ── BUY 来源行业前端 ──

def test_buy_payload_saves_source_industry(monkeypatch):
    """BUY payload 包含 source_industry 字段"""
    from v0_6.core.live_trade_store import init_schema, add_trade
    import v0_6.core.config as cfg
    import v0_6.core.live_trade_store as lts
    tmp = Path(tempfile.mkdtemp())
    db = tmp / "test.sqlite3"
    monkeypatch.setattr(cfg, "DB_PATH", db)
    monkeypatch.setattr(lts, "DB_PATH", db)

    init_schema()
    tid = add_trade(
        target="513780", target_type="ETF", target_name="银行ETF",
        action="BUY", amount=8000, price=1.356, shares=5900,
        trade_date="20260703",
        source_industry="证券",
        source_signal_date="20260703",
        source_signal_priority="FIRST",
        source_signal_type="STABILIZING_B",
    )
    assert tid > 0

    import sqlite3
    con = sqlite3.connect(str(db))
    row = con.execute(
        "SELECT source_industry, source_signal_priority, source_signal_type FROM live_trades WHERE trade_id=?",
        (tid,),
    ).fetchone()
    con.close()
    assert row[0] == "证券"
    assert row[1] == "FIRST"
    assert row[2] == "STABILIZING_B"


def test_buy_missing_source_industry_still_saves(monkeypatch):
    """BUY 未填写 source_industry 仍能保存，状态为 MISSING"""
    from v0_6.core.live_trade_store import init_schema, add_trade, get_position_summary_all
    import v0_6.core.config as cfg
    import v0_6.core.live_trade_store as lts
    tmp = Path(tempfile.mkdtemp())
    db = tmp / "test.sqlite3"
    monkeypatch.setattr(cfg, "DB_PATH", db)
    monkeypatch.setattr(lts, "DB_PATH", db)

    init_schema()
    add_trade(
        target="513780", target_type="ETF", target_name="银行ETF",
        action="BUY", amount=8000, price=1.356, shares=5900,
        trade_date="20260703",
    )
    pos = get_position_summary_all()
    assert len(pos) > 0
    assert pos[0]["source_industry_status"] == "MISSING"


def test_html_has_source_industry_input(monkeypatch):
    """前端 HTML 包含 source_industry 输入字段"""
    # 读取 trade_form_server.py 的 HTML 模板
    server_path = Path(__file__).parent.parent / "scripts" / "trade_form_server.py"
    html = server_path.read_text(encoding="utf-8")
    assert "source_industry" in html
    assert "f-source-industry" in html
    assert "来源行业" in html
    assert "source-section" in html


# ── 一致性 ──

def test_v1_1_signals_still_unchanged():
    """雷达持久化不修改正式信号逻辑"""
    from v0_6.core.observation_tracker import compute_radar_streak, RADAR_WATCH_CONFIG
    assert RADAR_WATCH_CONFIG["strong_min_streak"] == 2
    assert RADAR_WATCH_CONFIG["ret20_near_gap"] == 0.01
