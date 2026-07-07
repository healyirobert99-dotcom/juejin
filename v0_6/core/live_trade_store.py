"""
v0.6 实盘交易存储（v6 升级：标的语义 + 金额为主）

- 标的 = 行业（INDUSTRY）| ETF | 个股（STOCK）
- 主输入：金额（amount），价格/股数可选填
- 进度/桶：自动用 ETF 自身后复权价 60d_low 算
- 字段：target / target_type / target_name / amount / price / shares / progress_at_entry / progress_bucket / ...
"""
from __future__ import annotations

import csv
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from .config import DB_PATH, DATA_DIR, STOCK_DATA_DB

SCHEMA = """
CREATE TABLE IF NOT EXISTS live_trades (
    trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
    target TEXT NOT NULL,
    target_type TEXT NOT NULL CHECK(target_type IN ('INDUSTRY', 'ETF', 'STOCK')),
    target_name TEXT,
    action TEXT NOT NULL CHECK(action IN ('BUY', 'ADD', 'REDUCE', 'SELL')),
    amount REAL,
    price REAL,
    shares REAL,
    trade_date TEXT NOT NULL,
    signal_type TEXT,
    position_pct REAL,
    signal_breadth REAL,
    signal_vol_ratio REAL,
    progress_at_entry REAL,
    progress_bucket TEXT,
    stop_threshold REAL,
    take_profit_threshold REAL,
    stop_price REAL,
    take_profit_price REAL,
    reduced_1_3 INTEGER DEFAULT 0,
    notes TEXT,
    closed INTEGER DEFAULT 0,
    close_date TEXT,
    close_price REAL,
    close_pnl_pct REAL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    -- v6.2 退潮减仓字段
    source_industry TEXT,
    source_signal_date TEXT,
    source_signal_priority TEXT,
    source_signal_type TEXT,
    source_signal_ret20 REAL,
    take_profit_reduced_1_3 INTEGER DEFAULT 0,
    retreat_reduced_1_3 INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_live_trades_target ON live_trades(target);
CREATE INDEX IF NOT EXISTS idx_live_trades_target_type ON live_trades(target_type);
CREATE INDEX IF NOT EXISTS idx_live_trades_closed ON live_trades(closed);
CREATE INDEX IF NOT EXISTS idx_live_trades_date ON live_trades(trade_date);
"""


def init_schema() -> None:
    """初始化表结构（幂等 + 自动升级 + v6.1 迁移）

    v6.1 关键变化：
    - industry → target（标的语义化）
    - 加 target_type (INDUSTRY/ETF/STOCK)
    - 加 target_name（自动匹配名称）
    - 加 amount（金额为主输入）
    - 老表自动迁移：industry 值拷到 target，其他给默认值
    """
    con = sqlite3.connect(str(DB_PATH))

    # v6.1 迁移：如果表已存在但没有 target 列，整表迁移
    cur = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='live_trades'"
    )
    table_exists = cur.fetchone() is not None

    if table_exists:
        cur = con.execute("PRAGMA table_info(live_trades)")
        cols = {row[1] for row in cur.fetchall()}
        if "target" not in cols:
            con.executescript("""
            CREATE TABLE live_trades_new (
                trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
                target TEXT NOT NULL,
                target_type TEXT NOT NULL DEFAULT 'INDUSTRY',
                target_name TEXT,
                action TEXT NOT NULL,
                amount REAL,
                price REAL,
                shares REAL,
                trade_date TEXT NOT NULL,
                signal_type TEXT,
                position_pct REAL,
                signal_breadth REAL,
                signal_vol_ratio REAL,
                progress_at_entry REAL,
                progress_bucket TEXT,
                stop_threshold REAL,
                take_profit_threshold REAL,
                stop_price REAL,
                take_profit_price REAL,
                reduced_1_3 INTEGER DEFAULT 0,
                notes TEXT,
                closed INTEGER DEFAULT 0,
                close_date TEXT,
                close_price REAL,
                close_pnl_pct REAL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """)
            con.execute("""
            INSERT INTO live_trades_new
            (trade_id, target, target_type, target_name, action, amount, price, shares,
             trade_date, signal_type, position_pct, signal_breadth, signal_vol_ratio,
             progress_at_entry, progress_bucket, stop_threshold, take_profit_threshold,
             stop_price, take_profit_price, reduced_1_3, notes, closed, close_date,
             close_price, close_pnl_pct, created_at)
            SELECT trade_id, industry, 'INDUSTRY', industry, action, amount, price, shares,
                   trade_date, signal_type, position_pct, signal_breadth, signal_vol_ratio,
                   progress_at_entry, progress_bucket, stop_threshold, take_profit_threshold,
                   stop_price, take_profit_price, reduced_1_3, notes, closed, close_date,
                   close_price, close_pnl_pct, created_at
            FROM live_trades
            """)
            con.execute("DROP TABLE live_trades")
            con.execute("ALTER TABLE live_trades_new RENAME TO live_trades")
            con.commit()
            print("[init_schema] v6.1 迁移完成：industry → target")

    # 标准初始化（CREATE TABLE IF NOT EXISTS + 补 v6.1 列）
    con.executescript(SCHEMA)

    cur = con.execute("PRAGMA table_info(live_trades)")
    existing_cols = {row[1] for row in cur.fetchall()}

    v6_1_new_cols = {
        "target": "TEXT",
        "target_type": "TEXT",
        "target_name": "TEXT",
        "amount": "REAL",
    }
    for col_name, col_type in v6_1_new_cols.items():
        if col_name not in existing_cols:
            con.execute(f"ALTER TABLE live_trades ADD COLUMN {col_name} {col_type}")

    # v6.2 迁移：退潮减仓字段
    v6_2_new_cols = {
        "source_industry": "TEXT",
        "source_signal_date": "TEXT",
        "source_signal_priority": "TEXT",
        "source_signal_type": "TEXT",
        "source_signal_ret20": "REAL",
        "take_profit_reduced_1_3": "INTEGER DEFAULT 0",
        "retreat_reduced_1_3": "INTEGER DEFAULT 0",
    }
    for col_name, col_type in v6_2_new_cols.items():
        if col_name not in existing_cols:
            con.execute(f"ALTER TABLE live_trades ADD COLUMN {col_name} {col_type}")
    con.commit()
    con.close()


# ============== 标的匹配 ==============

def load_sector_etf_map() -> dict:
    """加载 行业→ETF 映射表（兼容旧接口，会因 dict 覆盖丢失多对多）

    新代码请使用 market_data.load_sector_etf_rows()
    """
    from .market_data import load_sector_etf_rows as _rows
    rows = _rows()
    out = {}
    for r in rows:
        out.setdefault(r["code"], r)
    return out


def match_target(input_str: str) -> dict:
    """用户输入（6位/9位/9位.SH/.SZ/名称）→ 标的标准化

    优先级：
    1. 6 位数字（默认 ETF，需用 sector_etf_map 验证）
    2. 9 位带后缀：.SH/.SZ → 个股
    3. 文字 → 模糊匹配 ETF 名称或行业

    Returns:
        {
            "target": "512800",  # 标准 key
            "target_type": "ETF" | "STOCK" | "INDUSTRY",
            "target_name": "华宝中证银行ETF",
            "sector": "金融",  # 仅 ETF/INDUSTRY 有
            "sub": "银行",
        }
    """
    s = input_str.strip()
    if not s:
        return {"target": "", "target_type": "INDUSTRY", "target_name": "", "error": "空输入"}

    etf_map = load_sector_etf_map()

    # Case 1: 9 位带后缀
    if "." in s:
        # 599800.SH / 000001.SZ
        if len(s) == 9 and s[6:] in (".SH", ".SZ"):
            symbol = s[:6]
            # 查 stock_basic
            con = sqlite3.connect(str(DB_PATH))
            row = con.execute(
                "SELECT ts_code, name, industry FROM stock_basic WHERE ts_code = ? OR symbol = ?",
                (s, symbol),
            ).fetchone()
            con.close()
            if row:
                return {
                    "target": row[0],  # 完整 ts_code
                    "target_type": "STOCK",
                    "target_name": row[1],
                    "industry": row[2],
                }
            # 视为 ETF（如果 sector_etf_map 有）
            if symbol in etf_map:
                m = etf_map[symbol]
                return {
                    "target": symbol,
                    "target_type": "ETF",
                    "target_name": m["name"],
                    "sector": m["sector"],
                    "sub": m["sub"],
                }
        return {"target": s, "target_type": "INDUSTRY", "target_name": s, "error": "未知代码"}

    # Case 2: 6 位数字
    if s.isdigit() and len(s) == 6:
        # 默认按 ETF 查
        if s in etf_map:
            m = etf_map[s]
            return {
                "target": s,
                "target_type": "ETF",
                "target_name": m["name"],
                "sector": m["sector"],
                "sub": m["sub"],
            }
        # 否则查 stock_basic
        con = sqlite3.connect(str(DB_PATH))
        row = con.execute(
            "SELECT ts_code, name, industry FROM stock_basic WHERE symbol = ?",
            (s,),
        ).fetchone()
        con.close()
        if row:
            return {
                "target": row[0],
                "target_type": "STOCK",
                "target_name": row[1],
                "industry": row[2],
            }
        return {"target": s, "target_type": "INDUSTRY", "target_name": s, "error": f"代码 {s} 不在 ETF/股票库"}

    # Case 3: 文字
    # 模糊匹配 ETF 名
    for code, m in etf_map.items():
        if m["name"] and (s in m["name"] or s == m["sub"]):
            return {
                "target": code,
                "target_type": "ETF",
                "target_name": m["name"],
                "sector": m["sector"],
                "sub": m["sub"],
            }
    # 匹配 stock_basic 行业或股票名
    con = sqlite3.connect(str(DB_PATH))
    row = con.execute(
        "SELECT ts_code, name, industry FROM stock_basic WHERE name LIKE ? OR industry = ? LIMIT 1",
        (f"%{s}%", s),
    ).fetchone()
    con.close()
    if row:
        return {
            "target": row[0],
            "target_type": "STOCK",
            "target_name": row[1],
            "industry": row[2],
        }

    # 当作行业
    return {"target": s, "target_type": "INDUSTRY", "target_name": s, "sector": s}


# ============== 进度计算 ==============

def calc_progress_for_etf(etf_code: str, end_date: str, lookback: int = 60) -> Optional[dict]:
    """拉 ETF 自身后复权价算 60 日最低 → 进度

    Returns:
        {"current": 0.85, "low_60d": 0.80, "progress": 0.0625, "as_of": "2026-06-24"}
        或 None（数据不足）
    """
    if not STOCK_DATA_DB.exists():
        return None
    # 自动补 .SH/.SZ 后缀
    candidates = [etf_code]
    if "." not in etf_code:
        if etf_code.startswith(("5", "1")):
            candidates.insert(0, etf_code + ".SH")
            candidates.append(etf_code + ".SZ")
    elif not etf_code.endswith((".SH", ".SZ")):
        candidates = [etf_code]

    con = sqlite3.connect(str(STOCK_DATA_DB))
    rows = None
    for c in candidates:
        # 优先 etf_daily（带 .SH/.SZ 后缀）
        rows = con.execute(
            "SELECT trade_date, close FROM etf_daily WHERE ts_code = ? AND trade_date <= ? ORDER BY trade_date DESC LIMIT ?",
            (c, end_date.replace("-", ""), lookback + 1),
        ).fetchall()
        if rows:
            break
        # fallback daily_hfq
        rows = con.execute(
            'SELECT trade_date, close FROM "daily_hfq" WHERE ts_code = ? AND trade_date <= ? ORDER BY trade_date DESC LIMIT ?',
            (c, end_date.replace("-", ""), lookback + 1),
        ).fetchall()
        if rows:
            break

    if not rows or len(rows) < 5:
        return None

    closes = [r[1] for r in rows]
    current = closes[0]
    low_60d = min(closes)
    progress = (current - low_60d) / low_60d if low_60d > 0 else 0
    return {
        "current": current,
        "low_60d": low_60d,
        "progress": progress,
        "as_of": rows[0][0],
    }


def calc_progress_for_industry(industry_name: str, end_date: str, lookback: int = 60) -> Optional[dict]:
    """按行业指数日线计算进度桶

    通过 industry_index_map.csv 将细分行业名映射到申万一级行业指数代码，
    从 industry_index_daily 表取 close 计算进度。

    Returns:
        {"current": 0.85, "low_60d": 0.80, "progress": 0.0625, "as_of": "20260701", "index_code": "801150.SI"}
        或 None（无映射/数据不足）
    """
    if not STOCK_DATA_DB.exists():
        return None

    from v0_6.core.market_data import load_industry_index_map
    index_map = load_industry_index_map()
    entry = index_map.get(industry_name)
    if not entry:
        return None

    idx_code = entry["index_code"].replace(".SI", "")
    con = sqlite3.connect(str(STOCK_DATA_DB))
    rows = con.execute(
        "SELECT trade_date, close FROM industry_index_daily "
        "WHERE ts_code = ? AND trade_date <= ? ORDER BY trade_date DESC LIMIT ?",
        (idx_code, end_date.replace("-", ""), lookback + 1),
    ).fetchall()
    con.close()

    if not rows or len(rows) < 5:
        return None

    closes = [r[1] for r in rows]
    current = closes[0]
    low_60d = min(closes)
    progress = (current - low_60d) / low_60d if low_60d and low_60d > 0 else 0
    return {
        "current": current,
        "low_60d": low_60d,
        "progress": progress,
        "as_of": rows[0][0],
        "index_code": idx_code,
    }


# ============== CRUD ==============

def add_trade(
    target: Optional[str] = None,
    action: str = "BUY",
    trade_date: Optional[str] = None,
    industry: Optional[str] = None,  # 向后兼容：旧调用 industry=
    target_type: str = "INDUSTRY",
    target_name: Optional[str] = None,
    amount: Optional[float] = None,
    price: Optional[float] = None,
    shares: Optional[float] = None,
    signal_type: Optional[str] = None,
    position_pct: Optional[float] = None,
    progress: Optional[float] = None,
    progress_bucket: Optional[str] = None,
    stop_threshold: Optional[float] = None,
    take_profit_threshold: Optional[float] = None,
    stop_price: Optional[float] = None,
    take_profit_price: Optional[float] = None,
    notes: Optional[str] = None,
    # v6.2 来源行业
    source_industry: Optional[str] = None,
    source_signal_date: Optional[str] = None,
    source_signal_priority: Optional[str] = None,
    source_signal_type: Optional[str] = None,
    source_signal_ret20: Optional[float] = None,
) -> int:
    """录入一笔交易，返回 trade_id

    兼容：industry= 旧接口（自动映射到 target）
    """
    # 向后兼容
    if target is None and industry is not None:
        target = industry
    if target is None:
        raise ValueError("target/industry 必填")
    target = target.strip()
    if not target:
        raise ValueError("target/industry 不能为空")

    from .gold_signal_v6 import get_bucket, calc_stop_price, calc_take_profit_price

    init_schema()
    if action not in ("BUY", "ADD", "REDUCE", "SELL"):
        raise ValueError(f"action 必须是 BUY/ADD/REDUCE/SELL，当前: {action}")
    if target_type not in ("INDUSTRY", "ETF", "STOCK"):
        raise ValueError(f"target_type 必须是 INDUSTRY/ETF/STOCK，当前: {target_type}")

    # 推算 price / shares
    if amount is not None and amount > 0:
        if price and not shares:
            shares = round(amount / price, 2)
        elif shares and not price:
            price = round(amount / shares, 4)
        elif not price and not shares:
            raise ValueError("提供 amount 时必须同时提供 price 或 shares 之一")
    else:
        if not (price and shares):
            raise ValueError("必须提供 amount，或 (price + shares)")

    amount = amount or (price * shares)

    # 若 progress 已给则算桶
    if progress is not None and progress_bucket is None:
        bucket = get_bucket(progress)
        progress_bucket = bucket["name"]
        stop_threshold = stop_threshold or bucket["stop"]
        take_profit_threshold = take_profit_threshold or bucket["take_profit"]
        stop_price = stop_price or calc_stop_price(price, progress)
        take_profit_price = take_profit_price or calc_take_profit_price(price, progress)

    con = sqlite3.connect(str(DB_PATH))
    cur = con.execute(
        """
        INSERT INTO live_trades
        (target, target_type, target_name, action, amount, price, shares,
         trade_date, signal_type, position_pct, progress_at_entry, progress_bucket,
         stop_threshold, take_profit_threshold, stop_price, take_profit_price,
         notes, source_industry, source_signal_date, source_signal_priority,
         source_signal_type, source_signal_ret20)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            target, target_type, target_name, action, amount, price, shares,
            trade_date, signal_type, position_pct, progress, progress_bucket,
            stop_threshold, take_profit_threshold, stop_price, take_profit_price,
            notes,
            source_industry, source_signal_date, source_signal_priority,
            source_signal_type, source_signal_ret20,
        ),
    )
    trade_id = cur.lastrowid
    con.commit()
    con.close()
    return trade_id


def list_open_positions() -> List[dict]:
    """列出所有未平仓的 BUY/ADD 记录（逐行返回，不聚合）

    注意：新代码应优先使用 get_position_summary_all() 获取净持仓。
    """
    init_schema()
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """
        SELECT * FROM live_trades
        WHERE action IN ('BUY', 'ADD') AND closed = 0
        ORDER BY trade_id DESC
        """
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


def get_position_net(target: str) -> dict:
    """获取一个 target 的净持仓

    avg_cost = 累计买入金额 ÷ 累计买入股数（金额加权）
    REDUCE/SELL 不改变成本，只减少股数。
    """
    init_schema()
    con = sqlite3.connect(str(DB_PATH))
    buys = con.execute(
        "SELECT SUM(shares) AS s, SUM(amount) AS a, MIN(trade_date) AS d FROM live_trades "
        "WHERE target = ? AND action IN ('BUY', 'ADD') AND closed = 0",
        (target,),
    ).fetchone()
    sells = con.execute(
        "SELECT SUM(shares) AS s FROM live_trades "
        "WHERE target = ? AND action IN ('REDUCE', 'SELL') AND closed = 0",
        (target,),
    ).fetchone()
    con.close()
    buy_shares = buys[0] or 0
    buy_amount = buys[1] or 0
    sell_shares = sells[0] or 0
    net_shares = buy_shares - sell_shares
    if net_shares <= 0:
        return {"target": target, "net_shares": 0, "net_amount": 0, "avg_cost": 0, "first_buy": None}
    avg_cost = buy_amount / buy_shares  # 金额加权，不受卖出影响
    return {
        "target": target,
        "net_shares": net_shares,
        "net_amount": avg_cost * net_shares,  # 剩余持仓的账面成本
        "avg_cost": avg_cost,
        "first_buy": buys[2],
    }


def get_position_summary_all() -> List[dict]:
    """按 target 汇总所有未平仓标的的净持仓

    返回每个 target 一条记录，包含：
    - target, target_type, target_name
    - total_shares: 净股数（BUY+ADD 减 REDUCE+SELL，仅统计当前未平仓周期）
    - avg_cost: 金额加权平均成本
    - first_buy: 首次买入日期
    - has_reduced: 当前周期是否曾减仓
    - 监控用参数（取自首笔 BUY/ADD）：progress_bucket, stop_threshold, take_profit_threshold
    净持仓 ≤ 0 的标的不返回。

    注意：
    - REDUCE/SELL 也检查 closed=0，确保上一周期的卖出不扣减新的一轮持仓
    - has_reduced 只检查当前未平仓周期
    """
    init_schema()
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """
        SELECT
            target,
            target_type,
            MIN(target_name) AS target_name,
            SUM(CASE WHEN action IN ('BUY', 'ADD') AND closed = 0 THEN shares ELSE 0 END) AS buy_shares,
            SUM(CASE WHEN action IN ('BUY', 'ADD') AND closed = 0 THEN amount ELSE 0 END) AS buy_amount,
            SUM(CASE WHEN action IN ('REDUCE', 'SELL') AND closed = 0 THEN shares ELSE 0 END) AS sell_shares,
            MIN(CASE WHEN action IN ('BUY', 'ADD') AND closed = 0 THEN trade_date END) AS first_buy,
            MAX(CASE WHEN action IN ('REDUCE', 'SELL') AND closed = 0 THEN 1 ELSE 0 END) AS has_reduced_in_cycle
        FROM live_trades
        GROUP BY target, target_type
        HAVING buy_shares - sell_shares > 0
        ORDER BY first_buy
        """
    ).fetchall()
    con.close()

    results = []
    for r in rows:
        buy_shares = r["buy_shares"] or 0
        buy_amount = r["buy_amount"] or 0
        sell_shares = r["sell_shares"] or 0
        net_shares = buy_shares - sell_shares
        if net_shares <= 0:
            continue
        avg_cost = buy_amount / buy_shares if buy_shares > 0 else 0

        # 取最早一笔未平仓 BUY/ADD 的监控参数（同一条记录）
        con2 = sqlite3.connect(str(DB_PATH))
        first = con2.execute(
            "SELECT progress_bucket, stop_threshold, take_profit_threshold, progress_at_entry "
            "FROM live_trades "
            "WHERE target = ? AND action IN ('BUY', 'ADD') AND closed = 0 "
            "ORDER BY trade_id ASC LIMIT 1",
            (r["target"],),
        ).fetchone()
        con2.close()
        bucket = first[0] if first else None
        stop = first[1] if first else None
        tp = first[2] if first else None
        prog = first[3] if first else None

        results.append({
            "target": r["target"],
            "target_type": r["target_type"],
            "target_name": r["target_name"],
            "total_shares": net_shares,
            "avg_cost": avg_cost,
            "first_buy": r["first_buy"],
            "has_reduced": bool(r["has_reduced_in_cycle"]),
            "progress_bucket": bucket,
            "stop_threshold": stop,
            "take_profit_threshold": tp,
            "progress_at_entry": prog,
        })

        # 附带 source_industry（从首笔 BUY 中读取）+ 一致性检查
        con3 = sqlite3.connect(str(DB_PATH))
        si = con3.execute(
            "SELECT source_industry, source_signal_date, source_signal_priority, "
            "source_signal_type, source_signal_ret20, "
            "take_profit_reduced_1_3, retreat_reduced_1_3 "
            "FROM live_trades WHERE target=? AND action IN ('BUY','ADD') AND closed=0 "
            "ORDER BY trade_id ASC LIMIT 1",
            (r["target"],),
        ).fetchone()

        # 一致性检查：同一周期内所有 BUY 的 source_industry 是否一致
        all_si = con3.execute(
            "SELECT DISTINCT source_industry, source_signal_type "
            "FROM live_trades WHERE target=? AND action IN ('BUY','ADD') AND closed=0",
            (r["target"],),
        ).fetchall()
        con3.close()

        source_industries = {row[0] for row in all_si if row[0]}
        source_types = {row[1] for row in all_si if row[1]}

        if si:
            results[-1]["source_industry"] = si[0]
            results[-1]["source_signal_date"] = si[1]
            results[-1]["source_signal_priority"] = si[2]
            results[-1]["source_signal_type"] = si[3]
            results[-1]["source_signal_ret20"] = si[4]
            results[-1]["take_profit_reduced"] = bool(si[5])
            results[-1]["retreat_reduced"] = bool(si[6])
            results[-1]["source_industry_status"] = (
                "OK" if len(source_industries) == 1
                else "CONFLICT" if len(source_industries) > 1
                else "MISSING"
            )
        else:
            results[-1]["source_industry"] = None
            results[-1]["source_signal_date"] = None
            results[-1]["source_signal_type"] = None
            results[-1]["take_profit_reduced"] = False
            results[-1]["retreat_reduced"] = False
            results[-1]["source_industry_status"] = "MISSING"
    return results


def close_trade(trade_id: int, close_price: float, close_date: str) -> None:
    """平仓一笔交易（单笔）"""
    con = sqlite3.connect(str(DB_PATH))
    trade = con.execute(
        "SELECT price, shares FROM live_trades WHERE trade_id = ? AND closed = 0",
        (trade_id,),
    ).fetchone()
    if not trade:
        con.close()
        raise ValueError(f"trade_id {trade_id} 不存在或已平仓")
    entry_price, shares = trade
    pnl_pct = (close_price - entry_price) / entry_price if entry_price else 0
    con.execute(
        "UPDATE live_trades SET closed = 1, close_date = ?, close_price = ?, close_pnl_pct = ? WHERE trade_id = ?",
        (close_date, close_price, pnl_pct, trade_id),
    )
    con.commit()
    con.close()


def close_trade_by_target(target: str, close_price: float, close_date: str) -> dict:
    """平仓某 target 的全部未平仓 BUY/ADD 记录

    - 每行按各自买入价计算 close_pnl_pct
    - 同时关闭该 target 的 REDUCE/SELL 记录，避免影响下一轮持仓
    - 没有未平仓记录时抛出 ValueError（拒绝重复清仓）
    - 返回汇总：{target, total_shares, total_pnl, avg_pnl_pct}
    """
    con = sqlite3.connect(str(DB_PATH))
    open_rows = con.execute(
        "SELECT trade_id, price, shares FROM live_trades "
        "WHERE target = ? AND action IN ('BUY', 'ADD') AND closed = 0",
        (target,),
    ).fetchall()
    if not open_rows:
        con.close()
        raise ValueError(f"标的 '{target}' 当前无未平仓持仓，不允许清仓")

    total_shares = 0
    total_pnl = 0
    for row in open_rows:
        entry_price = row[1]
        shares = row[2]
        pnl_pct = (close_price - entry_price) / entry_price if entry_price else 0
        pnl_amount = (close_price - entry_price) * shares
        con.execute(
            "UPDATE live_trades SET closed = 1, close_date = ?, close_price = ?, close_pnl_pct = ? WHERE trade_id = ?",
            (close_date, close_price, pnl_pct, row[0]),
        )
        total_shares += shares
        total_pnl += pnl_amount

    # 同时关闭该 target 的所有未关闭 REDUCE/SELL（避免影响下轮持仓）
    con.execute(
        "UPDATE live_trades SET closed = 1, close_date = ? "
        "WHERE target = ? AND action IN ('REDUCE', 'SELL') AND closed = 0",
        (close_date, target),
    )

    con.commit()
    con.close()
    # avg_entry = close_price - (total_pnl / total_shares)
    avg_entry = close_price - (total_pnl / total_shares) if total_shares > 0 else 0
    avg_pnl_pct = (close_price - avg_entry) / avg_entry if avg_entry else 0
    return {
        "target": target,
        "total_shares": total_shares,
        "total_pnl": round(total_pnl, 2),
        "avg_pnl_pct": round(avg_pnl_pct, 4),
    }
