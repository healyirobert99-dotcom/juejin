"""一次性拉取 86 只 ETF 日线（10 年）写入 stock_data.db::etf_daily

Usage:
    cd D:\\zhuxian-catch-v0_6
    python scripts/fetch_etf_daily.py
"""
import csv
import os
import sqlite3
import sys
import time
from pathlib import Path

V0_6_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(V0_6_ROOT))

from dotenv import load_dotenv

load_dotenv(V0_6_ROOT / ".env")
import tushare as ts  # noqa: E402

STOCK_DATA_DB = V0_6_ROOT / "stock_data" / "stock_data.db"
SECTOR_MAP = V0_6_ROOT / "data" / "sector_etf_map.csv"


def main():
    if not STOCK_DATA_DB.exists():
        print(f"❌ {STOCK_DATA_DB} 不存在")
        return 1
    if "TUSHARE_TOKEN" not in os.environ:
        print("❌ .env 里没有 TUSHARE_TOKEN")
        return 1

    con = sqlite3.connect(str(STOCK_DATA_DB))
    con.execute("""
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

    codes = []
    with open(SECTOR_MAP, encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader)
        for row in reader:
            if not row or row[0].startswith("==="):
                continue
            if not row[3].strip() or row[3] == "—":
                continue
            codes.append(row[3].strip())
    print(f"待拉 ETF: {len(codes)} 只")

    ts.set_token(os.environ["TUSHARE_TOKEN"])
    pro = ts.pro_api()

    total = 0
    failed = []
    for i, code in enumerate(codes):
        full = code + (".SH" if code.startswith(("5", "1")) else ".SZ")
        # 先试 fund_daily（基金接口）
        df = None
        try:
            df = pro.fund_daily(ts_code=full, start_date="20150101", end_date="20260624")
        except Exception:
            pass
        # 退路：pro.daily（通用行情，ETF 也能用）
        if df is None or df.empty:
            try:
                df = pro.daily(ts_code=full, start_date="20150101", end_date="20260624")
            except Exception as e:
                failed.append((full, str(e)))
                continue
        if df is None or df.empty:
            failed.append((full, "empty"))
            continue
        rows = []
        for _, r in df.iterrows():
            # 不同接口的字段名略有差异，兼容处理
            tc = r.get("ts_code") or full
            td = r.get("trade_date") or r.get("cal_date")
            cl = r.get("close")
            vo = r.get("vol")
            am = r.get("amount")
            rows.append(
                (tc, td, float(cl) if cl is not None else None,
                 float(vo) if vo is not None else None,
                 float(am) if am is not None else None)
            )
        con.executemany(
            "INSERT OR REPLACE INTO etf_daily (ts_code, trade_date, close, vol, amount) VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        total += len(rows)
        if (i + 1) % 10 == 0:
            print(f"  ... 已拉 {i+1}/{len(codes)}，累计 {total} 行")
            con.commit()
        time.sleep(0.3)
    con.commit()
    con.close()

    print(f"\n✅ 完成：{total} 行 ETF 日线写入 stock_data.db::etf_daily")
    if failed:
        print(f"⚠️ 失败 {len(failed)} 只：{failed[:5]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
