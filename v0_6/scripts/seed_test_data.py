"""
测试数据填充脚本

为 v6 实盘日报演示填入测试数据：
- 3-4 笔今日触发的掘金信号（不同行业）
- 5-6 笔持仓（覆盖不同进度桶、不同盈亏状态）

用法：
    python v0_6/scripts/seed_test_data.py
"""
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

V0_6_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(V0_6_ROOT))

from v0_6.core import (
    init_schema,
    add_trade,
    get_bucket,
    calc_stop_price,
    calc_take_profit_price,
)
from v0_6.core.config import DB_PATH


# 测试数据：模拟 2026-06-24 的实盘场景
TEST_POSITIONS = [
    {
        # 持仓 1：触发红色止损 - 半导体
        "industry": "半导体",
        "entry_date": "2026-04-15",
        "entry_price": 100.0,
        "shares": 1000,
        "progress": 0.05,
        "current_price_change": -0.20,  # 当前 -20%，已突破 -18% 止损
        "notes": "半导体极早期入场，已深度浮亏",
    },
    {
        # 持仓 2：触发止盈 - 银行
        "industry": "银行",
        "entry_date": "2026-05-20",
        "entry_price": 10.0,
        "shares": 5000,
        "progress": 0.18,
        "current_price_change": 0.32,  # +32%，已突破 +30% 止盈
        "notes": "银行早期入场，已达 +30% 止盈",
    },
    {
        # 持仓 3：接近止损 - 有色金属
        "industry": "有色金属",
        "entry_date": "2026-05-28",
        "entry_price": 50.0,
        "shares": 2000,
        "progress": 0.12,
        "current_price_change": -0.12,  # -12%，距 -15% 止损 3pp
        "notes": "有色金属早期入场，接近止损",
    },
    {
        # 持仓 4：接近止盈 - 医药
        "industry": "医药",
        "entry_date": "2026-05-10",
        "entry_price": 25.0,
        "shares": 4000,
        "progress": 0.08,
        "current_price_change": 0.25,  # +25%，距 +30% 止盈 5pp
        "notes": "医药极早期入场，接近止盈",
    },
    {
        # 持仓 5：正常持有 - 房地产
        "industry": "房地产",
        "entry_date": "2026-06-05",
        "entry_price": 8.0,
        "shares": 6000,
        "progress": 0.22,
        "current_price_change": 0.05,  # +5%
        "notes": "房地产早期入场，浮盈",
    },
    {
        # 持仓 6：桶切换告警 - 煤炭
        "industry": "煤炭",
        "entry_date": "2026-03-20",
        "entry_price": 15.0,
        "shares": 3000,
        "progress": 0.05,
        "current_price_change": 0.55,  # +55%，已从极早期切到晚期
        "notes": "煤炭极早期入场，价格大涨桶切换",
    },
]


# 今日触发的掘金信号（不入库到 live_trades，模拟信号列表）
TODAY_SIGNALS = [
    {"industry": "白酒", "priority": "FIRST", "breadth": 0.42, "vol_ratio": 1.25, "avg_ret20": 0.018},
    {"industry": "汽车", "priority": "REPEAT", "breadth": 0.51, "vol_ratio": 1.45, "avg_ret20": 0.032},
    {"industry": "电力", "priority": "FIRST", "breadth": 0.38, "vol_ratio": 1.08, "avg_ret20": 0.012},
]


def seed_positions():
    """填充测试持仓"""
    init_schema()
    con = sqlite3.connect(str(DB_PATH))
    con.execute("DELETE FROM live_trades")
    con.commit()
    con.close()

    print("[1/2] 填充测试持仓...")
    for p in TEST_POSITIONS:
        bucket = get_bucket(p["progress"])
        current_price = p["entry_price"] * (1 + p["current_price_change"])
        trade_id = add_trade(
            industry=p["industry"],
            action="BUY",
            price=p["entry_price"],
            shares=p["shares"],
            trade_date=p["entry_date"],
            signal_type="GOLD",
            position_pct=0.08,
            progress=p["progress"],
            notes=p["notes"],
        )
        print(
            f"  + {p['industry']:<8} "
            f"入场 ¥{p['entry_price']:<7.2f}  "
            f"现价 ¥{current_price:<7.2f}  "
            f"{p['current_price_change']*100:+.1f}%  "
            f"({bucket['name']}, 止损 {bucket['stop']*100:.0f}%)"
        )

    print(f"\n[2/2] 填充 {len(TODAY_SIGNALS)} 个今日信号（不写入数据库）:")
    for s in TODAY_SIGNALS:
        tag = "🔁 重复" if s["priority"] == "REPEAT" else "🆕 首次"
        print(
            f"  + {s['industry']:<6} {tag}  宽度 {s['breadth']*100:.0f}%  "
            f"量比 {s['vol_ratio']:.2f}  20日均涨幅 {s['avg_ret20']*100:+.1f}%"
        )

    print(f"\n✅ 测试数据填充完成（持仓 {len(TEST_POSITIONS)} 笔 + 信号 {len(TODAY_SIGNALS)} 个）")


if __name__ == "__main__":
    seed_positions()
