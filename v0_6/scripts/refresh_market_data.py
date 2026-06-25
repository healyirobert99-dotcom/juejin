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
    """拉取 Tushare 交易日历（动态截止日期：当前日期 + 至少 400 天）"""
    result = {"table": "market_calendar", "rows_added": 0, "ok": True, "error": None}
    try:
        from datetime import timedelta
        today = datetime.now().date()
        end_dt = today + timedelta(days=400)
        start_date = "20000101"
        end_date = end_dt.strftime("%Y%m%d")
        df = pro.trade_cal(start_date=start_date, end_date=end_date)
        if df is None or df.empty:
            result["ok"] = False
            result["error"] = "Tushare 返回空日历"
            return result
        if dry_run:
            result["rows_added"] = len(df)
            result["range"] = f"{start_date} → {end_date}"
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
    """同步 daily_hfq（支持同日补齐）

    优先从 JUEJIN_HFQ_SOURCE_DB 同步；从 local_max 起始（含当天）。
    每日期原子提交。
    """
    result = {"table": "daily_hfq", "rows_added": 0, "ok": True, "error": None}
    source_db = os.getenv("JUEJIN_HFQ_SOURCE_DB")

    if not source_db or not Path(source_db).exists():
        result["ok"] = False
        result["error"] = "未配置 JUEJIN_HFQ_SOURCE_DB，无法同步 daily_hfq"
        return result

    # dry-run 只读预览 — 先用独立连接，关闭后不再使用
    if dry_run:
        src_tmp = sqlite3.connect(source_db)
        src_max = src_tmp.execute("SELECT MAX(trade_date) FROM daily_hfq").fetchone()
        src_count = src_tmp.execute("SELECT COUNT(*) FROM daily_hfq").fetchone()
        sm = src_max[0] if src_max else None
        sc = src_count[0] if src_count else 0
        result["source_max"] = sm
        result["source_rows"] = sc
        if sm:
            local_max = get_table_latest_date("daily_hfq") or "00000000"
            pending = src_tmp.execute(
                "SELECT COUNT(*) FROM daily_hfq WHERE trade_date >= ?",
                (local_max,),
            ).fetchone()
            result["pending_rows"] = pending[0] if pending else 0
        src_tmp.close()
        return result

    try:
        src = sqlite3.connect(source_db)
        dst = sqlite3.connect(str(STOCK_DATA_DB))
        # 确保表存在
        dst.execute("""
            CREATE TABLE IF NOT EXISTS daily_hfq (
                ts_code TEXT NOT NULL, trade_date TEXT NOT NULL,
                close REAL, vol REAL, amount REAL,
                PRIMARY KEY (ts_code, trade_date)
            )
        """)
        dst.commit()

        local_max = get_table_latest_date("daily_hfq") or "00000000"
        # 从 local_max 起始（含当天 — 支持同日补齐）
        src_rows = src.execute(
            "SELECT ts_code, trade_date, close, vol, amount FROM daily_hfq "
            "WHERE trade_date >= ? ORDER BY trade_date",
            (local_max,),
        ).fetchall()

        if not src_rows:
            src.close()
            dst.close()
            return result

        # 按日期分组，逐日事务
        from collections import defaultdict
        by_date: dict[str, list] = defaultdict(list)
        for r in src_rows:
            by_date[r[1]].append(r)

        total_added = 0
        for trade_date in sorted(by_date.keys()):
            batch = by_date[trade_date]
            try:
                dst.execute("BEGIN")
                # 删除目标库该日期数据（重置）
                dst.execute("DELETE FROM daily_hfq WHERE trade_date = ?", (trade_date,))
                # 批量写入
                dst.executemany(
                    "INSERT INTO daily_hfq (ts_code, trade_date, close, vol, amount) VALUES (?, ?, ?, ?, ?)",
                    batch,
                )
                # 验证行数
                cnt = dst.execute(
                    "SELECT COUNT(*) FROM daily_hfq WHERE trade_date = ?", (trade_date,)
                ).fetchone()[0]
                if cnt != len(batch):
                    raise ValueError(f"{trade_date} 写入行数 {cnt} ≠ 源行数 {len(batch)}")
                dst.execute("COMMIT")
                total_added += len(batch)
            except Exception as e:
                dst.execute("ROLLBACK")
                src.close()
                dst.close()
                result["ok"] = False
                result["error"] = f"日期 {trade_date} 同步失败: {e}"
                return result

        result["rows_added"] = total_added
        src.close()
        dst.close()
    except Exception as e:
        if not dry_run and 'src' in dir() and src:
            src.close()
        if not dry_run and 'dst' in dir() and dst:
            dst.close()
        result["ok"] = False
        result["error"] = str(e)

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

    # 终止日期 = 今天或今天以前最近一个已开市日期（不让未来日期）
    today_str = datetime.now().strftime("%Y%m%d")
    con = sqlite3.connect(str(STOCK_DATA_DB))
    if _has_table(con, "market_calendar"):
        end_row = con.execute(
            "SELECT MAX(cal_date) FROM market_calendar WHERE is_open=1 AND cal_date <= ?",
            (today_str,),
        ).fetchone()
        con.close()
        if end_row and end_row[0]:
            end_date = end_row[0]
        else:
            result["ok"] = False
            result["error"] = "无法确定终止日期（无今天或以前的开市日）"
            return result
    else:
        con.close()
        end_date = today_str

    if start_date >= end_date:
        result["rows_added"] = 0
        return result

    if dry_run:
        result["rows_added"] = -1  # 表示预览模式
        result["range"] = f"{start_date} → {end_date}"
        return result

    try:
        con = sqlite3.connect(str(STOCK_DATA_DB))
        if not dry_run:
            _ensure_table(con, """
                CREATE TABLE IF NOT EXISTS stock_daily_raw (
                    ts_code TEXT NOT NULL, trade_date TEXT NOT NULL,
                    close REAL, vol REAL, amount REAL,
                    PRIMARY KEY (ts_code, trade_date)
                )
            """)
            con.execute("CREATE INDEX IF NOT EXISTS idx_stock_daily_raw_code ON stock_daily_raw(ts_code)")
            con.commit()

        total = 0
        cal_con = sqlite3.connect(str(STOCK_DATA_DB))
        trade_dates = cal_con.execute(
            "SELECT cal_date FROM market_calendar WHERE is_open=1 AND cal_date > ? AND cal_date <= ? ORDER BY cal_date",
            (start_date, end_date),
        ).fetchall()
        cal_con.close()

        for (td,) in trade_dates:
            if dry_run:
                total += 1
                continue
            try:
                df = pro.daily(trade_date=td)
                if df is None or df.empty:
                    con.close()
                    result["ok"] = False
                    result["error"] = f"日期 {td} Tushare 返回空数据"
                    return result
                rows = [
                    (r["ts_code"], r["trade_date"],
                     float(r["close"]) if r["close"] is not None else None,
                     float(r["vol"]) if r["vol"] is not None else None,
                     float(r["amount"]) if r["amount"] is not None else None)
                    for _, r in df.iterrows()
                ]
                # 单日期原子事务
                con.execute("BEGIN")
                con.execute("DELETE FROM stock_daily_raw WHERE trade_date = ?", (td,))
                con.executemany(
                    "INSERT INTO stock_daily_raw (ts_code, trade_date, close, vol, amount) VALUES (?, ?, ?, ?, ?)",
                    rows,
                )
                # 验证
                inserted = con.execute(
                    "SELECT COUNT(*) FROM stock_daily_raw WHERE trade_date = ?", (td,)
                ).fetchone()[0]
                if inserted != len(rows):
                    con.execute("ROLLBACK")
                    con.close()
                    result["ok"] = False
                    result["error"] = f"日期 {td} 写入行数 {inserted} ≠ {len(rows)}，回滚"
                    return result
                con.execute("COMMIT")
                total += len(rows)
            except Exception as e:
                try:
                    con.execute("ROLLBACK")
                except Exception:
                    pass
                con.close()
                result["ok"] = False
                result["error"] = f"日期 {td} 拉取失败: {e}"
                return result
            time.sleep(0.3)

        if not dry_run:
            con.close()
        result["rows_added"] = total
    except Exception as e:
        result["ok"] = False
        result["error"] = str(e)

    return result


# ── 4. etf_daily ──

def refresh_etf_daily(pro, dry_run: bool = False) -> dict:
    """增量更新 etf_daily（统一后缀，按日原子提交）

    每日期：
    1. 从 ETF 目录今日全部代码
    2. 开放持仓 ETF 优先
    3. 完整校验后再提交
    4. 任何开放持仓 ETF 失败 → 该日回滚
    """
    result = {"table": "etf_daily", "rows_added": 0, "ok": True, "error": None}

    etf_rows = load_sector_etf_rows()
    etf_codes = list({r["code"] for r in etf_rows if r["code"]})
    if not etf_codes:
        result["ok"] = False
        result["error"] = "sector_etf_map.csv 无有效 ETF 代码"
        return result

    normalized = []
    for c in etf_codes:
        try:
            normalized.append(normalize_etf_ts_code(c))
        except ValueError:
            result.setdefault("warnings", []).append(f"跳过无效 ETF 代码 {c}")

    if not normalized:
        result["ok"] = False
        result["error"] = "所有 ETF 代码均无法标准化"
        return result

    # 获取开放持仓 ETF
    open_etf_codes = set()
    try:
        from v0_6.core import get_position_summary_all
        for pos in get_position_summary_all():
            if pos.get("target_type") == "ETF":
                try:
                    open_etf_codes.add(normalize_etf_ts_code(pos["target"]))
                except ValueError:
                    pass
    except Exception:
        pass

    # 确定待拉取的交易日
    local_max = get_table_latest_date("etf_daily")
    start_date = local_max if (local_max and local_max >= "20150101") else "20150101"

    today_str = datetime.now().strftime("%Y%m%d")
    con = sqlite3.connect(str(STOCK_DATA_DB))
    if _has_table(con, "market_calendar"):
        trade_dates = con.execute(
            "SELECT cal_date FROM market_calendar WHERE is_open=1 AND cal_date > ? AND cal_date <= ? ORDER BY cal_date",
            (start_date, today_str),
        ).fetchall()
    else:
        trade_dates = []
    con.close()

    if dry_run:
        result["rows_added"] = -1
        result["range"] = f"{start_date} → {today_str}"
        result["etf_count"] = len(normalized)
        result["pending_dates"] = len(trade_dates)
        return result

    if not trade_dates:
        result["rows_added"] = 0
        return result

    dst = sqlite3.connect(str(STOCK_DATA_DB))
    _ensure_table(dst, """
        CREATE TABLE IF NOT EXISTS etf_daily (
            ts_code TEXT NOT NULL, trade_date TEXT NOT NULL,
            close REAL, vol REAL, amount REAL,
            PRIMARY KEY (ts_code, trade_date)
        )
    """)
    dst.execute("CREATE INDEX IF NOT EXISTS idx_etf_daily_code ON etf_daily(ts_code)")
    dst.commit()

    total_added = 0
    # 逐日处理
    for (td_str,) in trade_dates:
        # 1) 逐只 ETF 拉取该日数据
        day_rows = []
        day_failed = []
        # 开放持仓 ETF 优先拉取
        ordered_codes = sorted(normalized, key=lambda c: (0 if c in open_etf_codes else 1))
        for ts_code in ordered_codes:
            try:
                df = pro.fund_daily(ts_code=ts_code, start_date=td_str, end_date=td_str)
            except Exception:
                try:
                    df = pro.daily(ts_code=ts_code, start_date=td_str, end_date=td_str)
                except Exception as e:
                    day_failed.append((ts_code, str(e)))
                    continue
            if df is None or df.empty:
                day_failed.append((ts_code, "empty"))
                continue
            row = (ts_code, td_str,
                   float(df["close"].iloc[0]) if df["close"].iloc[0] is not None else None,
                   float(df["vol"].iloc[0]) if df["vol"].iloc[0] is not None else None,
                   float(df["amount"].iloc[0]) if df["amount"].iloc[0] is not None else None)
            day_rows.append(row)
            time.sleep(0.15)

        # 2) 检查开放持仓 ETF 是否全部成功
        open_failed = [c for c, _ in day_failed if c in open_etf_codes]
        if open_failed:
            dst.close()
            result["ok"] = False
            result["error"] = (
                f"日期 {td_str}: 开放持仓 ETF {open_failed} 更新失败，该日回滚"
            )
            result["failed"] = day_failed
            return result

        # 3) 原子写入
        try:
            dst.execute("BEGIN")
            dst.execute("DELETE FROM etf_daily WHERE trade_date = ?", (td_str,))
            dst.executemany(
                "INSERT INTO etf_daily (ts_code, trade_date, close, vol, amount) VALUES (?, ?, ?, ?, ?)",
                day_rows,
            )
            dst.execute("COMMIT")
            total_added += len(day_rows)
        except Exception as e:
            dst.execute("ROLLBACK")
            dst.close()
            result["ok"] = False
            result["error"] = f"日期 {td_str} 写入失败: {e}"
            return result

    dst.close()
    result["rows_added"] = total_added
    if result.get("failed"):
        result["ok"] = False
    return result


# ── 5. 程序化刷新入口（不解析 sys.argv）──

def refresh_all(dry_run: bool = False) -> dict:
    """不解析命令行的程序化刷新，供日报等模块直接调用。

    返回所有子结果的合并 dict。
    不调用 sys.exit()。
    """
    results = {}
    if not STOCK_DATA_DB.exists():
        return {"ok": False, "error": "stock_data.db 不存在"}
    if not TUSHARE_TOKEN:
        return {"ok": False, "error": "TUSHARE_TOKEN 未配置"}

    import tushare as ts
    ts.set_token(TUSHARE_TOKEN)
    pro = ts.pro_api()

    r = refresh_calendar(pro, dry_run=dry_run)
    results["calendar"] = r
    r = refresh_daily_hfq(pro, dry_run=dry_run)
    results["hfq"] = r
    r = refresh_stock_daily_raw(pro, dry_run=dry_run)
    results["stock_raw"] = r
    r = refresh_etf_daily(pro, dry_run=dry_run)
    results["etf"] = r

    ok = all(rr.get("ok", False) for rr in results.values())
    merged = {"ok": ok, "results": results}
    for k, v in results.items():
        merged[k] = v
    return merged


# ── CLI 入口 ──

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

    # 自动验证
    if ok and not args.dry_run:
        try:
            from v0_6.core import get_position_summary_all
            from v0_6.core.market_data import validate_market_data
            validation = validate_market_data(
                datetime.now().strftime("%Y-%m-%d"),
                open_positions=get_position_summary_all(),
            )
            if validation["ok"]:
                print(f"  数据完整性校验通过")
            else:
                print(f"  ⚠️ 数据完整性校验未通过（可能仍有持仓级问题）")
                for err in validation["errors"]:
                    print(f"    {err}")
                ok = False
        except Exception as e:
            print(f"  ⚠️ 自动验证失败: {e}")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
