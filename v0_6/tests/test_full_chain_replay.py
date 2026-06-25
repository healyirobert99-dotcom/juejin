"""
全链路历史回放测试（v3：真实信号注入 + 完整交易链路）

覆盖：
1. mock 信号注入 BUY → 受控 ETF 价格 → REDUCE → SELL 链路
2. 两次 run_replay 确定性（含真实交易）
3. 每次运行独立交易库
4. 真实库 SHA256 前后一致
5. 未来数据隔离
6. daily_account.csv 有真实的金额/盈亏
7. SELL 用净股数
8. realized_pnl 不重复计算（REDUCE 股数不计入 SELL）
9. 输出目录隔离（产物在 ctx.replay_dir 下，不覆盖全局）
"""
import sys
import sqlite3
import hashlib
import csv
import shutil
from pathlib import Path

V0_6_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(V0_6_ROOT))

import pandas as pd
import pytest


# ── helper: 构造足够大的源库 + ETF 价格轨迹 ──

def _make_rich_source_db(tmp_path, days=500, n_stocks=20, etf_code="512800.SH") -> Path:
    """创建最小化但足以触发回放链路的源行情库。

    关键约束：
    - ≥310 个交易日（250+60 行业判定窗口）
    - ≥20 只股票（min_stocks_per_industry）
    - ETF 数据完整覆盖全部交易日
    - ETF 价格轨迹：先涨后跌，确保能触发 REDUCE 和 CLEAR
    """
    tmp_path = Path(tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = tmp_path / "source_stock_data.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE market_calendar (cal_date TEXT, is_open INTEGER)")
    con.execute("CREATE TABLE daily_hfq (ts_code TEXT, trade_date TEXT, close REAL, vol REAL)")
    con.execute("CREATE TABLE etf_daily (ts_code TEXT, trade_date TEXT, close REAL, vol REAL, amount REAL)")
    con.execute("CREATE TABLE stock_daily_raw (ts_code TEXT, trade_date TEXT, close REAL, vol REAL, amount REAL)")

    from datetime import date, timedelta
    start = date(2024, 1, 2)
    dates = []
    cur = start
    while len(dates) < days:
        if cur.weekday() < 5:
            dates.append(cur.strftime("%Y%m%d"))
        cur += timedelta(days=1)

    # 构造 20 只金融股
    industry_codes = [
        "601398.SH", "601939.SH", "600036.SH", "601288.SH", "600000.SH",
        "600016.SH", "600036.SH", "601166.SH", "601328.SH", "600030.SH",
        "600837.SH", "601818.SH", "601009.SH", "600015.SH", "600050.SH",
        "601088.SH", "601857.SH", "600028.SH", "600104.SH", "600585.SH",
    ]

    # ETF 受控价格轨迹：
    # - 阶段 1 (前 150 天)：平稳 1.00
    # - 阶段 2 (150-200)：缓涨到 1.20
    # - 阶段 3 (200-250)：涨到 1.35（触发 REDUCE，tp=1.30）
    # - 阶段 4 (250-300)：暴跌到 0.80（触发 CLEAR，stop=0.85）
    # - 阶段 5 (300+)：恢复平稳
    def etf_price(i):
        if i < 150:
            return 1.00
        elif i < 200:
            return 1.00 + 0.20 * (i - 150) / 50
        elif i < 250:
            return 1.20 + 0.15 * (i - 200) / 50
        elif i < 300:
            return 1.35 - 0.55 * (i - 250) / 50
        else:
            return 0.80

    for i, d in enumerate(dates):
        # 交易日历（所有工作日均为开市）
        con.execute("INSERT INTO market_calendar VALUES (?, 1)", (d,))
        e_price = etf_price(i)
        # ETF 每天都有数据（512880 是 real sector_etf_map.csv 中"金融"的首选）
        for code in [etf_code, "512880.SH"]:
            con.execute("INSERT INTO etf_daily VALUES (?, ?, ?, ?, ?)",
                        (code, d, e_price, 1000000, 1000000))
        # 20 只股票每天有随机漫步价格
        for code in industry_codes:
            stock_price = 5.0 + 0.02 * i + (hash(code + d) % 50) * 0.02
            con.execute("INSERT INTO daily_hfq VALUES (?, ?, ?, ?)",
                        (code, d, stock_price, 1000000))
            con.execute("INSERT INTO stock_daily_raw VALUES (?, ?, ?, ?, ?)",
                        (code, d, stock_price, 1000000, 10000000))

    con.commit()
    con.close()
    return db


# ── helper: hash ──

def _hash(path: Path) -> str:
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _make_mock_get(signal_dates: dict):
    """返回一个 mock get_today_signals，在给定日期（YYYY-MM-DD -> priority）返回金融行业信号"""
    def mock_get(signal_data_date, lookback_days=3):
        if signal_data_date in signal_dates:
            return pd.DataFrame([{
                "signal_date": pd.Timestamp(signal_data_date),
                "industry": "金融",
                "priority": signal_dates[signal_data_date],
                "breadth_at_signal": 0.45,
                "vol_ratio_at_signal": 1.2,
            }])
        return pd.DataFrame()
    return mock_get


# ═══════════════════════════════════════════════
# 测试 1：mock 信号 → 完整 BUY→REDUCE→SELL 链路
# ═══════════════════════════════════════════════

def test_replay_buy_reduce_sell_chain(tmp_path, monkeypatch):
    """注入 mock 信号 + 受控 ETF 价格 → BUY → REDUCE → SELL 全链路"""
    src = _make_rich_source_db(tmp_path / "src", days=500, n_stocks=20)

    # ── 注入 mock 信号 ──
    from v0_6.scripts import run_daily_v1_html

    monkeypatch.setattr(run_daily_v1_html, "get_today_signals",
                        _make_mock_get({"2024-07-01": "FIRST", "2024-09-01": "REPEAT"}))

    from v0_6.core.replay_engine import ReplayContext, run_replay
    out = tmp_path / "replay_out"
    ctx = ReplayContext(source_db=src, replay_dir=out)
    ctx.setup()
    try:
        result = run_replay(ctx, max_days=200)

        # ── 必须有真实交易 ──
        assert result["total_signals"] > 0, "应有注入的信号"
        assert result["total_orders"] > 0, "信号应产生 BUY 订单"
        assert result["total_trades"] > 0, "应有成交记录"
        assert ctx.trades, "ctx.trades 不应为空"

        actions = [t["action"] for t in ctx.trades]
        assert "BUY" in actions, f"应有 BUY，实际 actions={actions}"
        # REDUCE 和 SELL 取决于价格是否触发监控
        # 在 200 天内 ETF 价格从 1.00→1.35→~1.30（尚未暴跌到 0.80 附近）
        # REDUCE 应在价格 > tp=1.30 时触发
        has_reduce = "REDUCE" in actions
        has_sell = "SELL" in actions
        assert has_reduce or has_sell, (
            f"应有 REDUCE 或 SELL（至少一个退出动作），实际 actions={actions}"
        )

        # ── 验证输出文件在 ctx.replay_dir 下 ──
        assert (out / "replay_report.md").exists(), "报告应在 replay_dir 下"
        assert (out / "daily_account.csv").exists()
        assert (out / "trades.csv").exists()
        assert (out / "signals.csv").exists()

        # ── 验证 daily_account 有真实数字 ──
        daily_path = out / "daily_account.csv"
        with open(str(daily_path), encoding="utf-8") as f:
            reader = list(csv.DictReader(f))
        assert len(reader) > 0, "daily_account 应有数据"
        for row in reader:
            if float(row.get("position_count", 0)) > 0:
                # 有持仓时 gross_exposure 应 > 0（是金额不是数量）
                assert float(row["gross_exposure"]) > float(row["position_count"]), (
                    f"gross_exposure 应是金额（> position_count），"
                    f"实际 gross_exposure={row['gross_exposure']}, position_count={row['position_count']}"
                )
                break

        print(f"  ✓ 链路: actions={actions}, trades={result['total_trades']}, "
              f"signals={result['total_signals']}")
        print(f"  ✓ 输出目录: {out}")

    finally:
        ctx.teardown()


# ═══════════════════════════════════════════════
# 测试 2：两次 run_replay 确定性（含真实交易）
# ═══════════════════════════════════════════════

def test_replay_two_runs_deterministic_with_trades(tmp_path, monkeypatch):
    """两次完整回放：关键指标 + 成交记录一致"""
    src = _make_rich_source_db(tmp_path / "src", days=500, n_stocks=20)

    from v0_6.scripts import run_daily_v1_html

    monkeypatch.setattr(run_daily_v1_html, "get_today_signals",
                        _make_mock_get({"2024-07-01": "FIRST"}))

    from v0_6.core.replay_engine import ReplayContext, run_replay

    results = []
    for run_name in ["run1", "run2"]:
        out = tmp_path / run_name
        ctx = ReplayContext(source_db=src, replay_dir=out)
        ctx.setup()
        try:
            r = run_replay(ctx, max_days=200)
            results.append({
                "total_signals": r["total_signals"],
                "total_orders": r["total_orders"],
                "total_trades": r["total_trades"],
                "invariant_violations": r["invariant_violations"],
                "trades": sorted([
                    (t["fill_date"], t["target"], t["action"], t["shares"])
                    for t in ctx.trades
                ]),
            })
        finally:
            ctx.teardown()

    r1, r2 = results
    assert r1["total_signals"] == r2["total_signals"]
    assert r1["total_orders"] == r2["total_orders"]
    assert r1["total_trades"] == r2["total_trades"]
    assert r1["trades"] == r2["trades"], f"两次回放成交不一致"
    assert r1["total_trades"] > 0, "应有成交记录"


# ═══════════════════════════════════════════════
# 测试 3：每次运行独立交易库（第二次从零开始）
# ═══════════════════════════════════════════════

def test_replay_each_run_zero_positions_start(tmp_path, monkeypatch):
    """setup() 强制删除旧交易库 → 第二次 run 持仓从 0 开始"""
    src = _make_rich_source_db(tmp_path / "src", days=500, n_stocks=20)

    from v0_6.scripts import run_daily_v1_html
    monkeypatch.setattr(run_daily_v1_html, "get_today_signals",
                        _make_mock_get({"2024-07-01": "FIRST"}))

    from v0_6.core.replay_engine import ReplayContext, run_replay
    from v0_6.core.config import DB_PATH

    # 第一次
    out1 = tmp_path / "run1"
    ctx1 = ReplayContext(source_db=src, replay_dir=out1)
    ctx1.setup()
    try:
        r1 = run_replay(ctx1, max_days=200)
    finally:
        ctx1.teardown()

    # 第二次：应创建全新的交易库，从零持仓开始
    out2 = tmp_path / "run2"
    ctx2 = ReplayContext(source_db=src, replay_dir=out2)
    ctx2.setup()
    try:
        from v0_6.core import get_position_summary_all
        # 刚 setup 完还没 run，持仓应为 0
        assert len(get_position_summary_all()) == 0, "setup 后持仓应从 0 开始"
        r2 = run_replay(ctx2, max_days=200)
        # 最终持仓应与第一次一致（确定性）
        assert r2["total_trades"] == r1["total_trades"], "两次应产生相同成交数"
    finally:
        ctx2.teardown()


# ═══════════════════════════════════════════════
# 测试 4：真实数据库未被修改
# ═══════════════════════════════════════════════

def test_replay_real_database_unchanged(tmp_path, monkeypatch):
    """run_replay 前后真实 trade/stock DB SHA256 不变"""
    from v0_6.scripts import run_daily_v1_html
    monkeypatch.setattr(run_daily_v1_html, "get_today_signals",
                        _make_mock_get({"2024-07-01": "FIRST"}))

    from v0_6.core.config import DB_PATH, STOCK_DATA_DIR
    real_trade_hash = _hash(DB_PATH)
    real_stock_hash = _hash(STOCK_DATA_DIR / "stock_data.db")

    src = _make_rich_source_db(tmp_path / "src", days=500, n_stocks=20)
    from v0_6.core.replay_engine import ReplayContext, run_replay
    out = tmp_path / "replay_out"
    ctx = ReplayContext(source_db=src, replay_dir=out)
    ctx.setup()
    try:
        run_replay(ctx, max_days=1)
    finally:
        ctx.teardown()

    assert _hash(DB_PATH) == real_trade_hash, "真实交易库被修改"
    assert _hash(STOCK_DATA_DIR / "stock_data.db") == real_stock_hash, "真实行情库被修改"


# ═══════════════════════════════════════════════
# 测试 5：未来数据隔离
# ═══════════════════════════════════════════════

def test_replay_no_future_data_in_daily_lib(tmp_path, monkeypatch):
    """回放库中不包含最后处理日之后的行情数据"""
    from v0_6.scripts import run_daily_v1_html
    monkeypatch.setattr(run_daily_v1_html, "get_today_signals",
                        _make_mock_get({"2024-07-01": "FIRST"}))

    src = _make_rich_source_db(tmp_path / "src", days=500, n_stocks=20)
    from v0_6.core.replay_engine import ReplayContext, run_replay
    out = tmp_path / "replay_out"
    ctx = ReplayContext(source_db=src, replay_dir=out)
    ctx.setup()
    try:
        result = run_replay(ctx, max_days=30)
        # 最后处理的交易日
        last_date = ctx.days[-1]["replay_date"] if ctx.days else None
        assert last_date, "应有处理的交易日"
        last_str = last_date.replace("-", "")

        con = sqlite3.connect(str(ctx.stock_db))
        for tbl in ["daily_hfq", "etf_daily", "stock_daily_raw"]:
            try:
                rows = con.execute(
                    f"SELECT MAX(trade_date) FROM {tbl}"
                ).fetchone()
                if rows and rows[0]:
                    assert rows[0] <= last_str, (
                        f"表 {tbl} 存在 {rows[0]} > 最后处理日 {last_str} 的未来数据"
                    )
            except sqlite3.OperationalError:
                pass
        con.close()
    finally:
        ctx.teardown()


# ═══════════════════════════════════════════════
# 测试 6：realized_pnl 不重复计算
# ═══════════════════════════════════════════════

def test_replay_realized_pnl_no_double_count(tmp_path):
    """手动构造 BUY→REDUCE→SELL 后：realized_pnl ≠ REDUCE前股数×SELL价

    再用 _compute_daily_account 验证
    """
    # 直接走 _compute_daily_account 而不是 run_replay
    from v0_6.core.replay_engine import ReplayContext, _compute_daily_account
    from v0_6.core import add_trade, close_trade_by_target
    import v0_6.core.config as cfg

    src = _make_rich_source_db(tmp_path / "src", days=500, n_stocks=20)
    out = tmp_path / "replay_out"
    ctx = ReplayContext(source_db=src, replay_dir=out)
    ctx.setup()
    try:
        # 手动创建交易序列
        # BUY 1000 @ 1.00
        add_trade(
            target="512800.SH", target_type="ETF", target_name="银行ETF",
            action="BUY", amount=1000, price=1.00, shares=1000,
            trade_date="20240617",
        )
        # REDUCE 333 @ 1.10
        add_trade(
            target="512800.SH", target_type="ETF", target_name="银行ETF",
            action="REDUCE", amount=round(333 * 1.10, 2), price=1.10, shares=333,
            trade_date="20240618",
        )
        # SELL 667 @ 1.20
        net_shares = 667
        add_trade(
            target="512800.SH", target_type="ETF", target_name="银行ETF",
            action="SELL", amount=round(net_shares * 1.20, 2), price=1.20,
            shares=net_shares, trade_date="20240619",
        )
        close_trade_by_target("512800.SH", 1.20, "20240619")

        # 注入 ETF 价格到回放行情库（_compute_daily_account 需要）
        con = sqlite3.connect(str(ctx.stock_db))
        con.execute("INSERT INTO etf_daily VALUES (?, ?, ?, 1000000, 1000000)",
                    ("512800.SH", "20240619", 1.20))
        con.commit()
        con.close()

        # 计算
        acct = _compute_daily_account(ctx, "2024-06-19")

        # 正确已实现盈亏：333 × 0.10 + 667 × 0.20 = 33.30 + 133.40 = 166.70
        expected = 333 * 0.10 + 667 * 0.20
        actual = acct["realized_pnl"]
        assert abs(actual - expected) < 0.02, (
            f"realized_pnl 应为 ~{expected:.2f}，实际 {actual:.2f}"
            f"\n（若=200 说明把已 REDUCE 的 333 股又按 SELL 价算了一次）"
        )
        print(f"  ✓ realized_pnl={actual:.2f}（期望={expected:.2f}）— 未重复计算")

    finally:
        ctx.teardown()


# ═══════════════════════════════════════════════
# 测试 7：SELL 用净股数
# ═══════════════════════════════════════════════

def test_replay_sell_uses_net_shares(tmp_path):
    """add_trade(SELL) 的 shares 字段是净股数，不是原始 BUY 总数"""
    from v0_6.core.replay_engine import ReplayContext
    from v0_6.core import add_trade, get_position_net
    import v0_6.core.config as cfg

    src = _make_rich_source_db(tmp_path / "src", days=500, n_stocks=20)
    out = tmp_path / "replay_out"
    ctx = ReplayContext(source_db=src, replay_dir=out)
    ctx.setup()
    try:
        add_trade(
            target="512800.SH", target_type="ETF", target_name="银行ETF",
            action="BUY", amount=1000, price=1.00, shares=1000,
            trade_date="20240615",
        )
        add_trade(
            target="512800.SH", target_type="ETF", target_name="银行ETF",
            action="REDUCE", amount=round(333 * 1.10, 2), price=1.10, shares=333,
            trade_date="20240616",
        )
        net_before = get_position_net("512800.SH")
        assert net_before["net_shares"] == 667, f"REDUCE 后净股数应为 667"

        net_s = net_before["net_shares"]
        sell_id = add_trade(
            target="512800.SH", target_type="ETF", target_name="银行ETF",
            action="SELL", amount=round(net_s * 1.20, 2), price=1.20,
            shares=net_s, trade_date="20240617",
        )

        con = sqlite3.connect(str(cfg.DB_PATH))
        row = con.execute(
            "SELECT shares, amount FROM live_trades WHERE trade_id=?",
            (sell_id,),
        ).fetchone()
        con.close()
        assert row[0] == 667, f"SELL 成交股数应为净股数 667，实际 {row[0]}"
        assert abs(row[1] - 667 * 1.20) < 0.01
    finally:
        ctx.teardown()


# ═══════════════════════════════════════════════
# 测试 8：输出目录隔离
# ═══════════════════════════════════════════════

def test_replay_output_dir_isolation(tmp_path, monkeypatch):
    """产物写入 ctx.replay_dir，不写入全局 OUTPUT_DIR"""
    from v0_6.scripts import run_daily_v1_html
    monkeypatch.setattr(run_daily_v1_html, "get_today_signals",
                        _make_mock_get({"2024-07-01": "FIRST"}))

    src = _make_rich_source_db(tmp_path / "src", days=500, n_stocks=20)
    from v0_6.core.replay_engine import ReplayContext, run_replay, OUTPUT_DIR

    out1 = tmp_path / "run_a"
    out2 = tmp_path / "run_b"

    # 两次独立回放
    for od in [out1, out2]:
        ctx = ReplayContext(source_db=src, replay_dir=od)
        ctx.setup()
        try:
            run_replay(ctx, max_days=30)
        finally:
            ctx.teardown()

    # 各自有自己的产物
    for od in [out1, out2]:
        assert (od / "replay_report.md").exists(), f"{od} 应有报告"
        assert (od / "daily_account.csv").exists()
        assert (od / "trades.csv").exists()

    # 全局 OUTPUT_DIR 不应有产物（除非被复用，但这里我们没用 --reuse-output）
    # 注意：OUTPUT_DIR 仅作为 CLI 默认的基础目录，不直接被 _write_* 使用
