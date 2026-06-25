"""
统一行情数据模块

集中负责：
- ETF 代码后缀标准化
- 交易日历
- 行情数据完整性校验
- 数据新鲜度校验
"""
from __future__ import annotations

import csv
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from .config import DATA_DIR, DB_PATH, STOCK_DATA_DB, PROJECT_ROOT


# ── 1. ETF 代码后缀统一 ──

def normalize_etf_ts_code(code: str) -> str:
    """ETF 6位代码 → 带市场后缀的标准 ts_code

    5xxxxx → .SH
    1xxxxx → .SZ
    159xxx → .SZ  （深圳 ETF）
    已经带后缀 → 原样返回
    其他 → 抛出 ValueError
    """
    code = code.strip().upper()
    if code.endswith((".SH", ".SZ")):
        return code
    if len(code) != 6 or not code.isdigit():
        raise ValueError(f"无效 ETF 代码格式: {code!r}")
    if code.startswith("5"):
        return code + ".SH"
    if code.startswith(("1", "159")):
        return code + ".SZ"
    raise ValueError(f"无法确定 ETF {code} 的市场后缀")


def normalize_stock_ts_code(code: str) -> str:
    """个股代码 → 带市场后缀的标准 ts_code"""
    code = code.strip().upper()
    if code.endswith((".SH", ".SZ", ".BJ")):
        return code
    if len(code) != 6 or not code.isdigit():
        raise ValueError(f"无效股票代码格式: {code!r}")
    if code.startswith(("6", "9")):
        return code + ".SH"
    if code.startswith(("0", "3", "2")):
        return code + ".SZ"
    if code.startswith("4") or code.startswith("8"):
        return code + ".BJ"
    raise ValueError(f"无法确定股票 {code} 的市场后缀")


# ── 2. 交易日历 ──

def load_market_calendar() -> pd.DataFrame:
    """从 stock_data.db 加载交易日历表，不存在时返回空 DataFrame"""
    if not STOCK_DATA_DB.exists():
        return pd.DataFrame()
    con = sqlite3.connect(str(STOCK_DATA_DB))
    try:
        df = pd.read_sql_query(
            "SELECT cal_date, is_open FROM market_calendar ORDER BY cal_date",
            con,
        )
    except pd.io.sql.DatabaseError:
        df = pd.DataFrame()
    con.close()
    return df


def get_expected_trade_date(requested_date: str) -> str | None:
    """返回 requested_date 当日或之前最近一个已开市日期。

    格式 YYYY-MM-DD。
    无交易日历数据时返回 None（无法判断）。
    """
    cal = load_market_calendar()
    if cal.empty:
        return None
    cal = cal[cal["is_open"] == 1]
    if cal.empty:
        return None
    # 格式化
    cal["cal_date"] = pd.to_datetime(cal["cal_date"])
    target = pd.to_datetime(requested_date)
    eligible = cal[cal["cal_date"] <= target]
    if eligible.empty:
        return None
    return eligible["cal_date"].iloc[-1].strftime("%Y-%m-%d")


# ── 3. 行情表最新日期查询 ──

def get_table_latest_date(table: str) -> str | None:
    """返回 stock_data.db 中某表的最新 trade_date，格式 YYYYMMDD 或 None"""
    if not STOCK_DATA_DB.exists():
        return None
    con = sqlite3.connect(str(STOCK_DATA_DB))
    try:
        row = con.execute(f"SELECT MAX(trade_date) FROM {table}").fetchone()
    except sqlite3.OperationalError:
        con.close()
        return None
    con.close()
    if row and row[0]:
        return str(row[0])
    return None


def get_table_row_count(table: str, trade_date: str = None) -> int:
    """返回某表指定日期的行数；不指定日期时返回总行数"""
    if not STOCK_DATA_DB.exists():
        return 0
    con = sqlite3.connect(str(STOCK_DATA_DB))
    try:
        if trade_date:
            row = con.execute(
                f"SELECT COUNT(*) FROM {table} WHERE trade_date = ?",
                (trade_date.replace("-", ""),),
            ).fetchone()
        else:
            row = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    except sqlite3.OperationalError:
        con.close()
        return 0
    con.close()
    return row[0] if row else 0


# ── 4. 完整性校验 ──

def validate_market_data(requested_date: str, open_positions: list[dict] | None = None) -> dict:
    """验证行情数据的完整性和新鲜度

    返回:
        {
            "ok": bool,
            "requested_date": str,
            "expected_trade_date": str | None,
            "daily_hfq_date": str | None,
            "daily_hfq_count": int,
            "daily_hfq_median_count": float,
            "stock_daily_raw_date": str | None,
            "stock_daily_raw_count": int,
            "etf_daily_date": str | None,
            "etf_daily_count": int,
            "errors": list[str],
            "warnings": list[str],
        }
    """
    errors = []
    warnings = []

    expected = get_expected_trade_date(requested_date)
    if expected is None:
        errors.append("交易日历不可用，无法判断预期交易日")

    daily_hfq_date = get_table_latest_date("daily_hfq")
    stock_raw_date = get_table_latest_date("stock_daily_raw")
    etf_date = get_table_latest_date("etf_daily")

    # daily_hfq 校验
    if daily_hfq_date is None:
        errors.append("daily_hfq 无数据")
    elif expected and daily_hfq_date < expected.replace("-", ""):
        errors.append(f"daily_hfq 最新日期 {daily_hfq_date} 早于预期交易日 {expected}")

    # 完整性：daily_hfq 行数检查
    daily_hfq_count = 0
    daily_hfq_median = 0
    if daily_hfq_date and expected:
        daily_hfq_count = get_table_row_count("daily_hfq", daily_hfq_date)
        # 前5个完整交易日中位数
        if STOCK_DATA_DB.exists():
            con = sqlite3.connect(str(STOCK_DATA_DB))
            rows = con.execute(
                "SELECT trade_date, COUNT(*) as cnt FROM daily_hfq "
                "WHERE trade_date < ? GROUP BY trade_date ORDER BY trade_date DESC LIMIT 5",
                (daily_hfq_date,),
            ).fetchall()
            con.close()
            if rows:
                cnts = [r[1] for r in rows]
                daily_hfq_median = sorted(cnts)[len(cnts) // 2]
                threshold = daily_hfq_median * 0.9
                if daily_hfq_count < threshold:
                    errors.append(
                        f"daily_hfq 当日行数 {daily_hfq_count} 低于正常水平 {daily_hfq_median}×90%={threshold:.0f}，"
                        "可能为部分更新"
                    )

    # stock_daily_raw 校验
    stock_raw_count = 0
    if stock_raw_date is not None:
        stock_raw_count = get_table_row_count("stock_daily_raw", stock_raw_date)
        if expected and stock_raw_date < expected.replace("-", ""):
            errors.append(f"stock_daily_raw 最新日期 {stock_raw_date} 早于预期交易日 {expected}")
    else:
        stock_raw_count = get_table_row_count("stock_daily_raw")

    # etf_daily 校验
    etf_count = 0
    if etf_date is not None:
        etf_count = get_table_row_count("etf_daily", etf_date)
        if expected and etf_date < expected.replace("-", ""):
            errors.append(f"etf_daily 最新日期 {etf_date} 早于预期交易日 {expected}")
    else:
        errors.append("etf_daily 无数据")

    # 开放持仓校验
    if open_positions:
        if STOCK_DATA_DB.exists():
            con = sqlite3.connect(str(STOCK_DATA_DB))
            for pos in open_positions:
                target = pos.get("target", "")
                ttype = pos.get("target_type", "")
                if ttype == "ETF":
                    try:
                        tc = normalize_etf_ts_code(target)
                        row = con.execute(
                            "SELECT MAX(trade_date) FROM etf_daily WHERE ts_code = ? AND trade_date <= ?",
                            (tc, (expected or "99999999").replace("-", "")),
                        ).fetchone()
                        if not row or not row[0]:
                            warnings.append(f"ETF {target}（{tc}）无有效行情")
                    except ValueError:
                        warnings.append(f"ETF {target} 代码无法标准化")
                elif ttype == "STOCK":
                    try:
                        tc = normalize_stock_ts_code(target)
                        row = con.execute(
                            "SELECT MAX(trade_date) FROM stock_daily_raw WHERE ts_code = ? AND trade_date <= ?",
                            (tc, (expected or "99999999").replace("-", "")),
                        ).fetchone()
                        if not row or not row[0]:
                            warnings.append(f"STOCK {target}（{tc}）无未复权行情数据")
                    except ValueError:
                        warnings.append(f"STOCK {target} 代码无法标准化")
            con.close()

    return {
        "ok": len(errors) == 0,
        "requested_date": requested_date,
        "expected_trade_date": expected,
        "daily_hfq_date": daily_hfq_date,
        "daily_hfq_count": daily_hfq_count,
        "daily_hfq_median_count": daily_hfq_median,
        "stock_daily_raw_date": stock_raw_date,
        "stock_daily_raw_count": stock_raw_count,
        "etf_daily_date": etf_date,
        "etf_daily_count": etf_count,
        "errors": errors,
        "warnings": warnings,
    }


# ── 5. 加载 ETF 行级目录（多对多支持）──

def load_sector_etf_rows() -> list[dict]:
    """加载 sector_etf_map.csv 的全部行（每行一条关系，不覆盖）

    同一 ETF 可以出现在多行（如 512170→医疗、512170→CXO）。
    """
    path = DATA_DIR / "sector_etf_map.csv"
    rows = []
    if not path.exists():
        return rows
    with open(path, encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader)
        for row in reader:
            if not row or row[0].startswith("==="):
                continue
            if len(row) < 6 or not row[3].strip() or row[3] == "—":
                continue
            rows.append({
                "code": row[3].strip(),
                "sector": row[0].strip(),
                "sub": row[2].strip(),
                "name": row[4].strip(),
                "index": row[5].strip() if len(row) > 5 else "",
                "scale": row[6].strip() if len(row) > 6 else "",
                "purity": row[7].strip() if len(row) > 7 else "",
                "note": row[8].strip() if len(row) > 8 else "",
            })
    return rows
