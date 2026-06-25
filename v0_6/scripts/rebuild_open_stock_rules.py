"""
一次性迁移：使用未复权行情重算现有未平仓 STOCK 的进度桶和止损止盈参数。

用法：
    python rebuild_open_stock_rules.py                # 执行校正
    python rebuild_open_stock_rules.py --dry-run      # 预览拟修改记录
    python rebuild_open_stock_rules.py --backup        # 先备份数据库
"""
from __future__ import annotations

import hashlib
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

V0_6_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(V0_6_ROOT))

from v0_6.core.config import DB_PATH, STOCK_DATA_DB
from v0_6.core.gold_signal_v6 import get_bucket, calc_stop_price, calc_take_profit_price
from v0_6.core.market_data import normalize_stock_ts_code, get_table_latest_date
from v0_6.core.live_trade_store import init_schema


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="重算未平仓 STOCK 的进度桶和止损止盈")
    parser.add_argument("--dry-run", action="store_true", help="预览模式，不写库")
    parser.add_argument("--backup", action="store_true", help="执行前备份数据库")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"❌ 交易数据库不存在: {DB_PATH}")
        sys.exit(1)
    if not STOCK_DATA_DB.exists():
        print(f"❌ 行情数据库不存在: {STOCK_DATA_DB}")
        sys.exit(1)

    # 备份
    if args.backup and not args.dry_run:
        backup_path = DB_PATH.with_suffix(f".sqlite3.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
        shutil.copy2(DB_PATH, backup_path)
        before_hash = sha256(DB_PATH)
        print(f"📦 备份: {backup_path}")
        print(f"  SHA256 (before): {before_hash}")

    init_schema()
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row

    # 查出所有未平仓 STOCK
    rows = con.execute(
        "SELECT * FROM live_trades WHERE target_type='STOCK' AND action IN ('BUY', 'ADD') AND closed=0 "
        "ORDER BY trade_id"
    ).fetchall()

    if not rows:
        print("✅ 无未平仓 STOCK 记录，无需校正")
        con.close()
        return

    print(f"\n待校正 STOCK 记录: {len(rows)} 笔\n")

    stock_con = sqlite3.connect(str(DB_PATH))
    results = []

    for row in rows:
        tid = row["trade_id"]
        target = row["target"]
        trade_date = row["trade_date"]
        entry_price = row["price"]

        # 标准化代码
        try:
            ts_code = normalize_stock_ts_code(target)
        except ValueError:
            results.append((tid, target, "代码无法标准化", "跳过"))
            continue

        # 查 stock_daily_raw
        raw_rows = stock_con.execute(
            "SELECT trade_date, close FROM stock_daily_raw "
            "WHERE ts_code = ? AND trade_date <= ? "
            "ORDER BY trade_date DESC LIMIT 60",
            (ts_code, trade_date.replace("-", "")),
        ).fetchall()

        if not raw_rows or len(raw_rows) < 5:
            results.append((tid, target, f"stock_daily_raw 数据不足 ({len(raw_rows)} 条)", "未修复"))
            continue

        closes = [r[1] for r in raw_rows]
        current_raw = closes[0]
        low_60d = min(closes)
        progress = (current_raw - low_60d) / low_60d if low_60d > 0 else 0
        bucket = get_bucket(progress)

        # 止损止盈价仍基于 entry_price（实际成交价）
        stop_price = entry_price * (1 + bucket["stop"])
        tp_price = entry_price * (1 + bucket["take_profit"])

        old_bucket = row["progress_bucket"]
        old_stop = row["stop_threshold"]
        old_tp = row["take_profit_threshold"]

        if args.dry_run:
            results.append((
                tid, target, trade_date, ts_code,
                old_bucket, bucket["name"],
                old_stop, bucket["stop"],
                old_tp, bucket["take_profit"],
                progress,
            ))
        else:
            con.execute(
                "UPDATE live_trades SET "
                "progress_at_entry=?, progress_bucket=?, "
                "stop_threshold=?, take_profit_threshold=?, "
                "stop_price=?, take_profit_price=? "
                "WHERE trade_id=?",
                (progress, bucket["name"], bucket["stop"], bucket["take_profit"],
                 stop_price, tp_price, tid),
            )
            results.append((
                tid, target, trade_date, ts_code,
                old_bucket, bucket["name"],
                old_stop, bucket["stop"],
                old_tp, bucket["take_profit"],
                progress, "已修复",
            ))

    stock_con.close()

    if not args.dry_run:
        con.commit()

    # 输出报告
    print(f"{'ID':<5} {'标的':<12} {'入场日':<12} {'原桶':<8} {'新桶':<10} {'原止损':<8} {'新止损':<8} {'原止盈':<8} {'新止盈':<8} {'进度':<8} {'状态':<10}")
    print("-" * 110)
    for r in results:
        if args.dry_run:
            tid, target, td, ts, ob, nb, os_, ns, ot, nt, prog = r
            status = "预览"
        else:
            tid, target, td, ts, ob, nb, os_, ns, ot, nt, prog, status = r
        print(f"{tid:<5} {target:<12} {td:<12} {str(ob or ''):<8} {str(nb or ''):<10} "
              f"{str(os_ or ''):<8} {str(ns or ''):<8} {str(ot or ''):<8} {str(nt or ''):<8} "
              f"{prog*100 if prog else 0:<7.1f}% {status:<10}")

    con.close()

    fixed = sum(1 for r in results if "已修复" in r[-1] or "预览" in r[-1])
    skipped = len(results) - fixed
    print(f"\n统计: 总计 {len(results)}, 已处理 {fixed}, 跳过 {skipped}")

    if args.dry_run:
        print("\n🔍 预览模式完成，未修改数据库。使用 --backup 备份后去掉 --dry-run 执行。")
    else:
        after_hash = sha256(DB_PATH)
        print(f"\n  SHA256 (after): {after_hash}")
        print("✅ 校正完成")


if __name__ == "__main__":
    main()
