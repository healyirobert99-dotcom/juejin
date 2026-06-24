"""
v0.6 实盘交易录入工具（v6 升级：掘金信号 + progress 参数）

用法：
    # 买入（首次触发）
    python log_trade.py buy 半导体 1.234 1000 2026-06-24 --progress 0.05 --position-pct 0.08

    # 加仓
    python log_trade.py add 半导体 1.250 500 2026-06-25 --position-pct 0.04

    # 减仓（止盈 1/3）
    python log_trade.py reduce 半导体 1.400 333 2026-07-15 --trade-id 1

    # 清仓
    python log_trade.py sell 半导体 1.200 667 2026-07-20 --trade-id 1
"""
import argparse
import sys
from pathlib import Path

# 路径
V0_6_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(V0_6_ROOT))

from v0_6.core import (
    add_trade,
    close_trade,
    init_schema,
    get_bucket,
    calc_stop_price,
    calc_take_profit_price,
)


def main():
    parser = argparse.ArgumentParser(description="v0.6 实盘交易录入（v6 掘金信号）")
    subparsers = parser.add_subparsers(dest="action", required=True)

    # BUY
    buy_p = subparsers.add_parser("buy", help="买入")
    buy_p.add_argument("industry", help="行业名称")
    buy_p.add_argument("price", type=float, help="买入价格")
    buy_p.add_argument("shares", type=float, help="买入股数")
    buy_p.add_argument("date", help="买入日期 YYYY-MM-DD")
    buy_p.add_argument("--progress", type=float, default=None, help="入场 progress (0-1)")
    buy_p.add_argument("--position-pct", type=float, default=None, help="仓位比例")
    buy_p.add_argument("--signal-type", default="GOLD", help="信号类型（GOLD=掘金信号）")
    buy_p.add_argument("--notes", default=None, help="备注")

    # ADD
    add_p = subparsers.add_parser("add", help="加仓")
    add_p.add_argument("industry")
    add_p.add_argument("price", type=float)
    add_p.add_argument("shares", type=float)
    add_p.add_argument("date")
    add_p.add_argument("--position-pct", type=float, default=None)
    add_p.add_argument("--notes", default=None)

    # REDUCE
    red_p = subparsers.add_parser("reduce", help="减仓（止盈 1/3）")
    red_p.add_argument("industry")
    red_p.add_argument("price", type=float)
    red_p.add_argument("shares", type=float)
    red_p.add_argument("date")
    red_p.add_argument("--trade-id", type=int, required=True, help="原 BUY 交易的 trade_id")
    red_p.add_argument("--notes", default=None)

    # SELL
    sell_p = subparsers.add_parser("sell", help="清仓")
    sell_p.add_argument("industry")
    sell_p.add_argument("price", type=float)
    sell_p.add_argument("shares", type=float)
    sell_p.add_argument("date")
    sell_p.add_argument("--trade-id", type=int, required=True)
    sell_p.add_argument("--notes", default=None)

    args = parser.parse_args()
    init_schema()

    if args.action == "buy":
        # 算进度桶
        if args.progress is not None:
            bucket = get_bucket(args.progress)
            stop_price = calc_stop_price(args.price, args.progress)
            tp_price = calc_take_profit_price(args.price, args.progress)
            print(f"\n[掘金信号] progress={args.progress:.1%}")
            print(f"  进度桶: {bucket['name']}")
            print(f"  止损阈值: {bucket['stop']:.0%} → 止损价 ¥{stop_price:.4f}")
            print(f"  止盈阈值: {bucket['take_profit']:.0%} → 止盈价 ¥{tp_price:.4f}")
            print(f"  仓位: {args.position_pct or bucket['position_first']:.0%}")

        trade_id = add_trade(
            industry=args.industry,
            action="BUY",
            price=args.price,
            shares=args.shares,
            trade_date=args.date,
            signal_type=args.signal_type,
            position_pct=args.position_pct,
            progress=args.progress,
            notes=args.notes,
        )
        print(f"\n✅ 买入录入成功，trade_id={trade_id}")

    elif args.action == "add":
        trade_id = add_trade(
            industry=args.industry,
            action="ADD",
            price=args.price,
            shares=args.shares,
            trade_date=args.date,
            position_pct=args.position_pct,
            notes=args.notes,
        )
        print(f"\n✅ 加仓录入成功，trade_id={trade_id}")

    elif args.action == "reduce":
        # 减仓 = 录入 REDUCE + 标记原 BUY 的 reduced_1_3
        import sqlite3
        from v0_6.core.config import DB_PATH

        trade_id = add_trade(
            industry=args.industry,
            action="REDUCE",
            price=args.price,
            shares=args.shares,
            trade_date=args.date,
            notes=args.notes,
        )
        con = sqlite3.connect(str(DB_PATH))
        con.execute(
            "UPDATE live_trades SET reduced_1_3 = 1 WHERE trade_id = ?",
            (args.trade_id,),
        )
        con.commit()
        con.close()
        print(f"\n✅ 减仓录入成功，trade_id={trade_id}")
        print(f"  原 trade_id={args.trade_id} 标记为已减 1/3")

    elif args.action == "sell":
        trade_id = add_trade(
            industry=args.industry,
            action="SELL",
            price=args.price,
            shares=args.shares,
            trade_date=args.date,
            notes=args.notes,
        )
        # 标记原 BUY 为已平仓
        close_trade(args.trade_id, args.price, args.date)
        print(f"\n✅ 清仓录入成功，trade_id={trade_id}")
        print(f"  原 trade_id={args.trade_id} 已平仓")


if __name__ == "__main__":
    main()
