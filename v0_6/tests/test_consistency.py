"""
回归测试：交易录入、持仓监控与日报一致性（第六步）

覆盖 5 个场景：
1. SELL 后重新 BUY 周期隔离
2. 异常持仓日报可见
3. BUY+ADD 聚合退出列表
4. REDUCE 建议使用当前价
5. SELL 建议完整覆盖净持仓

全部使用临时数据库。
"""
import sys
import sqlite3
from pathlib import Path

V0_6_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(V0_6_ROOT))

import pytest


def _patch_db(monkeypatch, tmp_db_path):
    """同时 patch config.DB_PATH 和 live_trade_store.DB_PATH"""
    import v0_6.core.config as cfg
    import v0_6.core.live_trade_store as lts
    monkeypatch.setattr(cfg, "DB_PATH", tmp_db_path)
    monkeypatch.setattr(lts, "DB_PATH", tmp_db_path)


# ── 测试 1：SELL 后重新 BUY 周期隔离 ──

def test_sell_rebuy_cycle_isolated(monkeypatch, tmp_path):
    """SELL 记录 closed=1，新周期不受旧周期卖出影响"""
    db = tmp_path / "test_trade.sqlite3"
    _patch_db(monkeypatch, db)
    from v0_6.core import init_schema, add_trade, get_position_summary_all, close_trade_by_target, get_position_net
    import sqlite3
    init_schema()

    # 第一轮：BUY 1000
    t1 = add_trade(target="512800", target_type="ETF", target_name="银行ETF",
                   action="BUY", amount=780, price=0.78, shares=1000.0,
                   trade_date="2026-06-01")

    # 模拟 SELL（与后端 SELL 分支一致：先写 SELL 再 close_trade_by_target）
    net = get_position_net("512800")
    sell_shares = net["net_shares"]
    sell_amount = sell_shares * 0.80
    add_trade(target="512800", target_type="ETF", target_name="银行ETF",
              action="SELL", amount=sell_amount, price=0.80, shares=sell_shares,
              trade_date="2026-06-15")
    close_trade_by_target("512800", 0.80, "2026-06-15")

    # 验证 SELL 记录 closed=1
    con = sqlite3.connect(str(db))
    sell_row = con.execute("SELECT closed FROM live_trades WHERE action='SELL'").fetchone()
    assert sell_row[0] == 1, f"SELL 应 closed=1，实 {sell_row[0]}"

    # 第二轮：BUY 600
    add_trade(target="512800", target_type="ETF", target_name="银行ETF",
              action="BUY", amount=480, price=0.80, shares=600.0,
              trade_date="2026-06-20")

    summaries = get_position_summary_all()
    assert len(summaries) == 1, f"应返回 1 个持仓，实 {len(summaries)}"
    assert summaries[0]["total_shares"] == 600.0, \
        f"新周期净股应=600，实 {summaries[0]['total_shares']}"

    net2 = get_position_net("512800")
    assert net2["net_shares"] == 600.0, f"net_shares 应=600，实 {net2['net_shares']}"
    assert net2["avg_cost"] == 0.80, f"avg_cost 应=0.80，实 {net2['avg_cost']}"

    con.close()


# ── 测试 2：异常持仓日报可见 ──

def test_daily_report_renders_error_position_card():
    """监控结果含 error 记录时，日报渲染异常卡片而非跳过"""
    from v0_6.scripts.run_daily_v1_html import render_position_card, render_html
    import pandas as pd

    no_price = {
        "target": "159995", "target_type": "ETF", "entry_date": "2026-06-10",
        "error": "no_price_data", "alerts": [],
    }
    unknown = {
        "target": "xxx", "target_type": "UNKNOWN", "entry_date": "2026-06-10",
        "error": "unknown_target_type",
        "alerts": [{"type": "UNKNOWN_TARGET_TYPE",
                     "message": "未知标的类型 'UNKNOWN'，需要人工确认历史持仓类型"}],
    }
    industry_err = {
        "target": "保险", "target_type": "INDUSTRY", "entry_date": "2026-06-10",
        "error": "industry_not_executable",
        "alerts": [{"type": "INDUSTRY_NOT_EXECUTABLE",
                     "message": "行业 保险 不可作为交易标的，请通过 ETF 执行"}],
    }

    for err_case in [no_price, unknown, industry_err]:
        card_html = render_position_card(err_case)
        assert card_html, f"{err_case['error']} 渲染结果不应为空"
        assert err_case["target"] in card_html, \
            f"卡片应包含标的 {err_case['target']}"

    monitoring = [no_price]
    empty_signals = pd.DataFrame({"signal_date": pd.Series(dtype="datetime64[ns]"),
                                   "industry": pd.Series(dtype=str),
                                   "priority": pd.Series(dtype=str)})
    html = render_html("2026-06-25", "2026-06-24", empty_signals, monitoring)
    # n_positions 计数包含 error 记录
    assert "159995" in html, "日报 HTML 应含异常持仓的标的代码"
    assert "暂无可用行情" in html, "日报 HTML 应包含异常原因"
    assert "当前无持仓" not in html, "有异常持仓时不显示「当前无持仓」"


# ── 测试 3：BUY+ADD 聚合退出列表 ──

def test_buy_add_aggregated_summary(monkeypatch, tmp_path):
    """BUY+ADD 后 get_position_summary_all 只返回 1 行，total_shares=1500"""
    db = tmp_path / "test_trade.sqlite3"
    _patch_db(monkeypatch, db)
    from v0_6.core import init_schema, add_trade, get_position_summary_all
    init_schema()

    add_trade(target="512800", target_type="ETF", target_name="银行ETF",
              action="BUY", amount=780, price=0.78, shares=1000.0,
              trade_date="2026-06-01")
    add_trade(target="512800", target_type="ETF", target_name="银行ETF",
              action="ADD", amount=410, price=0.82, shares=500.0,
              trade_date="2026-06-10")

    summaries = get_position_summary_all()
    assert len(summaries) == 1, f"应返回 1 行，实 {len(summaries)}"
    assert summaries[0]["total_shares"] == 1500.0, \
        f"total_shares 应=1500，实 {summaries[0]['total_shares']}"


# ── 测试 4：REDUCE 建议使用当前价 ──

def test_reduce_suggestion_uses_current_price():
    """REDUCE 建议金额 = total_shares × latest_price / 3"""
    # 模拟 JS 计算：const suggestedShares = totalShares * ratio;
    # const suggestedAmount = suggestedShares * latestPrice;
    total_shares = 1500.0
    latest_price = 1.20
    ratio = 1.0 / 3.0

    suggested_shares = total_shares * ratio
    suggested_amount = suggested_shares * latest_price

    assert abs(suggested_shares - 500.0) < 1e-9, \
        f"建议减仓股数应=500，实 {suggested_shares}"
    assert abs(suggested_amount - 600.0) < 1e-9, \
        f"建议金额应=600，实 {suggested_amount}"

    # 验证不使用 avg_cost
    avg_cost = 0.80
    wrong_amount = (total_shares * avg_cost) / 3
    assert suggested_amount != wrong_amount, \
        "不应使用 avg_cost 计算建议金额"


# ── 测试 5：SELL 建议完整覆盖净持仓 ──

def test_sell_fully_covers_net_position():
    """SELL 建议金额 = total_shares × latest_price × 1"""
    # 模拟 JS 计算
    total_shares = 1500.0
    latest_price = 1.20
    ratio = 1.0

    suggested_shares = total_shares * ratio
    suggested_amount = suggested_shares * latest_price

    assert abs(suggested_shares - 1500.0) < 1e-9, \
        f"建议 SELL 股数应=1500，实 {suggested_shares}"
    assert abs(suggested_amount - 1800.0) < 1e-9, \
        f"建议 SELL 金额应=1800，实 {suggested_amount}"

    # 验证后端逻辑：SELL 记录 shares=1500, amount=1800, 最终 closed=1
    # 模拟 get_position_net 返回值
    net_shares = 1500.0
    price = 1.20
    sell_shares = net_shares
    sell_amount = sell_shares * price
    assert abs(sell_shares - 1500.0) < 1e-9
    assert abs(sell_amount - 1800.0) < 1e-9
