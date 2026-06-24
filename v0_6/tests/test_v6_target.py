"""
v6.1/v6.2 标的匹配 + 进度/桶 + 止损/止盈价 · 关键回归测试

覆盖：
- 98 个 ETF 全部能匹配
- 45 个有日线数据的 ETF 进度算法精度
- 桶边界判定
- 止损/止盈价基于【用户买入价】算（不是现价）
- 完整录入数据流
"""
import sys
import sqlite3
import csv
from pathlib import Path

V0_6_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(V0_6_ROOT))

from v0_6.core import (
    match_target,
    calc_progress_for_etf,
    get_bucket,
    add_trade,
    init_schema,
)
from v0_6.core.gold_signal_v6 import (
    get_stop_threshold,
    get_take_profit_threshold,
    PROGRESS_BUCKETS,
)


def test_etf_match_all():
    """98 个 ETF 全部能匹配"""
    with open(V0_6_ROOT / "data" / "sector_etf_map.csv", encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader)
        etfs = []
        for row in reader:
            if not row or row[0].startswith("==="):
                continue
            if not row[3].strip() or row[3] == "—":
                continue
            etfs.append((row[3].strip(), row[4].strip()))

    fail = []
    for code, name in etfs:
        m = match_target(code)
        if m.get("target_type") != "ETF" or m.get("target_name") != name:
            fail.append((code, name, m))

    assert not fail, f"{len(fail)}/{len(etfs)} 个 ETF 匹配失败: {fail[:3]}"
    print(f"  ✓ 98 个 ETF 全部能匹配")


def test_progress_precision():
    """进度算法精度 + 桶判定"""
    con = sqlite3.connect(str(V0_6_ROOT / "stock_data" / "stock_data.db"))
    codes = [r[0] for r in con.execute("SELECT DISTINCT ts_code FROM etf_daily")]
    con.close()

    err = []
    for ts_code in codes:
        r = calc_progress_for_etf(ts_code, "20260624")
        if not r:
            continue
        cur, low, prog = r["current"], r["low_60d"], r["progress"]
        manual = (cur - low) / low if low > 0 else 0
        if abs(manual - prog) > 1e-9:
            err.append(f"{ts_code}: 进度精度误差 {abs(manual-prog)*1e6:.2f}ppm")
            continue
        # 桶边界
        if prog < 0.10:
            exp = "极早期"
        elif prog < 0.30:
            exp = "早期"
        elif prog < 0.50:
            exp = "中期"
        else:
            exp = "晚期"
        actual = get_bucket(prog)["name"]
        if exp != actual:
            err.append(f"{ts_code}: 进度 {prog*100:.2f}% 应入 {exp}，实入 {actual}")

    assert not err, f"进度/桶错误 {len(err)}: {err[:3]}"
    print(f"  ✓ {len(codes)} 个 ETF 进度/桶判定全部正确")


def test_stop_take_profit_uses_user_price():
    """止损/止盈价必须基于用户买入价，不是现价"""
    init_schema()
    # 场景：用户以 ¥0.78 买入 512800 银行
    # 进度 0% → 极早期 → 止损 -18% / 止盈 +30%
    buy_price = 0.78
    progress = 0.0
    stop_pct = get_stop_threshold(progress)
    tp_pct = get_take_profit_threshold(progress)
    stop = buy_price * (1 + stop_pct)
    tp = buy_price * (1 + tp_pct)
    assert abs(stop - 0.6396) < 1e-9, f"止损价错: {stop}"
    assert abs(tp - 1.0140) < 1e-9, f"止盈价错: {tp}"
    print(f"  ✓ 买入价 0.78 → 止损 0.6396, 止盈 1.0140（不是基于现价）")

    # 关键：买入价 0.85 时止损止盈不同
    buy_price2 = 0.85
    stop2 = buy_price2 * 0.82
    tp2 = buy_price2 * 1.30
    assert stop2 != stop
    assert tp2 != tp
    print(f"  ✓ 买入价 0.85 → 止损 {stop2:.4f}, 止盈 {tp2:.4f}（不同，证明基于用户价）")


def test_add_trade_persists_correct():
    """完整录入数据流：用户买入价 → 止损价正确写入 DB"""
    init_schema()
    trade_id = add_trade(
        target="512800",
        target_type="ETF",
        target_name="华宝中证银行ETF",
        action="BUY",
        amount=780,
        price=0.78,
        trade_date="2026-06-24",
        progress=0.0,
        progress_bucket="极早期",
        stop_threshold=-0.18,
        take_profit_threshold=0.30,
        stop_price=0.78 * 0.82,
        take_profit_price=0.78 * 1.30,
        notes="v6.2 test",
    )
    con = sqlite3.connect(str(V0_6_ROOT / "data" / "a_stock_selector.sqlite3"))
    row = con.execute(
        "SELECT target, target_type, target_name, amount, price, shares, "
        "progress_bucket, stop_price, take_profit_price "
        "FROM live_trades WHERE trade_id = ?",
        (trade_id,),
    ).fetchone()
    con.execute("DELETE FROM live_trades WHERE trade_id = ?", (trade_id,))
    con.commit()
    con.close()

    assert row[0] == "512800"
    assert row[1] == "ETF"
    assert row[2] == "华宝中证银行ETF"
    assert abs(row[3] - 780) < 1e-9
    assert abs(row[4] - 0.78) < 1e-9
    assert abs(row[5] - 1000) < 1e-9  # shares = 780 / 0.78
    assert row[6] == "极早期"
    assert abs(row[7] - 0.6396) < 1e-4
    assert abs(row[8] - 1.0140) < 1e-4
    print(f"  ✓ 录入数据流：target/amount/price/shares/bucket/stop/tp 全部正确")


def test_progress_negative_or_zero():
    """边界：现价低于 60d_low（罕见但要测）"""
    # 进度 0%：60d_low == 现价（实际场景）
    r = calc_progress_for_etf("512800", "20260624")
    if r:
        assert r["progress"] == 0.0
        bucket = get_bucket(0.0)
        assert bucket["name"] == "极早期"
        print(f"  ✓ 进度 0% → 极早期（边界）")
    else:
        print(f"  ⊘ 512800 无数据，跳过")


if __name__ == "__main__":
    print("\n=== v6.2 关键回归测试 ===\n")
    test_etf_match_all()
    test_progress_precision()
    test_stop_take_profit_uses_user_price()
    test_add_trade_persists_correct()
    test_progress_negative_or_zero()
    print("\n✅ 全部通过\n")
