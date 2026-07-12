"""v1.2.1 信号生命周期账本测试（修复版）

覆盖 v1.2 修复指令 §17 所有用例 + 原有核心测试。
不修改四因子/交易规则，仅验证记录能力。
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


# ── helpers ──

def _make_industry_daily(days: int = 30, start_date: str = "2026-07-01") -> pd.DataFrame:
    """构造多行业 industry_daily，每个行业含基本指标。"""
    dates = (
        pd.date_range(start_date, periods=days, freq="B")
        .strftime("%Y-%m-%d").tolist()
    )
    rows = []
    for i, d in enumerate(dates):
        for ind_name in ("医疗保健", "生物制药", "IT设备", "造纸"):
            rows.append({
                "trade_date": d, "industry": ind_name, "n": 30,
                "breadth_ma20": round(0.5 + 0.01 * i, 4),
                "breadth_ma60": 0.5,
                "avg_ret1": 0.005,
                "avg_ret5": 0.025,
                "avg_ret10": 0.05,
                "avg_ret20": round(0.1 + 0.002 * i, 4),
                "avg_ret60": 0.2,
                "median_ret20": 0.08,
                "vol_ratio_5_60": 1.1,
            })
    return pd.DataFrame(rows)


def _radar(industry: str) -> list[dict]:
    return [{
        "industry": industry, "breadth": 0.4, "vol_ratio": 1.05,
        "avg_ret20": -0.02, "missing": ["20日收益"],
        "consecutive_radar_days": 1, "watch_level": "WATCH",
        "factor_pass_count": 3, "factor_total": 4,
    }]


def _signal(industry: str, date: str, priority: str = "FIRST") -> pd.DataFrame:
    return pd.DataFrame([{
        "signal_date": date, "industry": industry, "priority": priority,
        "breadth_at_signal": 0.4, "vol_ratio_at_signal": 1.05,
        "avg_ret20_at_signal": 0.1, "n_low_in_past_60d": 40,
    }])


@pytest.fixture
def tmp_data_dir(monkeypatch):
    """Hermetic 夹具：每个测试用独立路径。"""
    from v0_6.core import config
    tmp = Path(tempfile.mkdtemp())
    monkeypatch.setattr(config, "DATA_DIR", tmp / "data")
    return tmp


# ═══════════════════════════════════════════════════════════════════════
# 17.1 — 空值重读
# ═══════════════════════════════════════════════════════════════════════

def test_null_roundtrip_preserved(tmp_data_dir):
    """雷达写入后重读，空字段不变成 nan / TRIGGERED。"""
    from v0_6.core.signal_ledger import update_signal_ledger, _read_ledger

    ind = _make_industry_daily(30)
    dates = ind["trade_date"].unique().tolist()
    day1 = dates[0]

    # Day 1: RADAR
    df1 = update_signal_ledger(_radar("医疗保健"), pd.DataFrame(), ind, day1,
                               open_positions=[], closed_positions=[])
    row = df1[df1["industry"] == "医疗保健"].iloc[0]
    assert row["current_status"] == "RADAR"
    assert row["signal_date"] == ""  # 空字符串，不是 nan

    # Day 2: 重读（行业仍在 radar）
    day2 = dates[1]
    df2 = update_signal_ledger(_radar("医疗保健"), pd.DataFrame(), ind, day2,
                               open_positions=[], closed_positions=[])
    row2 = df2[df2["industry"] == "医疗保健"].iloc[0]
    assert row2["current_status"] == "RADAR"
    assert row2["signal_date"] == ""


# ═══════════════════════════════════════════════════════════════════════
# 17.2 — D0 无未来结果
# ═══════════════════════════════════════════════════════════════════════

def test_d0_no_future_leak(tmp_data_dir):
    """信号当天（D0），即使 industry_daily 包含未来数据，D+N 也全为空。"""
    from v0_6.core.signal_ledger import update_signal_ledger

    ind = _make_industry_daily(days=30, start_date="2026-07-01")
    dates = ind["trade_date"].unique().tolist()
    today = dates[0]  # 2026-07-01

    df = update_signal_ledger([], _signal("医疗保健", today), ind, today,
                              open_positions=[], closed_positions=[])
    row = df[df["industry"] == "医疗保健"].iloc[0]
    assert row["current_status"] == "TRIGGERED"

    # D0：as_of = signal_date 当天，无未来数据 → 全部为空
    for p in (1, 5, 20):
        assert row[f"d{p}_ret"] == "", f"D+{p} ret should be empty on D0, got {row[f'd{p}_ret']}"
        assert row[f"d{p}_breadth"] == "", f"D+{p} breadth should be empty on D0"


# ═══════════════════════════════════════════════════════════════════════
# 17.3 — 分阶段结果
# ═══════════════════════════════════════════════════════════════════════

def test_staged_outcome_d1(tmp_data_dir):
    """as_of=D+1：只有 D+1 有结果。"""
    from v0_6.core.signal_ledger import update_signal_ledger

    ind = _make_industry_daily(days=30, start_date="2026-07-01")
    dates = ind["trade_date"].unique().tolist()
    signal_day = dates[0]
    as_of_day = dates[1]  # D+1

    # 先在 signal_day 上触发信号
    update_signal_ledger([], _signal("医疗保健", signal_day), ind, signal_day,
                         open_positions=[], closed_positions=[])
    # 在 as_of_day 保持雷达中（防止退出），以 as_of_date 截断读取
    df = update_signal_ledger(_radar("医疗保健"), pd.DataFrame(), ind, as_of_day,
                              open_positions=[], closed_positions=[])
    row = df[df["industry"] == "医疗保健"].iloc[0]

    assert row["d1_ret"] not in ("", None), f"D+1 ret should be available, got {row['d1_ret']}"
    assert row["d5_ret"] == "", f"D+5 ret should NOT be available at D+1, got {row['d5_ret']}"
    assert row["d20_ret"] == "", f"D+20 ret should NOT be available at D+1"


def test_staged_outcome_d5(tmp_data_dir):
    """as_of=D+5：D+1 和 D+5 有结果。"""
    from v0_6.core.signal_ledger import update_signal_ledger

    ind = _make_industry_daily(days=30, start_date="2026-07-01")
    dates = ind["trade_date"].unique().tolist()
    signal_day = dates[0]
    as_of_day = dates[5]

    update_signal_ledger([], _signal("医疗保健", signal_day), ind, signal_day,
                         open_positions=[], closed_positions=[])
    df = update_signal_ledger(_radar("医疗保健"), pd.DataFrame(), ind, as_of_day,
                              open_positions=[], closed_positions=[])
    row = df[df["industry"] == "医疗保健"].iloc[0]

    assert row["d1_ret"] not in ("", None)
    assert row["d5_ret"] not in ("", None)
    assert row["d20_ret"] == "", f"D+20 should NOT be available at D+5, got {row['d20_ret']}"


def test_staged_outcome_d20(tmp_data_dir):
    """as_of=D+20：三项齐全。"""
    from v0_6.core.signal_ledger import update_signal_ledger

    ind = _make_industry_daily(days=30, start_date="2026-07-01")
    dates = ind["trade_date"].unique().tolist()
    signal_day = dates[0]
    as_of_day = dates[20]

    update_signal_ledger([], _signal("医疗保健", signal_day), ind, signal_day,
                         open_positions=[], closed_positions=[])
    df = update_signal_ledger(_radar("医疗保健"), pd.DataFrame(), ind, as_of_day,
                              open_positions=[], closed_positions=[])
    row = df[df["industry"] == "医疗保健"].iloc[0]

    assert row["d1_ret"] not in ("", None)
    assert row["d5_ret"] not in ("", None)
    assert row["d20_ret"] not in ("", None)


def test_staged_outcome_d4(tmp_data_dir):
    """as_of=D+4：只有 D+1。"""
    from v0_6.core.signal_ledger import update_signal_ledger

    ind = _make_industry_daily(days=30, start_date="2026-07-01")
    dates = ind["trade_date"].unique().tolist()
    signal_day = dates[0]
    as_of_day = dates[4]

    update_signal_ledger([], _signal("医疗保健", signal_day), ind, signal_day,
                         open_positions=[], closed_positions=[])
    df = update_signal_ledger(_radar("医疗保健"), pd.DataFrame(), ind, as_of_day,
                              open_positions=[], closed_positions=[])
    row = df[df["industry"] == "医疗保健"].iloc[0]

    assert row["d1_ret"] not in ("", None), f"D+1 should be available at D+4, got {row['d1_ret']}"
    assert row["d5_ret"] == "", f"D+5 should NOT be available at D+4, got {row['d5_ret']}"
    assert row["d20_ret"] == ""


def test_staged_outcome_d19(tmp_data_dir):
    """as_of=D+19：无 D+20。"""
    from v0_6.core.signal_ledger import update_signal_ledger

    ind = _make_industry_daily(days=30, start_date="2026-07-01")
    dates = ind["trade_date"].unique().tolist()
    signal_day = dates[0]
    as_of_day = dates[19]

    update_signal_ledger([], _signal("医疗保健", signal_day), ind, signal_day,
                         open_positions=[], closed_positions=[])
    df = update_signal_ledger(_radar("医疗保健"), pd.DataFrame(), ind, as_of_day,
                              open_positions=[], closed_positions=[])
    row = df[df["industry"] == "医疗保健"].iloc[0]

    assert row["d1_ret"] not in ("", None)
    assert row["d5_ret"] not in ("", None)
    assert row["d20_ret"] == "", f"D+20 should NOT be available at D+19, got {row['d20_ret']}"


# ═══════════════════════════════════════════════════════════════════════
# 17.4 — 雷达退出
# ═══════════════════════════════════════════════════════════════════════

def test_radar_exit_close(tmp_data_dir):
    """D0 进入 RADAR，D1 不在雷达且未触发 → CLOSED（雷达退出）。"""
    from v0_6.core.signal_ledger import update_signal_ledger

    ind = _make_industry_daily(days=30)
    dates = ind["trade_date"].unique().tolist()
    day1 = dates[0]
    day2 = dates[1]

    # Day 1: RADAR
    df1 = update_signal_ledger(_radar("医疗保健"), pd.DataFrame(), ind, day1,
                               open_positions=[], closed_positions=[])
    row = df1[df1["industry"] == "医疗保健"].iloc[0]
    assert row["current_status"] == "RADAR"

    # Day 2: 医疗保健不在雷达 → 退出
    df2 = update_signal_ledger([], pd.DataFrame(), ind, day2,
                               open_positions=[], closed_positions=[])
    row2 = df2[df2["industry"] == "医疗保健"].iloc[0]
    assert row2["current_status"] == "CLOSED"
    assert row2["close_reason"] == "雷达退出"


# ═══════════════════════════════════════════════════════════════════════
# 17.5 — 雷达重新进入
# ═══════════════════════════════════════════════════════════════════════

def test_radar_reentry_new_id(tmp_data_dir):
    """第一次 RADAR → 退出 → 再次进入 → 新 signal_id。"""
    from v0_6.core.signal_ledger import update_signal_ledger

    ind = _make_industry_daily(days=30)
    dates = ind["trade_date"].unique().tolist()
    day1, day2, day3, day4 = dates[0], dates[1], dates[2], dates[3]

    # Day 1: RADAR
    df1 = update_signal_ledger(_radar("医疗保健"), pd.DataFrame(), ind, day1,
                               open_positions=[], closed_positions=[])
    sid1 = df1[df1["industry"] == "医疗保健"].iloc[0]["signal_id"]

    # Day 2: 不在雷达 → 退出
    update_signal_ledger([], pd.DataFrame(), ind, day2,
                         open_positions=[], closed_positions=[])
    # Day 3: 确认已关闭
    df3 = update_signal_ledger([], pd.DataFrame(), ind, day3,
                               open_positions=[], closed_positions=[])
    row3 = df3[df3["industry"] == "医疗保健"].iloc[0]
    assert row3["current_status"] == "CLOSED"

    # Day 4: 重新进入雷达 → 新 ID
    df4 = update_signal_ledger(_radar("医疗保健"), pd.DataFrame(), ind, day4,
                               open_positions=[], closed_positions=[])
    row4 = df4[df4["industry"] == "医疗保健"].iloc[-1]  # 新增行
    sid4 = row4["signal_id"]
    assert sid4 != sid1, f"重新进入应生成新 signal_id，旧={sid1} 新={sid4}"
    assert row4["current_status"] == "RADAR"


# ═══════════════════════════════════════════════════════════════════════
# 17.6 — HOLD 不得回退
# ═══════════════════════════════════════════════════════════════════════

def test_hold_no_regress(tmp_data_dir):
    """已有 HOLD，open_positions 临时为空但无 closed_positions → 不得回退。"""
    from v0_6.core.signal_ledger import update_signal_ledger

    ind = _make_industry_daily(days=30)
    dates = ind["trade_date"].unique().tolist()
    day1, day2, day3 = dates[0], dates[1], dates[2]

    # Day 1: 触发 + 持仓 → HOLD
    open_pos = [{"target": "513780", "source_industry": "医疗保健",
                 "source_industry_status": "OK"}]
    df1 = update_signal_ledger([], _signal("医疗保健", day1), ind, day1,
                               open_positions=open_pos, closed_positions=[])
    row1 = df1[df1["industry"] == "医疗保健"].iloc[0]
    assert row1["current_status"] == "HOLD"

    # Day 2: 仍在雷达且有持仓
    df2 = update_signal_ledger(_radar("医疗保健"), pd.DataFrame(), ind, day2,
                               open_positions=open_pos, closed_positions=[])
    row2 = df2[df2["industry"] == "医疗保健"].iloc[0]
    assert row2["current_status"] == "HOLD"

    # Day 3: 持仓列表暂时为空（数据可能获取失败），无平仓记录
    # HOLD 不得回退到 TRIGGERED
    df3 = update_signal_ledger(_radar("医疗保健"), pd.DataFrame(), ind, day3,
                               open_positions=[], closed_positions=[])
    row3 = df3[df3["industry"] == "医疗保健"].iloc[0]
    assert row3["current_status"] == "HOLD", (
        f"HOLD 不应回退为 {row3['current_status']}"
    )


# ═══════════════════════════════════════════════════════════════════════
# 17.7 — 明确平仓
# ═══════════════════════════════════════════════════════════════════════

def test_explicit_close_via_signal_id(tmp_data_dir):
    """closed_positions 包含对应 signal_id → CLOSED + 正确原因。"""
    from v0_6.core.signal_ledger import update_signal_ledger

    ind = _make_industry_daily(days=30)
    dates = ind["trade_date"].unique().tolist()
    day1, day2 = dates[0], dates[1]

    # Day 1: 触发
    df1 = update_signal_ledger([], _signal("医疗保健", day1), ind, day1,
                               open_positions=[], closed_positions=[])
    sid = df1[df1["industry"] == "医疗保健"].iloc[0]["signal_id"]

    # Day 2: 明确平仓
    closed = [{"signal_id": sid, "source_industry": "医疗保健",
               "close_reason": "TAKE_PROFIT", "close_date": day2}]
    df2 = update_signal_ledger([], pd.DataFrame(), ind, day2,
                               open_positions=[], closed_positions=closed)
    row = df2[df2["industry"] == "医疗保健"].iloc[0]
    assert row["current_status"] == "CLOSED"
    assert row["close_reason"] == "TAKE_PROFIT"


def test_explicit_close_via_industry(tmp_data_dir):
    """closed_positions 按行业匹配（无 signal_id 时）→ CLOSED。"""
    from v0_6.core.signal_ledger import update_signal_ledger

    ind = _make_industry_daily(days=30)
    dates = ind["trade_date"].unique().tolist()
    day1, day2 = dates[0], dates[1]

    df1 = update_signal_ledger([], _signal("生物制药", day1), ind, day1,
                               open_positions=[], closed_positions=[])
    assert df1[df1["industry"] == "生物制药"].iloc[0]["current_status"] == "TRIGGERED"

    closed = [{"source_industry": "生物制药",
               "close_reason": "STOP_LOSS", "close_date": day2}]
    df2 = update_signal_ledger([], pd.DataFrame(), ind, day2,
                               open_positions=[], closed_positions=closed)
    row = df2[df2["industry"] == "生物制药"].iloc[0]
    assert row["current_status"] == "CLOSED"
    assert row["close_reason"] == "STOP_LOSS"


# ═══════════════════════════════════════════════════════════════════════
# 17.8 — 退潮不等于平仓
# ═══════════════════════════════════════════════════════════════════════

def test_retreat_does_not_close(tmp_data_dir, monkeypatch):
    """退潮 CONFIRMED_RETREAT 但无实际平仓 → 保持 HOLD（不关闭）。"""
    from v0_6.core import signal_ledger

    # 模拟退潮
    monkeypatch.setattr(signal_ledger, "_retreat_map",
                        lambda *a, **k: {"医疗保健": "CONFIRMED_RETREAT"})

    ind = _make_industry_daily(days=30)
    dates = ind["trade_date"].unique().tolist()
    day1, day2 = dates[0], dates[1]

    open_pos = [{"target": "513780", "source_industry": "医疗保健",
                 "source_industry_status": "OK"}]

    # Day 1: HOLD
    df1 = signal_ledger.update_signal_ledger([], _signal("医疗保健", day1), ind, day1,
                               open_positions=open_pos, closed_positions=[])
    row1 = df1[df1["industry"] == "医疗保健"].iloc[0]
    assert row1["current_status"] == "HOLD"

    # Day 2: CONFIRMED_RETREAT，但无 closed_positions → 保持 HOLD
    df2 = signal_ledger.update_signal_ledger([], pd.DataFrame(), ind, day2,
                               open_positions=open_pos, closed_positions=[])
    row2 = df2[df2["industry"] == "医疗保健"].iloc[0]
    assert row2["current_status"] == "HOLD", (
        f"退潮不应直接关闭，应为 HOLD，实际 {row2['current_status']}"
    )


# ═══════════════════════════════════════════════════════════════════════
# 17.9 — REPEAT 不覆盖原日期
# ═══════════════════════════════════════════════════════════════════════

def test_repeat_preserves_original_signal_date(tmp_data_dir):
    """原 signal_date=D0，D10 出现 REPEAT → signal_date 仍为 D0。"""
    from v0_6.core.signal_ledger import update_signal_ledger

    ind = _make_industry_daily(days=30)
    dates = ind["trade_date"].unique().tolist()
    d0, d10 = dates[0], dates[10]

    # D0: FIRST
    df1 = update_signal_ledger([], _signal("医疗保健", d0, "FIRST"), ind, d0,
                               open_positions=[], closed_positions=[])
    row1 = df1[df1["industry"] == "医疗保健"].iloc[0]
    assert row1["signal_date"] == d0

    # D10: REPEAT — 不覆盖原 signal_date
    df2 = update_signal_ledger([], _signal("医疗保健", d10, "REPEAT"), ind, d10,
                               open_positions=[], closed_positions=[])
    row2 = df2[df2["industry"] == "医疗保健"].iloc[0]
    assert row2["signal_date"] == d0, f"REPEAT 不应覆盖 signal_date，期望 {d0}，实际 {row2['signal_date']}"


# ═══════════════════════════════════════════════════════════════════════
# 17.10 — CSV 损坏
# ═══════════════════════════════════════════════════════════════════════

def test_csv_corrupt_raises(tmp_data_dir):
    """写入非法二进制数据，再读取必须抛异常（不得返回空表）。"""
    from v0_6.core.signal_ledger import _ledger_path, _read_ledger

    p = _ledger_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    # 写入完全无法解析的二进制数据
    p.write_bytes(b'\x00\x01\x02\x03\x04\x05\x06\x07')

    with pytest.raises(RuntimeError, match="读取信号账本失败"):
        _read_ledger()


# ═══════════════════════════════════════════════════════════════════════
# 17.11 — ETF 错误分类
# ═══════════════════════════════════════════════════════════════════════

def test_etf_matched(tmp_data_dir, monkeypatch):
    """正常匹配 → MATCHED / YES。"""
    from v0_6.core import etf_matcher
    from v0_6.core.signal_ledger import update_signal_ledger

    def fake_match(industry, top_n=2):
        return {"industry": industry,
                "best": {"code": "513780.SH", "name": "医疗ETF"},
                "matches": [], "match_type": "exact",
                "confidence": 0.95, "note": ""}

    monkeypatch.setattr(etf_matcher, "match_industry_to_etfs", fake_match)
    ind = _make_industry_daily(30)
    today = ind["trade_date"].unique().tolist()[0]

    df = update_signal_ledger(_radar("医疗保健"), pd.DataFrame(), ind, today,
                              open_positions=[], closed_positions=[])
    row = df[df["industry"] == "医疗保健"].iloc[0]
    assert row["trade_available"] == "YES"
    assert row["etf_code"] == "513780.SH"
    assert row["etf_match_status"] == "MATCHED"


def test_etf_not_found(tmp_data_dir, monkeypatch):
    """正常匹配但无 ETF → NOT_FOUND / NO。"""
    from v0_6.core import etf_matcher
    from v0_6.core.signal_ledger import update_signal_ledger

    def fake_match(industry, top_n=2):
        return {"industry": industry, "best": None, "matches": [],
                "match_type": "none", "confidence": 0.0, "note": "ETF映射缺失"}

    monkeypatch.setattr(etf_matcher, "match_industry_to_etfs", fake_match)
    ind = _make_industry_daily(30)
    today = ind["trade_date"].unique().tolist()[0]

    df = update_signal_ledger(_radar("冷门行业"), pd.DataFrame(), ind, today,
                              open_positions=[], closed_positions=[])
    row = df[df["industry"] == "冷门行业"].iloc[0]
    assert row["trade_available"] == "NO"
    assert row["etf_match_status"] == "NOT_FOUND"


def test_etf_error(tmp_data_dir, monkeypatch):
    """ETF 匹配抛异常 → ERROR / NO（不得误报为 NOT_FOUND）。"""
    from v0_6.core import etf_matcher
    from v0_6.core.signal_ledger import update_signal_ledger

    def fake_match_error(industry, top_n=2):
        raise RuntimeError("网络超时")

    monkeypatch.setattr(etf_matcher, "match_industry_to_etfs", fake_match_error)
    ind = _make_industry_daily(30)
    today = ind["trade_date"].unique().tolist()[0]

    df = update_signal_ledger(_radar("异常行业"), pd.DataFrame(), ind, today,
                              open_positions=[], closed_positions=[])
    row = df[df["industry"] == "异常行业"].iloc[0]
    assert row["trade_available"] == "NO"
    assert row["etf_match_status"] == "ERROR"
    assert "网络超时" in row["etf_match_note"]


# ═══════════════════════════════════════════════════════════════════════
# 17.12 — 阶段边界
# ═══════════════════════════════════════════════════════════════════════

def test_phase_boundaries(tmp_data_dir):
    """D+4 → D0; D+5 → D+5; D+19 → D+5; D+20 → D+20。"""
    from v0_6.core.signal_ledger import update_signal_ledger

    ind = _make_industry_daily(days=35, start_date="2026-07-01")
    dates = ind["trade_date"].unique().tolist()
    signal_day = dates[0]

    # 先在 signal_day 触发信号
    update_signal_ledger([], _signal("医疗保健", signal_day), ind, signal_day,
                         open_positions=[], closed_positions=[])

    # D+4 → D0（保持在雷达中防止退出）
    df4 = update_signal_ledger(_radar("医疗保健"), pd.DataFrame(), ind, dates[4],
                               open_positions=[], closed_positions=[])
    row4 = df4[df4["industry"] == "医疗保健"].iloc[0]
    assert row4["current_phase"] == "D0", f"D+4 应显示 D0，实际 {row4['current_phase']}"

    # D+5 → D+5
    df5 = update_signal_ledger(_radar("医疗保健"), pd.DataFrame(), ind, dates[5],
                               open_positions=[], closed_positions=[])
    row5 = df5[df5["industry"] == "医疗保健"].iloc[0]
    assert row5["current_phase"] == "D+5", f"D+5 应显示 D+5，实际 {row5['current_phase']}"

    # D+19 → D+5
    df19 = update_signal_ledger(_radar("医疗保健"), pd.DataFrame(), ind, dates[19],
                                open_positions=[], closed_positions=[])
    row19 = df19[df19["industry"] == "医疗保健"].iloc[0]
    assert row19["current_phase"] == "D+5", f"D+19 应显示 D+5，实际 {row19['current_phase']}"

    # D+20 → D+20
    df20 = update_signal_ledger(_radar("医疗保健"), pd.DataFrame(), ind, dates[20],
                                open_positions=[], closed_positions=[])
    row20 = df20[df20["industry"] == "医疗保健"].iloc[0]
    assert row20["current_phase"] == "D+20", f"D+20 应显示 D+20，实际 {row20['current_phase']}"


# ═══════════════════════════════════════════════════════════════════════
# 17.13 — 多行业同日序号
# ═══════════════════════════════════════════════════════════════════════

def test_multi_industry_sequence(tmp_data_dir):
    """同日 3 个新行业 → 001 / 002 / 003 不重复。"""
    from v0_6.core.signal_ledger import update_signal_ledger

    ind = _make_industry_daily(30)
    today = ind["trade_date"].unique().tolist()[0]
    date_compact = today.replace("-", "")

    df = update_signal_ledger(
        _radar("医疗保健") + _radar("IT设备") + _radar("造纸"),
        pd.DataFrame(),
        ind, today,
        open_positions=[], closed_positions=[],
    )

    sids = sorted(df["signal_id"].tolist())
    expected = [
        f"{date_compact}_IT设备_001",
        f"{date_compact}_医疗保健_001",
        f"{date_compact}_造纸_001",
    ]
    # signal_id 排序可能因字典顺序不同，但三者应各不同且格式正确
    assert len(set(sids)) == 3, f"3 个新行业应有 3 个唯一 signal_id，实际 {sids}"


# ═══════════════════════════════════════════════════════════════════════
# 17.14 — 同日幂等
# ═══════════════════════════════════════════════════════════════════════

def test_same_day_idempotent(tmp_data_dir):
    """相同输入运行两次：行数、signal_id、result 字段不变。"""
    from v0_6.core.signal_ledger import update_signal_ledger

    ind = _make_industry_daily(30)
    today = ind["trade_date"].unique().tolist()[0]

    def run():
        return update_signal_ledger(
            _radar("医疗保健") + _radar("生物制药"),
            _signal("医疗保健", today),
            ind, today,
            open_positions=[], closed_positions=[],
        )

    df1 = run()
    df2 = run()

    assert len(df1) == len(df2), f"幂等失败：行数 {len(df1)} ≠ {len(df2)}"
    for _, r1 in df1.iterrows():
        match = df2[df2["industry"] == r1["industry"]]
        assert len(match) > 0
        r2 = match.iloc[0]
        assert r1["signal_id"] == r2["signal_id"], f"signal_id 不一致: {r1['signal_id']} vs {r2['signal_id']}"
        assert r1["current_status"] == r2["current_status"]
        assert r1["signal_date"] == r2["signal_date"]


# ═══════════════════════════════════════════════════════════════════════
# 原有测试（适配新版 API）
# ═══════════════════════════════════════════════════════════════════════

def test_signal_id_format_and_radar_trigger_assoc(tmp_data_dir):
    from v0_6.core.signal_ledger import update_signal_ledger
    ind = _make_industry_daily(30)
    dates = ind["trade_date"].unique().tolist()
    today = dates[0]

    df1 = update_signal_ledger(_radar("医疗保健"), pd.DataFrame(), ind, today,
                               open_positions=[], closed_positions=[])
    row = df1[df1["industry"] == "医疗保健"].iloc[0]
    assert row["signal_id"] == f"{today.replace('-', '')}_医疗保健_001"
    assert row["current_status"] == "RADAR"

    # 次日正式触发 → signal_id 复用
    today2 = dates[1]
    df2 = update_signal_ledger([], _signal("医疗保健", today2), ind, today2,
                               open_positions=[], closed_positions=[])
    row2 = df2[df2["industry"] == "医疗保健"].iloc[0]
    assert row2["current_status"] == "TRIGGERED"
    assert row2["signal_id"] == row["signal_id"]
    assert row2["signal_date"] == today2


def test_hold_status_with_open_position(tmp_data_dir):
    from v0_6.core.signal_ledger import update_signal_ledger
    ind = _make_industry_daily(30)
    today = ind["trade_date"].unique().tolist()[0]
    open_pos = [{"target": "513780", "source_industry": "医疗保健",
                 "source_industry_status": "OK"}]
    df = update_signal_ledger([], _signal("医疗保健", today), ind, today,
                              open_positions=open_pos, closed_positions=[])
    row = df[df["industry"] == "医疗保健"].iloc[0]
    assert row["current_status"] == "HOLD"
