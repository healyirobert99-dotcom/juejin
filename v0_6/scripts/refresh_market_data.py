"""
统一行情数据刷新脚本

使用 Tushare 增量更新：
- daily_hfq（从 JUEJIN_HFQ_SOURCE_DB 同步，或通过 Tushare daily API）
- stock_daily_raw（未复权日线）
- etf_daily（ETF 日线）
- market_calendar（交易日历）

用法：
    python refresh_market_data.py                     # 完整刷新
    python refresh_market_data.py --dry-run           # 只预览
    python refresh_market_data.py --no-calendar       # 跳过交易日历
    python refresh_market_data.py --no-hfq            # 跳过 daily_hfq
    python refresh_market_data.py --no-stock-raw      # 跳过 stock_daily_raw
    python refresh_market_data.py --no-etf            # 跳过 etf_daily
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from pathlib import Path

V0_6_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(V0_6_ROOT))

import sqlite3

from dotenv import load_dotenv

load_dotenv()

from v0_6.core.config import STOCK_DATA_DB
from v0_6.core.market_data import (
    normalize_etf_ts_code,
    get_table_latest_date,
    load_sector_etf_rows,
)

TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")


# ── 辅助 ──

def _has_table(con, name):
    row = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _ensure_table(con, sql):
    con.execute(sql)
    con.commit()


# ── 1. 交易日历 ──

def refresh_calendar(pro, dry_run: bool = False) -> dict:
    """拉取 Tushare 交易日历（2000~2026）"""
    result = {"table": "market_calendar", "rows_added": 0, "ok": True, "error": None}
    try:
        df = pro.trade_cal(start_date="20000101", end_date="20261231")
        if df is None or df.empty:
            result["ok"] = False
            result["error"] = "Tushare 返回空日历"
            return result
        if dry_run:
            result["rows_added"] = len(df)
            return result

        con = sqlite3.connect(str(STOCK_DATA_DB))
        _ensure_table(con, """
            CREATE TABLE IF NOT EXISTS market_calendar (
                cal_date TEXT PRIMARY KEY,
                is_open INTEGER NOT NULL
            )
        """)
        for _, r in df.iterrows():
            con.execute(
                "INSERT OR REPLACE INTO market_calendar (cal_date, is_open) VALUES (?, ?)",
                (r["cal_date"], int(r["is_open"])),
            )
        con.commit()
        result["rows_added"] = len(df)
        con.close()
    except Exception as e:
        result["ok"] = False
        result["error"] = str(e)
    return result


# ── 2. daily_hfq ──

def refresh_daily_hfq(pro, dry_run: bool = False) -> dict:
    """同步 daily_hfq

    优先从 JUEJIN_HFQ_SOURCE_DB 同步；未配置时尝试 Tushare daily API。
    """
    result = {"table": "daily_hfq", "rows_added": 0, "ok": True, "error": None}
    source_db = os.getenv("JUEJIN_HFQ_SOURCE_DB")

    if source_db and Path(source_db).exists():
        # 从源数据库同步
        try:
            src = sqlite3.connect(source_db)
            dst = sqlite3.connect(str(STOCK_DATA_DB))

            if not _has_table(dst, "daily_hfq"):
                dst.execute("""
                    CREATE TABLE daily_hfq (
                        ts_code TEXT NOT NULL,
                        trade_date TEXT NOT NULL,
                        close REAL,
                        vol REAL,
                        amount REAL,
                        PRIMARY KEY (ts_code, trade_date)
                    )
                """)
                dst.commit()

            local_max = get_table_latest_date("daily_hfq") or "00000000"
            src_rows = src.execute(
                "SELECT ts_code, trade_date, close, vol, amount FROM daily_hfq "
                "WHERE trade_date > ? ORDER BY trade_date",
                (local_max,),
            ).fetchall()
            if src_rows:
                dst.executemany(
                    "INSERT OR REPLACE INTO daily_hfq (ts_code, trade_date, close, vol, amount) VALUES (?, ?, ?, ?, ?)",
                    src_rows,
                )
                dst.commit()
                result["rows_added"] = len(src_rows)
            src.close()
            dst.close()
        except Exception as e:
            result["ok"] = False
            result["error"] = f"HFQ 源同步失败: {e}"
        return result

    # 无源 DB — 从 Tushare 拉
    result["ok"] = False
    result["error"] = "未配置 JUEJIN_HFQ_SOURCE_DB，且当前环境不支持通过 Tushare daily API 重建 daily_hfq（避免改变回测口径）"
    return result


# ── 3. stock_daily_raw ──

def refresh_stock_daily_raw(pro, dry_run: bool = False) -> dict:
    """增量更新 stock_daily_raw（未复权日线）

    按交易日拉取全部 A 股，不逐只股票。
    """
    result = {"table": "stock_daily_raw", "rows_added": 0, "ok": True, "error": None}

    # 确定增量起点
    local_max = get_table_latest_date("stock_daily_raw")
    if local_max:
        start_date = local_max
    else:
        # 首次初始化：回填 120 个交易日
        cal_rows = []
        con = sqlite3.connect(str(STOCK_DATA_DB))
        if _has_table(con, "market_calendar"):
            cal_rows = con.execute(
                "SELECT cal_date FROM market_calendar WHERE is_open=1 ORDER BY cal_date DESC LIMIT 120",
            ).fetchall()
        con.close()
        if not cal_rows:
            result["ok"] = False
            result["error"] = "需要先刷新交易日历（先运行 refresh_calendar）"
            return result
        start_date = cal_rows[-1][0]

    # 确定终止日期
    con = sqlite3.connect(str(STOCK_DATA_DB))
    if _has_table(con, "market_calendar"):
        end_row = con.execute(
            "SELECT MAX(cal_date) FROM market_calendar WHERE is_open=1"
        ).fetchone()
        con.close()
        end_date = end_row[0] if end_row and end_row[0] else datetime.now().strftime("%Y%m%d")
    else:
        con.close()
        end_date = datetime.now().strftime("%Y%m%d")

    if start_date >= end_date:
        result["rows_added"] = 0
        return result

    if dry_run:
        result["rows_added"] = -1  # 表示预览模式
        result["range"] = f"{start_date} → {end_date}"
        return result

    try:
        con = sqlite3.connect(str(STOCK_DATA_DB))
        _ensure_table(con, """
            CREATE TABLE IF NOT EXISTS stock_daily_raw (
                ts_code TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                close REAL,
                vol REAL,
                amount REAL,
                PRIMARY KEY (ts_code, trade_date)
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_stock_daily_raw_code ON stock_daily_raw(ts_code)")
        con.commit()

        total = 0
        # 按交易日循环拉取
        cal_con = sqlite3.connect(str(STOCK_DATA_DB))
        trade_dates = cal_con.execute(
            "SELECT cal_date FROM market_calendar WHERE is_open=1 AND cal_date > ? AND cal_date <= ? ORDER BY cal_date",
            (start_date, end_date),
        ).fetchall()
        cal_con.close()

        for (td,) in trade_dates:
            try:
                df = pro.daily(trade_date=td)
                if df is None or df.empty:
                    continue
                rows = [
                    (r["ts_code"], r["trade_date"],
                     float(r["close"]) if r["close"] is not None else None,
                     float(r["vol"]) if r["vol"] is not None else None,
                     float(r["amount"]) if r["amount"] is not None else None)
                    for _, r in df.iterrows()
                ]
                con.executemany(
                    "INSERT OR REPLACE INTO stock_daily_raw (ts_code, trade_date, close, vol, amount) VALUES (?, ?, ?, ?, ?)",
                    rows,
                )
                total += len(rows)
            except Exception as e:
                con.rollback()
                con.close()
                result["ok"] = False
                result["error"] = f"日期 {td} 拉取失败: {e}"
                return result
            time.sleep(0.3)

        con.commit()
        result["rows_added"] = total
        con.close()
    except Exception as e:
        result["ok"] = False
        result["error"] = str(e)

    return result


# ── 4. etf_daily ──

def refresh_etf_daily(pro, dry_run: bool = False) -> dict:
    """增量更新 etf_daily（使用统一后缀函数）"""
    result = {"table": "etf_daily", "rows_added": 0, "ok": True, "error": None}

    etf_rows = load_sector_etf_rows()
    # 去重取 ETF 代码
    etf_codes = list({r["code"] for r in etf_rows if r["code"]})
    if not etf_codes:
        result["ok"] = False
        result["error"] = "sector_etf_map.csv 无有效 ETF 代码"
        return result

    # 标准化后缀
    normalized = []
    for c in etf_codes:
        try:
            normalized.append(normalize_etf_ts_code(c))
        except ValueError:
            result["warnings"] = result.get("warnings", [])
            result["warnings"].append(f"跳过无效 ETF 代码 {c}")

    if not normalized:
        result["ok"] = False
        result["error"] = "所有 ETF 代码均无法标准化"
        return result

    local_max = get_table_latest_date("etf_daily")
    if local_max is None or local_max < "20000101":
        start_date = "20150101"
    else:
        start_date = local_max

    end_date = datetime.now().strftime("%Y%m%d")

    if dry_run:
        result["rows_added"] = -1
        result["range"] = f"{start_date} → {end_date}"
        result["etf_count"] = len(normalized)
        return result

    try:
        con = sqlite3.connect(str(STOCK_DATA_DB))
        _ensure_table(con, """
            CREATE TABLE IF NOT EXISTS etf_daily (
                ts_code TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                close REAL,
                vol REAL,
                amount REAL,
                PRIMARY KEY (ts_code, trade_date)
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_etf_daily_code ON etf_daily(ts_code)")
        con.commit()

        total = 0
        failed = []
        for ts_code in normalized:
            try:
                df = pro.fund_daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
            except Exception:
                # fallback: pro.daily
                try:
                    df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
                except Exception as e:
                    failed.append((ts_code, str(e)))
                    continue
            if df is None or df.empty:
                failed.append((ts_code, "empty"))
                continue
            rows = [
                (r["ts_code"], r["trade_date"],
                 float(r["close"]) if r["close"] is not None else None,
                 float(r["vol"]) if r["vol"] is not None else None,
                 float(r["amount"]) if r["amount"] is not None else None)
                for _, r in df.iterrows()
            ]
            con.executemany(
                "INSERT OR REPLACE INTO etf_daily (ts_code, trade_date, close, vol, amount) VALUES (?, ?, ?, ?, ?)",
                rows,
            )
            total += len(rows)
            time.sleep(0.3)

        con.commit()
        result["rows_added"] = total
        if failed:
            result["failed"] = failed
            result["ok"] = False
            result["error"] = f"{len(failed)}/{len(normalized)} 个 ETF 更新失败"
        con.close()
    except Exception as e:
        result["ok"] = False
        result["error"] = str(e)

    return result


# ── 主入口 ──

def main():
    import argparse
    parser = argparse.ArgumentParser(description="统一行情数据刷新")
    parser.add_argument("--dry-run", action="store_true", help="预览模式，不写库")
    parser.add_argument("--no-calendar", action="store_true", help="跳过交易日历")
    parser.add_argument("--no-hfq", action="store_true", help="跳过 daily_hfq")
    parser.add_argument("--no-stock-raw", action="store_true", help="跳过 stock_daily_raw")
    parser.add_argument("--no-etf", action="store_true", help="跳过 etf_daily")
    args = parser.parse_args()

    if not STOCK_DATA_DB.exists():
        print("❌ stock_data.db 不存在")
        sys.exit(1)

    if not TUSHARE_TOKEN:
        print("⚠️ TUSHARE_TOKEN 未配置，无法通过 Tushare API 更新数据")
        print("   如需更新 stock_daily_raw / etf_daily / market_calendar，请配置 .env")
        if args.dry_run:
            print("   （dry-run 模式继续）")
        else:
            sys.exit(1)

    import tushare as ts
    ts.set_token(TUSHARE_TOKEN)
    pro = ts.pro_api()

    print(f"\n{'='*60}")
    print(f"  行情数据刷新 | {'预览' if args.dry_run else '执行'}")
    print(f"{'='*60}")

    results = {}

    if not args.no_calendar:
        print("\n--- 交易日历 ---")
        r = refresh_calendar(pro, dry_run=args.dry_run)
        results["calendar"] = r
        print(f"  {'✓' if r['ok'] else '✗'} {r['rows_added']} 行" + (f" | {r.get('error', '')}" if not r['ok'] else ""))

    if not args.no_hfq:
        print("\n--- daily_hfq ---")
        r = refresh_daily_hfq(pro, dry_run=args.dry_run)
        results["hfq"] = r
        print(f"  {'✓' if r['ok'] else '✗'} {r['rows_added']} 行" + (f" | {r.get('error', '')}" if not r['ok'] else ""))

    if not args.no_stock_raw:
        print("\n--- stock_daily_raw ---")
        r = refresh_stock_daily_raw(pro, dry_run=args.dry_run)
        results["stock_raw"] = r
        print(f"  {'✓' if r['ok'] else '✗'} {r['rows_added']} 行" + (f" | {r.get('error', '')}" if not r['ok'] else ""))

    if not args.no_etf:
        print("\n--- etf_daily ---")
        r = refresh_etf_daily(pro, dry_run=args.dry_run)
        results["etf"] = r
        print(f"  {'✓' if r['ok'] else '✗'} {r['rows_added']} 行" + (f" | {r.get('error', '')}" if not r['ok'] else ""))

    print(f"\n{'='*60}")
    ok = all(r.get("ok", False) for r in results.values())
    print(f"  整体状态: {'✅ 全部成功' if ok else '❌ 部分失败'}")
    print(f"{'='*60}")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
