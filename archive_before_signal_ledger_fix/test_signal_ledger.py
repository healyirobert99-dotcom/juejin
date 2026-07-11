"""v1.2 信号生命周期账本单元测试

覆盖：
- signal_id 格式（YYYYMMDD_行业名_序号）
- 雷达 → 正式信号 的 signal_id 关联
- 开放持仓 → HOLD 状态
- 退潮确认 → CLOSED（close_reason=退潮信号出现）
- D+1/D+5/D+20 收益/宽度计算
- ETF 可执行性 trade_available YES/NO

不修改四因子/交易规则，仅验证记录能力。
"""
from __future__ import annotations

from pathlib import Path
import tempfile

import pandas as pd
import pytest


def _make_industry_daily(days: int = 30) -> pd.DataFrame:
    dates = (
        pd.date_range("2026-07-01", periods=days, freq="B")
        .strftime("%Y-%m-%d").tolist()
    )
    rows = []
    for i, d in enumerate(dates):
        rows.append({
            "trade_date": d, "industry": "医疗保健", "n": 30,
            "breadth_ma20": round(0.5 + 0.01 * i, 4), "breadth_ma60": 0.5,
            "avg_ret1": 0.005, "avg_ret5": 0.025, "avg_ret10": 0.05,
            "avg_ret20": round(0.1 + 0.002 * i, 4), "avg_ret60": 0.2,
            "median_ret20": 0.08, "vol_ratio_5_60": 1.1,
        })
        rows.append({
            "trade_date": d, "industry": "生物制药", "n": 30,
            "breadth_ma20": 0.55, "breadth_ma60": 0.5,
            "avg_ret1": 0.004, "avg_ret5": 0.02, "avg_ret10": 0.04,
            "avg_ret20": 0.12, "avg_ret60": 0.22,
            "median_ret20": 0.1, "vol_ratio_5_60": 1.2,
        })
    return pd.DataFrame(rows)


def _radar(industry: str) -> list[dict]:
    return [{
        "industry": industry, "breadth": 0.4, "vol_ratio": 1.05,
        "avg_ret20": -0.02, "missing": ["20日收益"],
        "consecutive_radar_days": 1, "watch_level": "WATCH",
        "factor_pass_count": 3, "factor_total": 4,
    }]


def _signal(industry: str, date: str) -> pd.DataFrame:
    return pd.DataFrame([{
        "signal_date": date, "industry": industry, "priority": "FIRST",
        "breadth_at_signal": 0.4, "vol_ratio_at_signal": 1.05,
        "avg_ret20_at_signal": 0.1, "n_low_in_past_60d": 40,
    }])


@pytest.fixture
def tmp_data_dir(monkeypatch):
    from v0_6.core import config
    tmp = Path(tempfile.mkdtemp())
    # 关键：DATA_DIR 指向 tmp/data，使 DATA_DIR.parent(=tmp) 唯一，
    # 避免 reports/observation/signal_ledger.csv 落到共享 Temp 目录导致跨测试污染
    monkeypatch.setattr(config, "DATA_DIR", tmp / "data")
    return tmp


def test_signal_id_format_and_radar_trigger_assoc(tmp_data_dir):
    from v0_6.core.signal_ledger import update_signal_ledger
    ind = _make_industry_daily(30)
    dates = ind["trade_date"].unique().tolist()
    today = dates[0]

    df1 = update_signal_ledger(_radar("医疗保健"), pd.DataFrame(), ind, today,
                               open_positions=[])
    row = df1[df1["industry"] == "医疗保健"].iloc[0]
    # 格式 YYYYMMDD_行业名_序号
    assert row["signal_id"] == f"{today.replace('-', '')}_医疗保健_001"
    assert row["current_status"] == "RADAR"

    # 次日正式触发 → signal_id 应复用（关联）
    today2 = dates[1]
    df2 = update_signal_ledger([], _signal("医疗保健", today2), ind, today2,
                               open_positions=[])
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
                              open_positions=open_pos)
    row = df[df["industry"] == "医疗保健"].iloc[0]
    assert row["current_status"] == "HOLD"


def test_closed_on_retreat(tmp_data_dir, monkeypatch):
    from v0_6.core import signal_ledger
    # 退潮横截面：医疗保健确认退潮
    monkeypatch.setattr(signal_ledger, "_retreat_map",
                        lambda *a, **k: {"医疗保健": "CONFIRMED_RETREAT"})
    ind = _make_industry_daily(30)
    today = ind["trade_date"].unique().tolist()[0]
    df = signal_ledger.update_signal_ledger([], _signal("医疗保健", today), ind, today,
                              open_positions=[])
    row = df[df["industry"] == "医疗保健"].iloc[0]
    assert row["current_status"] == "CLOSED"
    assert row["close_reason"] == "退潮信号出现"


def test_d_outcome_computed(tmp_data_dir):
    from v0_6.core.signal_ledger import update_signal_ledger
    ind = _make_industry_daily(30)
    today = ind["trade_date"].unique().tolist()[0]
    df = update_signal_ledger([], _signal("医疗保健", today), ind, today,
                              open_positions=[])
    row = df[df["industry"] == "医疗保健"].iloc[0]
    # 触发后 D+1/D+5/D+20 收益与宽度均可计算
    assert row["d1_ret"] not in ("", None)
    assert row["d5_ret"] not in ("", None)
    assert row["d20_ret"] not in ("", None)
    assert float(row["d5_breadth"]) > 0
    # D+1 收益应约为单日 avg_ret1（≈0.5%）
    assert abs(float(row["d1_ret"]) - 0.005) < 1e-6


def test_trade_available_yes_and_no(tmp_data_dir, monkeypatch):
    from v0_6.core import etf_matcher
    from v0_6.core.signal_ledger import update_signal_ledger

    def fake_match(industry, top_n=2):
        if industry == "生物制药":
            return {"industry": industry,
                    "best": {"code": "513780.SH", "name": "创新药ETF"},
                    "matches": [], "match_type": "exact",
                    "confidence": 0.95, "note": ""}
        return {"industry": industry, "best": None, "matches": [],
                "match_type": "none", "confidence": 0.0, "note": "ETF映射缺失"}

    monkeypatch.setattr(etf_matcher, "match_industry_to_etfs", fake_match)
    ind = _make_industry_daily(30)
    today = ind["trade_date"].unique().tolist()[0]
    df = update_signal_ledger(
        _radar("生物制药") + _radar("冷门行业"), pd.DataFrame(), ind, today,
        open_positions=[],
    )
    bp = df[df["industry"] == "生物制药"].iloc[0]
    assert bp["trade_available"] == "YES"
    assert bp["etf_code"] == "513780.SH"
    cm = df[df["industry"] == "冷门行业"].iloc[0]
    assert cm["trade_available"] == "NO"


def test_idempotent_rerun_no_duplicate(tmp_data_dir):
    from v0_6.core.signal_ledger import update_signal_ledger
    ind = _make_industry_daily(30)
    today = ind["trade_date"].unique().tolist()[0]
    df1 = update_signal_ledger(_radar("医疗保健"), pd.DataFrame(), ind, today,
                               open_positions=[])
    n1 = len(df1)
    # 同日重跑
    df2 = update_signal_ledger(_radar("医疗保健"), pd.DataFrame(), ind, today,
                               open_positions=[])
    assert len(df2) == n1
    assert df2[df2["industry"] == "医疗保健"].iloc[0]["signal_id"] == \
        df1[df1["industry"] == "医疗保健"].iloc[0]["signal_id"]
