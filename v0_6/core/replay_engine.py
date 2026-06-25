"""
全链路逐日历史回放引擎

完全隔离模式：
- 源行情库只读（source_stock_data.db）
- 临时回放行情库（replay_stock_data.db）
- 临时回放交易库（replay_trade.sqlite3）

禁止联网、禁止读写真实数据库、不得在生产代码中执行。
"""
from __future__ import annotations

import csv
import hashlib
import json
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

# ── 路径（调用侧确保）──
V0_6_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = V0_6_ROOT / "artifacts" / "replay"

# ── 只保留一个全局状态用于注入 DB 路径 ──
_ORIG_DB_PATH = None
_ORIG_STOCK_DB = None


class ReplayContext:
    """回放上下文：持有临时库路径，负责注入/恢复生产模块的 DB_PATH"""

    def __init__(self, source_db: Path, replay_dir: Path):
        self.source_db = source_db
        self.replay_dir = replay_dir
        self.trade_db = replay_dir / "replay_trade.sqlite3"
        self.stock_db = replay_dir / "replay_stock_data.db"
        self.start_date: str | None = None
        self.end_date: str | None = None
        self.warmup_days = 120
        # 日交易记录
        self.days: list[dict] = []
        self.orders: list[dict] = []
        self.trades: list[dict] = []
        self.signals_log: list[dict] = []
        self.monitoring_log: list[dict] = []
        self.blocked_days: list[dict] = []
        self.invariant_violations: list[dict] = []
        # 待执行指令队列（T日信号 → T+1执行）
        self.pending_orders: list[dict] = []
        # 回放参考本金
        self.reference_capital = 1_000_000.0

    def setup(self):
        """创建隔离数据库、注入 DB_PATH"""
        global _ORIG_DB_PATH, _ORIG_STOCK_DB
        import v0_6.core.config as cfg
        _ORIG_DB_PATH = cfg.DB_PATH
        _ORIG_STOCK_DB = cfg.STOCK_DATA_DB

        self.replay_dir.mkdir(parents=True, exist_ok=True)

        # 1) 创建空回放行情库，只复制结构
        shutil.copy2(self.source_db, self.stock_db)

        # 2) 清空行情表（保留交易日历），只清空实际存在的表
        con = sqlite3.connect(str(self.stock_db))
        existing = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        for tbl in ["daily_hfq", "stock_daily_raw", "etf_daily"]:
            if tbl in existing:
                con.execute(f"DELETE FROM {tbl}")
        con.commit()
        con.close()

        # 3) 创建空回放交易库
        from v0_6.core import init_schema
        import v0_6.core.config as cfg_cls
        import v0_6.core.live_trade_store as lts
        cfg_cls.DB_PATH = self.trade_db
        cfg_cls.STOCK_DATA_DB = self.stock_db
        lts.DB_PATH = self.trade_db
        init_schema()

    def teardown(self):
        """恢复生产模块的 DB_PATH"""
        global _ORIG_DB_PATH, _ORIG_STOCK_DB
        if _ORIG_DB_PATH and _ORIG_STOCK_DB:
            import v0_6.core.config as cfg
            import v0_6.core.live_trade_store as lts
            import v0_6.core.monitor_v6 as mon
            import v0_6.core.market_data as md
            import v0_6.core.signal_b as sig
            cfg.DB_PATH = _ORIG_DB_PATH
            cfg.STOCK_DATA_DB = _ORIG_STOCK_DB
            lts.DB_PATH = _ORIG_DB_PATH
            mon.STOCK_DATA_DB = _ORIG_STOCK_DB
            md.STOCK_DATA_DB = _ORIG_STOCK_DB
            sig.DB_PATH = _ORIG_DB_PATH
            sig.STOCK_DATA_DB = _ORIG_STOCK_DB

    def inject_day_data(self, trade_date: str):
        """从源库复制指定日期的行情到回放库"""
        con_src = sqlite3.connect(str(self.source_db))
        con_dst = sqlite3.connect(str(self.stock_db))

        for tbl in ["daily_hfq", "stock_daily_raw", "etf_daily"]:
            # 检查目标库是否有该表
            if tbl not in {r[0] for r in con_dst.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}:
                continue
            try:
                rows = con_src.execute(
                    f"SELECT * FROM {tbl} WHERE trade_date = ?", (trade_date.replace("-", ""),)
                ).fetchall()
                if rows:
                    cols = [d[1] for d in con_src.execute(f"PRAGMA table_info({tbl})").fetchall()]
                    placeholders = ",".join("?" * len(cols))
                    col_names = ",".join(cols)
                    con_dst.executemany(
                        f"INSERT INTO {tbl} ({col_names}) VALUES ({placeholders})", rows
                    )
                    con_dst.commit()
            except sqlite3.OperationalError:
                pass

        con_src.close()
        con_dst.close()

    def inject_warmup(self, first_trade_date: str):
        """注入回放起始前的预热数据（至少 120 个交易日）"""
        con_src = sqlite3.connect(str(self.source_db))
        con_dst = sqlite3.connect(str(self.stock_db))

        # 取交易日历或 daily_hfq 唯一日期
        if con_src.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='market_calendar'"
        ).fetchone():
            cal_dates = con_src.execute(
                "SELECT cal_date FROM market_calendar WHERE is_open=1 "
                "AND cal_date < ? ORDER BY cal_date DESC LIMIT ?",
                (first_trade_date.replace("-", ""), self.warmup_days),
            ).fetchall()
        else:
            cal_dates = con_src.execute(
                "SELECT DISTINCT trade_date FROM daily_hfq "
                "WHERE trade_date < ? ORDER BY trade_date DESC LIMIT ?",
                (first_trade_date.replace("-", ""), self.warmup_days),
            ).fetchall()

        warmup_dates = sorted([r[0] for r in cal_dates])
        for td in warmup_dates:
            for tbl in ["daily_hfq", "stock_daily_raw", "etf_daily"]:
                if tbl not in {r[0] for r in con_dst.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()}:
                    continue
                try:
                    rows = con_src.execute(
                        f"SELECT * FROM {tbl} WHERE trade_date = ?", (td,)
                    ).fetchall()
                    if rows:
                        cols = [d[1] for d in con_src.execute(f"PRAGMA table_info({tbl})").fetchall()]
                        placeholders = ",".join("?" * len(cols))
                        col_names = ",".join(cols)
                        con_dst.executemany(
                            f"INSERT INTO {tbl} ({col_names}) VALUES ({placeholders})", rows
                        )
                except sqlite3.OperationalError:
                    pass
            con_dst.commit()

        con_src.close()
        con_dst.close()

    def determine_range(self) -> tuple[str, str]:
        """自动确定回放日期范围"""
        con = sqlite3.connect(str(self.source_db))

        # 各表可能不存在
        def _max_of(table, col="trade_date"):
            try:
                r = con.execute(f"SELECT MAX({col}) FROM {table}").fetchone()
                return r[0] if r and r[0] else None
            except sqlite3.OperationalError:
                return None

        max_cal = _max_of("market_calendar", "cal_date") if con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='market_calendar'"
        ).fetchone() else None
        max_hfq = _max_of("daily_hfq")
        max_raw = _max_of("stock_daily_raw")
        max_etf = _max_of("etf_daily")

        # 如果某表不存在，从其他表推断
        if max_cal is None:
            con.close()
            return _fallback_range(self.source_db)

        con.close()

        end = min(d for d in [max_cal, max_hfq, max_raw, max_etf] if d)
        end_str = f"{end[:4]}-{end[4:6]}-{end[6:]}"

        # 起始 = 预热后第一个交易日
        con2 = sqlite3.connect(str(self.source_db))
        warmup_start_row = con2.execute(
            "SELECT cal_date FROM market_calendar WHERE is_open=1 "
            "ORDER BY cal_date ASC LIMIT 1"
        ).fetchone()
        con2.close()
        if warmup_start_row:
            ws = warmup_start_row[0]
            ws_str = f"{ws[:4]}-{ws[4:6]}-{ws[6:]}"
            start = (
                pd.to_datetime(ws_str) + pd.Timedelta(days=self.warmup_days * 2)
            ).strftime("%Y-%m-%d")
        else:
            start = end_str

        return start, end_str

    def current_stock_db(self) -> Path:
        return self.stock_db

    def current_trade_db(self) -> Path:
        return self.trade_db

    def inject_prod_paths(self):
        """让生产模块指向回放数据库（含 signal_b）"""
        import v0_6.core.config as cfg
        import v0_6.core.live_trade_store as lts
        import v0_6.core.monitor_v6 as mon
        import v0_6.core.market_data as md
        import v0_6.core.signal_b as sig
        cfg.DB_PATH = self.trade_db
        cfg.STOCK_DATA_DB = self.stock_db
        lts.DB_PATH = self.trade_db
        mon.STOCK_DATA_DB = self.stock_db
        md.STOCK_DATA_DB = self.stock_db
        sig.DB_PATH = self.trade_db
        sig.STOCK_DATA_DB = self.stock_db

    def sha256(self, path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _fallback_range(source_db: Path) -> tuple[str, str]:
    """从 daily_hfq 推断日期范围"""
    con = sqlite3.connect(str(source_db))
    r = con.execute("SELECT MIN(trade_date), MAX(trade_date) FROM daily_hfq").fetchone()
    con.close()
    if r and r[0] and r[1]:
        s = f"{r[0][:4]}-{r[0][4:6]}-{r[0][6:]}"
        e = f"{r[1][:4]}-{r[1][4:6]}-{r[1][6:]}"
        return s, e
    raise RuntimeError("无法确定回放日期范围（源库无数据）")


def run_replay(ctx: ReplayContext, max_days: int | None = None) -> dict:
    """执行完整回放

    max_days: 如果设置，只跑前 N 个交易日（冒烟模式）
    """
    ctx.inject_prod_paths()

    from v0_6.core import (
        init_schema, get_position_summary_all, get_position_net,
        monitor_all_positions_v6, add_trade,
    )
    from v0_6.core.market_data import (
        validate_market_data, get_expected_trade_date,
    )
    from v0_6.scripts.run_daily_v1_html import get_today_signals, get_signal_data_date, render_html
    from v0_6.core.etf_matcher import match_industry_to_etfs
    from v0_6.core.gold_signal_v6 import get_position_pct

    init_schema()

    # 确定日期范围
    start_str, end_str = ctx.determine_range()
    ctx.start_date = start_str
    ctx.end_date = end_str

    print(f"\n回放范围: {start_str} → {end_str}")

    # 注入预热数据
    print(f"注入预热数据（{ctx.warmup_days} 个交易日）...")
    ctx.inject_warmup(start_str)

    # 生成交易日序列
    con = sqlite3.connect(str(ctx.source_db))
    if con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='market_calendar'"
    ).fetchone():
        trade_dates = con.execute(
            "SELECT cal_date FROM market_calendar WHERE is_open=1 "
            "AND cal_date >= ? AND cal_date <= ? ORDER BY cal_date",
            (start_str.replace("-", ""), end_str.replace("-", "")),
        ).fetchall()
    else:
        # 没有日历表时从 daily_hfq 取唯一交易日
        trade_dates = con.execute(
            "SELECT DISTINCT trade_date FROM daily_hfq "
            "WHERE trade_date >= ? AND trade_date <= ? ORDER BY trade_date",
            (start_str.replace("-", ""), end_str.replace("-", "")),
        ).fetchall()
    con.close()

    date_list = [f"{r[0][:4]}-{r[0][4:6]}-{r[0][6:]}" for r in trade_dates]

    if max_days:
        date_list = date_list[:max_days]
        print(f"冒烟模式: 仅回放前 {max_days} 个交易日")

    print(f"总计 {len(date_list)} 个交易日\n")

    total_days = len(date_list)

    for idx, current_date in enumerate(date_list):
        day_rec = {
            "replay_date": current_date,
            "expected_trade_date": None,
            "daily_hfq_date": None,
            "stock_daily_raw_date": None,
            "etf_daily_date": None,
            "data_gate_ok": False,
            "data_errors": [],
            "position_errors": [],
            "signals": [],
            "selected_etfs": [],
            "pending_orders_before": len(ctx.pending_orders),
            "filled_orders": [],
            "unfilled_orders": [],
            "positions_before": len(get_position_summary_all()),
            "positions_after": 0,
            "monitoring_actions": [],
            "pending_orders_after": 0,
            "blocked": False,
        }

        # 第一步：注入当天行情
        ctx.inject_day_data(current_date)

        # 第二步：数据门禁
        open_pos = get_position_summary_all()
        validation = validate_market_data(current_date, open_positions=open_pos)
        day_rec["expected_trade_date"] = validation.get("expected_trade_date")
        day_rec["daily_hfq_date"] = validation.get("daily_hfq_date")
        day_rec["stock_daily_raw_date"] = validation.get("stock_daily_raw_date")
        day_rec["etf_daily_date"] = validation.get("etf_daily_date")
        day_rec["data_errors"] = validation.get("errors", [])
        day_rec["position_errors"] = validation.get("position_errors", [])
        day_rec["data_gate_ok"] = validation["ok"]

        if not validation["ok"]:
            day_rec["blocked"] = True
            ctx.blocked_days.append({
                "date": current_date,
                "errors": validation.get("errors", []),
                "position_errors": validation.get("position_errors", []),
            })

        # 第三步：执行前一交易日的待成交指令（当天成交）
        fills = []
        unfills = []

        # 如果数据门禁失败，跳过全部待成交订单
        if not validation["ok"]:
            for order in list(ctx.pending_orders):
                unfills.append({
                    **order,
                    "status": "UNFILLED_DATA_BLOCKED",
                    "failure_reason": f"数据阻断日 {current_date} 不执行订单",
                })
                order["status"] = "UNFILLED_DATA_BLOCKED"
                ctx.pending_orders.remove(order)
        else:
            for order in list(ctx.pending_orders):
                target = order["target"]
                target_type = order["target_type"]
                action = order["action"]
                price = None
                tbl = "etf_daily" if target_type == "ETF" else "stock_daily_raw" if target_type == "STOCK" else None
                if tbl:
                    con_tmp = sqlite3.connect(str(ctx.stock_db))
                    # 尝试原始代码和后缀
                    for suffix in [target, target + ".SH", target + ".SZ", target.replace(".SH", "").replace(".SZ", "")]:
                        try:
                            candidates = [suffix]
                            if "." not in suffix:
                                try:
                                    from v0_6.core.market_data import normalize_etf_ts_code, normalize_stock_ts_code
                                    norm = normalize_etf_ts_code if target_type == "ETF" else normalize_stock_ts_code
                                    candidates.append(norm(suffix))
                                except Exception:
                                    pass
                            found = None
                            for c in candidates:
                                row = con_tmp.execute(
                                    f"SELECT close FROM {tbl} WHERE ts_code = ? AND trade_date = ?",
                                    (c, current_date.replace("-", "")),
                                ).fetchone()
                                if row:
                                    found = row[0]
                                    break
                            if found is not None:
                                price = found
                                break
                        except Exception:
                            continue
                    con_tmp.close()

                if price is None:
                    unfills.append({
                        **order,
                        "status": "UNFILLED_DATA_BLOCKED",
                        "failure_reason": f"T+1={current_date} 无行情",
                    })
                    order["status"] = "UNFILLED_DATA_BLOCKED"
                    ctx.pending_orders.remove(order)
                    continue

                shares = order["shares"]
                amount = shares * price

                if action in ("BUY", "ADD"):
                    # 计算 T+1 日的进度桶（基于 T+1 收盘价）
                    from v0_6.core.market_data import normalize_etf_ts_code, normalize_stock_ts_code
                    norm_fn = normalize_etf_ts_code if target_type == "ETF" else normalize_stock_ts_code
                    try:
                        ts_code = norm_fn(target)
                    except ValueError:
                        ts_code = target

                    # 查 60 日最低价（截止 T+1 日）
                    lookback = 60
                    con_tmp2 = sqlite3.connect(str(ctx.stock_db))
                    hist = con_tmp2.execute(
                        f"SELECT close FROM {tbl} WHERE ts_code LIKE ? AND trade_date <= ? "
                        f"ORDER BY trade_date DESC LIMIT {lookback}",
                        (ts_code + "%", current_date.replace("-", "")),
                    ).fetchall()
                    con_tmp2.close()
                    if hist and len(hist) >= 5:
                        closes = [r[0] for r in hist]
                        low_60d = min(closes)
                        progress = (price - low_60d) / low_60d if low_60d > 0 else 0
                        from v0_6.core.gold_signal_v6 import get_bucket
                        bucket_info = get_bucket(progress)
                        progress_bucket = bucket_info["name"]
                        stop_pct = bucket_info["stop"]
                        tp_pct = bucket_info["take_profit"]
                        stop_price = price * (1 + stop_pct)
                        tp_price = price * (1 + tp_pct)
                    else:
                        progress = None
                        progress_bucket = None
                        stop_pct = None
                        tp_pct = None
                        stop_price = None
                        tp_price = None

                    from v0_6.core import add_trade
                    try:
                        trade_id = add_trade(
                            target=target,
                            target_type=target_type,
                            target_name=order.get("target_name", target),
                            action=action,
                            amount=round(amount, 2),
                            price=price,
                            shares=shares,
                            trade_date=current_date,
                            progress=progress,
                            progress_bucket=progress_bucket,
                            stop_threshold=stop_pct,
                            take_profit_threshold=tp_pct,
                            stop_price=stop_price,
                            take_profit_price=tp_price,
                            notes=f"replay #{order.get('signal_date', '')}→{current_date}",
                        )
                        fills.append({
                            "signal_date": order.get("signal_date", ""),
                            "fill_date": current_date,
                            "target": target,
                            "target_type": target_type,
                            "action": action,
                            "shares": shares,
                            "price": price,
                            "amount": round(amount, 2),
                            "status": "FILLED",
                            "trade_id": trade_id,
                        })
                        order["status"] = "FILLED"
                        order["fill_date"] = current_date
                        order["price"] = price
                        order["amount"] = round(amount, 2)
                        order["trade_id"] = trade_id
                    except Exception as e:
                        unfills.append({
                            **order,
                            "status": "UNFILLED_ERROR",
                            "failure_reason": str(e),
                        })
                elif action == "REDUCE":
                    from v0_6.core import add_trade
                    try:
                        trade_id = add_trade(
                            target=target,
                            target_type=target_type,
                            target_name=order.get("target_name", target),
                            action="REDUCE",
                            amount=round(amount, 2),
                            price=price,
                            shares=shares,
                            trade_date=current_date,
                            notes=f"replay reduce #{order.get('signal_date', '')}",
                        )
                        # 标记 reduced_1_3
                        from v0_6.core.config import DB_PATH as _DB
                        con_r = sqlite3.connect(str(_DB))
                        con_r.execute("UPDATE live_trades SET reduced_1_3 = 1 WHERE target = ?", (target,))
                        con_r.commit()
                        con_r.close()
                        fills.append({
                            "signal_date": order.get("signal_date", ""),
                            "fill_date": current_date,
                            "target": target, "target_type": target_type,
                            "action": "REDUCE", "shares": shares,
                            "price": price, "amount": round(amount, 2),
                            "status": "FILLED",
                        })
                        order["status"] = "FILLED"
                    except Exception as e:
                        unfills.append({**order, "status": "UNFILLED_ERROR", "failure_reason": str(e)})
                elif action == "SELL":
                    from v0_6.core import close_trade_by_target
                    try:
                        summary = close_trade_by_target(target, price, current_date)
                        # SELL 记录已由 close_trade_by_target 关闭，不再额外 add_trade
                        fills.append({
                            "signal_date": order.get("signal_date", ""),
                            "fill_date": current_date,
                            "target": target, "target_type": target_type,
                            "action": "SELL", "shares": summary.get("total_shares", 0),
                            "price": price, "amount": summary.get("total_shares", 0) * price,
                            "status": "FILLED",
                            "pnl": summary.get("total_pnl", 0),
                        })
                        order["status"] = "FILLED"
                        order["fill_date"] = current_date
                    except Exception as e:
                        unfills.append({**order, "status": "UNFILLED_ERROR", "failure_reason": str(e)})

                ctx.pending_orders.remove(order)

        # 记录成交到 ctx.trades
        for f in fills:
            ctx.trades.append(f)

        day_rec["filled_orders"] = fills
        day_rec["unfilled_orders"] = unfills

        # 第四步：生成当天信号
        signals_today = None
        if validation["ok"]:
            sd_date = get_signal_data_date(current_date)
            if sd_date:
                signals_today = get_today_signals(sd_date)
                if signals_today is not None and not signals_today.empty:
                    for _, sig in signals_today.iterrows():
                        sig_rec = {
                            "date": current_date,
                            "industry": sig.get("industry", ""),
                            "priority": sig.get("priority", "FIRST"),
                            "breadth": float(sig.get("breadth_at_signal", 0)),
                            "vol_ratio": float(sig.get("vol_ratio_at_signal", 0)),
                        }
                        day_rec["signals"].append(sig_rec)
                        ctx.signals_log.append(sig_rec)

                        # ETF 匹配
                        etf_r = match_industry_to_etfs(sig.get("industry", ""))
                        if etf_r.get("best"):
                            best = etf_r["best"]
                            day_rec["selected_etfs"].append(best.get("code", ""))

                            # 生成 BUY 指令（T+1 执行）
                            from v0_6.core.gold_signal_v6 import get_position_pct
                            pos_pct = get_position_pct(0.05,
                                is_repeat=(sig.get("priority") == "REPEAT"))
                            suggested_amount = ctx.reference_capital * pos_pct

                            # 查该 ETF 现价
                            price_for_shares = None
                            con_tmp = sqlite3.connect(str(ctx.stock_db))
                            row = con_tmp.execute(
                                "SELECT close FROM etf_daily WHERE ts_code LIKE ? AND trade_date = ?",
                                (best.get("code", "") + "%", current_date.replace("-", "")),
                            ).fetchone()
                            con_tmp.close()
                            if row:
                                price_for_shares = row[0]
                            if price_for_shares and price_for_shares > 0:
                                suggested_shares = suggested_amount / price_for_shares
                            else:
                                suggested_shares = suggested_amount / 1.0  # fallback

                            order = {
                                "signal_date": current_date,
                                "target": best.get("code", ""),
                                "target_type": "ETF",
                                "target_name": best.get("name", ""),
                                "action": "BUY",
                                "shares": round(suggested_shares, 2),
                                "suggested_position_pct": pos_pct,
                                "status": "PENDING",
                            }
                            ctx.pending_orders.append(order)
                            ctx.orders.append(order)

        # 第五步：持仓监控
        monitoring_results = []
        if validation["ok"]:
            monitoring_results = monitor_all_positions_v6(current_date)
            for mr in monitoring_results:
                action = mr.get("action", "HOLD")
                day_rec["monitoring_actions"].append({
                    "target": mr.get("target", ""),
                    "target_type": mr.get("target_type", ""),
                    "action": action,
                    "priority": mr.get("priority", ""),
                    "return_pct": mr.get("return_pct", 0),
                })
                ctx.monitoring_log.append({
                    "date": current_date,
                    "target": mr.get("target", ""),
                    "target_type": mr.get("target_type", ""),
                    "action": action,
                    "priority": mr.get("priority", ""),
                })

                # REDUCE_1_3 → 下一交易日指令
                if action == "REDUCE_1_3":
                    target = mr.get("target", "")
                    ttype = mr.get("target_type", "")
                    net = get_position_net(target)
                    reduce_shares = round(net["net_shares"] / 3, 2)
                    if reduce_shares > 0:
                        order = {
                            "signal_date": current_date,
                            "target": target,
                            "target_type": ttype,
                            "target_name": mr.get("target_name", ""),
                            "action": "REDUCE",
                            "shares": reduce_shares,
                            "status": "PENDING",
                        }
                        ctx.pending_orders.append(order)
                        ctx.orders.append(order)

                # CLEAR → 下一交易日全部卖出
                if action == "CLEAR":
                    target = mr.get("target", "")
                    ttype = mr.get("target_type", "")
                    net = get_position_net(target)
                    if net["net_shares"] > 0:
                        order = {
                            "signal_date": current_date,
                            "target": target,
                            "target_type": ttype,
                            "target_name": mr.get("target_name", ""),
                            "action": "SELL",
                            "shares": net["net_shares"],
                            "status": "PENDING",
                        }
                        ctx.pending_orders.append(order)
                        ctx.orders.append(order)

        day_rec["positions_after"] = len(get_position_summary_all())
        day_rec["pending_orders_after"] = len(ctx.pending_orders)

        ctx.days.append(day_rec)

        # 不变量检查
        _check_invariants(ctx, current_date)

        if (idx + 1) % 20 == 0 or idx == 0 or idx == total_days - 1:
            blocked = "⚠️" if not validation["ok"] else "  "
            pnl_pct = 0.0
            print(f"  [{idx+1:4d}/{total_days}] {current_date} {blocked} "
                  f"信号={len(day_rec['signals']):2d} "
                  f"持仓={day_rec['positions_after']:2d} "
                  f"成交={len(fills)} "
                  f"待执={len(ctx.pending_orders)}")

    # 最终报告
    _write_report(ctx)
    _write_trades_csv(ctx)
    _write_daily_account_csv(ctx)
    _write_signals_csv(ctx)
    _write_orders_csv(ctx)
    _write_monitoring_actions_csv(ctx)
    _write_blocked_days_csv(ctx)
    _write_invariant_violations_csv(ctx)

    result = {
        "total_days": total_days,
        "blocked_days": len(ctx.blocked_days),
        "total_signals": len(ctx.signals_log),
        "total_orders": len(ctx.orders),
        "total_trades": len(ctx.trades),
        "invariant_violations": len(ctx.invariant_violations),
        "final_positions": len(get_position_summary_all()),
    }
    return result


def _check_invariants(ctx: ReplayContext, today: str):
    """逐日不变量检查"""
    from v0_6.core import get_position_summary_all, get_position_net

    summaries = get_position_summary_all()

    # 净股数 >= 0
    for s in summaries:
        if s["total_shares"] < 0:
            ctx.invariant_violations.append({
                "date": today,
                "check": "net_shares_non_negative",
                "target": s["target"],
                "detail": f"net_shares={s['total_shares']}",
            })

    # 一致性: get_position_net 与 get_position_summary_all 的 net_shares
    for s in summaries:
        net = get_position_net(s["target"])
        if abs(net.get("net_shares", 0) - s["total_shares"]) > 1e-9:
            ctx.invariant_violations.append({
                "date": today,
                "check": "net_vs_summary_consistent",
                "target": s["target"],
                "detail": f"position_net={net.get('net_shares',0)} vs summary={s['total_shares']}",
            })

    # 已清仓标的不应出现在 summary
    for s in summaries:
        if s["total_shares"] == 0:
            ctx.invariant_violations.append({
                "date": today,
                "check": "zero_shares_removed",
                "target": s["target"],
                "detail": "zero share position in summary",
            })


def _write_report(ctx: ReplayContext):
    """生成总结报告"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / "replay_report.md"

    import v0_6.core.config as cfg
    source_hash = ctx.sha256(ctx.source_db)

    # 需要获取最终持仓数量
    try:
        from v0_6.core import get_position_summary_all
        final_positions = len(get_position_summary_all())
    except Exception:
        final_positions = "—"

    lines = [
        "# 全链路历史回放报告",
        "",
        f"**代码提交**: {_get_commit_hash()}",
        f"**源数据库 SHA256**: {source_hash}",
        f"**参考本金**: ¥{ctx.reference_capital:,.0f}",
        f"**成交约定**: T 日信号 → T+1 收盘价",
        "",
        "## 回放范围",
        "",
        f"**开始日期**: {ctx.start_date}",
        f"**结束日期**: {ctx.end_date}",
        f"**预热交易日**: {ctx.warmup_days}",
        f"**正式交易日**: {len(ctx.days)}",
        "",
        "## 汇总",
        "",
        f"| 指标 | 值 |",
        f"|---|---|",
        f"| 交易总天数 | {len(ctx.days)} |",
        f"| 数据阻断日 | {len(ctx.blocked_days)} |",
        f"| 信号总数 | {len(ctx.signals_log)} |",
        f"| 订单总数 | {len(ctx.orders)} |",
        f"| 已成交 | {sum(1 for o in ctx.orders if o.get('status')=='FILLED')} |",
        f"| 未成交 | {sum(1 for o in ctx.orders if o.get('status','').startswith('UNFILLED'))} |",
        f"| BUY | {sum(1 for o in ctx.orders if o['action']=='BUY')} |",
        f"| REDUCE | {sum(1 for o in ctx.orders if o['action']=='REDUCE')} |",
        f"| SELL | {sum(1 for o in ctx.orders if o['action']=='SELL')} |",
        f"| 不变量违反 | {len(ctx.invariant_violations)} |",
        f"| 最终持仓 | {final_positions} |",
        "",
        "## 验收结论",
        "",
    ]

    if ctx.invariant_violations:
        lines.append("**❌ 不变量违反存在，验收失败**")
        for v in ctx.invariant_violations[:10]:
            lines.append(f"- {v['date']} {v['check']}: {v['detail']}")
    else:
        lines.append("**✅ 不变量违反 = 0**")

    lines.append("")
    lines.append("> 收益结果为标准化链路验收结果，")
    lines.append("> 不是包含资金竞争、手续费和滑点的正式投资回测。")
    lines.append("")
    lines.append("---")
    lines.append("*生成于 " + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "*")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n  📄 报告: {path}")


def _write_trades_csv(ctx: ReplayContext):
    path = OUTPUT_DIR / "trades.csv"
    with open(str(path), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["fill_date", "target", "target_type", "action", "shares", "price", "amount",
                     "signal_date", "status", "pnl"])
        for t in ctx.trades:
            w.writerow([
                t.get("fill_date", ""), t.get("target", ""), t.get("target_type", ""),
                t.get("action", ""), t.get("shares", 0), t.get("price", 0), t.get("amount", 0),
                t.get("signal_date", ""), t.get("status", ""), t.get("pnl", ""),
            ])


def _write_daily_account_csv(ctx: ReplayContext):
    path = OUTPUT_DIR / "daily_account.csv"
    with open(str(path), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "gross_exposure", "position_count", "realized_pnl", "unrealized_pnl",
                     "total_pnl", "blocked", "signals", "fills"])
        for d in ctx.days:
            # 粗略估算浮盈：通过 holdings 总和 — 这只是链路验收，不是精确财务
            w.writerow([
                d["replay_date"],
                d["positions_after"],
                d["positions_after"],
                "",
                "",
                "",
                1 if d["blocked"] else 0,
                len(d["signals"]),
                len(d["filled_orders"]),
            ])


def _write_signals_csv(ctx: ReplayContext):
    path = OUTPUT_DIR / "signals.csv"
    with open(str(path), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "industry", "priority", "breadth", "vol_ratio"])
        for s in ctx.signals_log:
            w.writerow([s["date"], s["industry"], s["priority"], s.get("breadth", ""), s.get("vol_ratio", "")])


def _write_orders_csv(ctx: ReplayContext):
    path = OUTPUT_DIR / "orders.csv"
    with open(str(path), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["signal_date", "target", "target_type", "action", "shares", "status"])
        for o in ctx.orders:
            w.writerow([o.get("signal_date", ""), o["target"], o.get("target_type", ""), o["action"], o.get("shares", 0), o.get("status", "")])


def _write_monitoring_actions_csv(ctx: ReplayContext):
    path = OUTPUT_DIR / "monitoring_actions.csv"
    with open(str(path), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "target", "target_type", "action", "priority"])
        for m in ctx.monitoring_log:
            w.writerow([m["date"], m["target"], m["target_type"], m["action"], m.get("priority", "")])


def _write_blocked_days_csv(ctx: ReplayContext):
    path = OUTPUT_DIR / "blocked_days.csv"
    with open(str(path), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "errors", "position_errors"])
        for b in ctx.blocked_days:
            w.writerow([b["date"], "; ".join(b.get("errors", [])), "; ".join(b.get("position_errors", []))])


def _write_invariant_violations_csv(ctx: ReplayContext):
    path = OUTPUT_DIR / "invariant_violations.csv"
    with open(str(path), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "check", "target", "detail"])
        for v in ctx.invariant_violations:
            w.writerow([v["date"], v["check"], v.get("target", ""), v.get("detail", "")])


def _get_commit_hash() -> str:
    try:
        import subprocess
        r = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                           cwd=str(V0_6_ROOT))
        return r.stdout.strip()[:12] if r.returncode == 0 else "unknown"
    except Exception:
        return "unknown"
