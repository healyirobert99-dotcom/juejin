"""
一次性迁移：使用未复权行情重算现有未平仓 STOCK 的进度桶和止损止盈参数。

用法：
    python rebuild_open_stock_rules.py                # 执行校正（自动备份）
    python rebuild_open_stock_rules.py --dry-run      # 预览拟修改记录
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
from v0_6.core.gold_signal_v6 import get_bucket
from v0_6.core.market_data import normalize_stock_ts_code


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="重算未平仓 STOCK 的进度桶和止损止盈")
    parser.add_argument("--dry-run", action="store_true", help="预览模式，不写库")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"❌ 交易数据库不存在: {DB_PATH}")
        sys.exit(1)
    if not STOCK_DATA_DB.exists():
        print(f"❌ 行情数据库不存在: {STOCK_DATA_DB}")
        sys.exit(1)

    # ── 行情连接 ──
    stock_con = sqlite3.connect(str(STOCK_DATA_DB))
    # ── 交易连接 ──
    trade_con = sqlite3.connect(str(DB_PATH))
    trade_con.row_factory = sqlite3.Row

    # 查出所有未平仓 STOCK
    rows = trade_con.execute(
        "SELECT * FROM live_trades WHERE target_type='STOCK' AND action IN ('BUY', 'ADD') AND closed=0 "
        "ORDER BY trade_id"
    ).fetchall()

    if not rows:
        print("✅ 无未平仓 STOCK 记录，无需校正")
        stock_con.close()
        trade_con.close()
        return

    print(f"\n待校正 STOCK 记录: {len(rows)} 笔\n")

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
            results.append({
                "trade_id": tid, "target": target, "trade_date": trade_date,
                "status": "error", "reason": "代码无法标准化",
            })
            continue

        # 查 stock_daily_raw（用行情 DB 连接）
        raw_rows = stock_con.execute(
            "SELECT trade_date, close FROM stock_daily_raw "
            "WHERE ts_code = ? AND trade_date <= ? "
            "ORDER BY trade_date DESC LIMIT 60",
            (ts_code, trade_date.replace("-", "")),
        ).fetchall()

        if not raw_rows or len(raw_rows) < 5:
            results.append({
                "trade_id": tid, "target": target, "trade_date": trade_date,
                "ts_code": ts_code, "status": "skipped",
                "reason": f"stock_daily_raw 数据不足 ({len(raw_rows)} 条)",
            })
            continue

        closes = [r[1] for r in raw_rows]
        current_raw = closes[0]
        low_60d = min(closes)
        progress = (current_raw - low_60d) / low_60d if low_60d > 0 else 0
        bucket = get_bucket(progress)

        stop_price = entry_price * (1 + bucket["stop"])
        tp_price = entry_price * (1 + bucket["take_profit"])

        old_bucket = row["progress_bucket"]
        old_stop = row["stop_threshold"]
        old_tp = row["take_profit_threshold"]

        results.append({
            "trade_id": tid, "target": target, "trade_date": trade_date,
            "ts_code": ts_code,
            "old_bucket": old_bucket, "new_bucket": bucket["name"],
            "old_stop": old_stop, "new_stop": bucket["stop"],
            "old_tp": old_tp, "new_tp": bucket["take_profit"],
            "progress": progress,
            "stop_price": stop_price, "take_profit_price": tp_price,
            "status": "preview" if args.dry_run else "pending",
        })

    if not args.dry_run:
        # ── 强制备份 ──
        backup_path = DB_PATH.with_suffix(
            f".sqlite3.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
        shutil.copy2(DB_PATH, backup_path)
        if not backup_path.exists():
            print("❌ 备份失败，终止操作")
            stock_con.close()
            trade_con.close()
            sys.exit(1)

        before_hash = sha256(DB_PATH)
        print(f"📦 自动备份: {backup_path}")
        print(f"  SHA256 (before): {before_hash}")
        print(f"  备份 SHA256: {sha256(backup_path)}")

        # ── 事务更新 ──
        try:
            trade_con.execute("BEGIN")
            for r in results:
                if r["status"] != "pending":
                    continue
                trade_con.execute(
                    "UPDATE live_trades SET "
                    "progress_at_entry=?, progress_bucket=?, "
                    "stop_threshold=?, take_profit_threshold=?, "
                    "stop_price=?, take_profit_price=? "
                    "WHERE trade_id=?",
                    (r["progress"], r["new_bucket"],
                     r["new_stop"], r["new_tp"],
                     r["stop_price"], r["take_profit_price"],
                     r["trade_id"]),
                )
                r["status"] = "fixed"
            trade_con.commit()
        except Exception as e:
            trade_con.rollback()
            print(f"❌ 迁移失败，已回滚: {e}")
            stock_con.close()
            trade_con.close()
            sys.exit(1)

        after_hash = sha256(DB_PATH)
        print(f"  SHA256 (after): {after_hash}")

    # ── 输出报告 ──
    print(f"\n{'ID':<5} {'标的':<12} {'入场日':<12} {'原桶':<8} {'新桶':<10} {'原止损':<8} {'新止损':<8} {'原止盈':<8} {'新止盈':<8} {'进度':<8} {'状态':<10}")
    print("-" * 110)
    for r in results:
        prog = r.get("progress", 0)
        print(f"{r['trade_id']:<5} {r['target']:<12} {r['trade_date']:<12} "
              f"{str(r.get('old_bucket', '') or ''):<8} {str(r.get('new_bucket', '') or ''):<10} "
              f"{str(r.get('old_stop', '') or ''):<8} {str(r.get('new_stop', '') or ''):<8} "
              f"{str(r.get('old_tp', '') or ''):<8} {str(r.get('new_tp', '') or ''):<8} "
              f"{prog*100 if prog else 0:<7.1f}% {r['status']:<10}")

    fixed = sum(1 for r in results if r["status"] in ("fixed", "preview"))
    skipped = sum(1 for r in results if r["status"] == "skipped")
    errors = sum(1 for r in results if r["status"] == "error")
    print(f"\n统计: 总计 {len(results)}, 已处理 {fixed}, 跳过 {skipped}, 错误 {errors}")

    if args.dry_run:
        print("\n🔍 预览模式完成，未修改数据库。去掉 --dry-run 执行真实迁移。")
    else:
        print(f"\n✅ 校正完成")

    stock_con.close()
    trade_con.close()


if __name__ == "__main__":
    main()
