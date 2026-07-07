"""
持仓来源行业人工补录工具

用法：
    python v0_6/scripts/backfill_position_source.py

功能：
1. 显示当前所有开放持仓
2. 显示已有来源行业字段
3. 允许人工输入 source_industry / source_signal_date 等
4. 补录前自动备份数据库到 artifacts/position_backfill/
5. 预览修改后二次确认
6. 单事务写入当前未平仓周期全部 BUY/ADD 记录
7. 输出审计文件
"""
import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

V0_6_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(V0_6_ROOT))

import v0_6.core.config as cfg
from v0_6.core.live_trade_store import init_schema

BACKUP_DIR = V0_6_ROOT / "artifacts" / "position_backfill"


def show_open_positions():
    """列出所有开放持仓及其来源行业"""
    init_schema()
    con = sqlite3.connect(str(cfg.DB_PATH))

    rows = con.execute("""
        SELECT target, target_type, MIN(target_name) as target_name,
               MIN(trade_date) as first_buy,
               SUM(CASE WHEN action IN ('BUY','ADD') AND closed=0 THEN shares ELSE 0 END) as net_shares,
               source_industry, source_signal_date, source_signal_priority,
               source_signal_type, source_signal_ret20
        FROM live_trades
        WHERE action IN ('BUY','ADD') AND closed=0
        GROUP BY target
        HAVING net_shares > 0
        ORDER BY first_buy
    """).fetchall()
    con.close()

    if not rows:
        print("没有开放持仓。")
        return None

    print(f"\n当前开放持仓 ({len(rows)} 笔):\n")
    for i, r in enumerate(rows, 1):
        print(f"  [{i}] {r[0]} ({r[2] or '?'})")
        print(f"      类型: {r[1]}, 首次买入: {r[3]}, 净股数: {r[4]}")
        print(f"      来源行业: {r[5] or '(空)'}")
        print(f"      信号日期: {r[6] or '(空)'}")
        print(f"      信号优先级: {r[7] or '(空)'}")
        print(f"      信号类型: {r[8] or '(空)'}")
        print(f"      信号20日收益: {r[9] or '(空)'}")
        print()

    return rows


def backup_database():
    """备份项目数据库"""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"backup_{ts}.sqlite3"
    shutil.copy2(str(cfg.DB_PATH), str(backup_path))
    print(f"  ✓ 数据库已备份到: {backup_path}")
    return backup_path


def backfill_position(target: str, source_industry: str,
                      signal_date: str = None, signal_priority: str = None,
                      signal_type: str = None, signal_ret20: float = None):
    """为指定 target 的全部未平仓 BUY/ADD 写入来源行业"""
    init_schema()
    con = sqlite3.connect(str(cfg.DB_PATH))

    # 检查现有数据
    existing = con.execute(
        "SELECT source_industry FROM live_trades "
        "WHERE target=? AND action IN ('BUY','ADD') AND closed=0 "
        "AND source_industry IS NOT NULL LIMIT 1",
        (target,),
    ).fetchone()

    if existing:
        print(f"  ⚠ {target} 已有来源行业: {existing[0]}")
        resp = input("  是否覆盖？[y/N]: ").strip().lower()
        if resp != "y":
            print("  已取消。")
            con.close()
            return False

    rows = con.execute(
        "SELECT trade_id, trade_date, action, amount FROM live_trades "
        "WHERE target=? AND action IN ('BUY','ADD') AND closed=0 "
        "ORDER BY trade_id",
        (target,),
    ).fetchall()

    if not rows:
        print(f"  ❌ {target} 没有未平仓的 BUY/ADD 记录。")
        con.close()
        return False

    # 预览
    print(f"\n  拟修改 {len(rows)} 行:\n")
    for r in rows:
        print(f"    trade_id={r[0]}, {r[3]}: BUY/ADD, date={r[2]}")
    print(f"\n    source_industry = '{source_industry}'")
    print(f"    source_signal_date = '{signal_date or '(空)'}'")
    print(f"    source_signal_priority = '{signal_priority or '(空)'}'")
    print(f"    source_signal_type = '{signal_type or '(空)'}'")
    print(f"    source_signal_ret20 = {signal_ret20}")

    resp = input("\n  确认写入？[y/N]: ").strip().lower()
    if resp != "y":
        print("  已取消。")
        con.close()
        return False

    # 事务写入
    try:
        con.execute("BEGIN")
        con.executemany(
            """UPDATE live_trades SET
               source_industry=?, source_signal_date=?, source_signal_priority=?,
               source_signal_type=?, source_signal_ret20=?
               WHERE trade_id=?""",
            [
                (source_industry, signal_date, signal_priority,
                 signal_type, signal_ret20, r[0])
                for r in rows
            ],
        )
        con.execute("COMMIT")
        print(f"  ✓ 已写入 {len(rows)} 行。")
    except Exception as e:
        con.execute("ROLLBACK")
        print(f"  ❌ 写入失败: {e}")
        con.close()
        return False

    con.close()
    return True


def write_audit(target: str, source_industry: str, backup_path: Path, **kwargs):
    """写审计文件"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    audit = {
        "timestamp": datetime.now().isoformat(),
        "target": target,
        "source_industry": source_industry,
        "source_signal_date": kwargs.get("signal_date"),
        "source_signal_priority": kwargs.get("signal_priority"),
        "source_signal_type": kwargs.get("signal_type"),
        "source_signal_ret20": kwargs.get("signal_ret20"),
        "backup_path": str(backup_path),
    }

    json_path = BACKUP_DIR / f"backfill_{ts}.json"
    md_path = BACKUP_DIR / f"backfill_{ts}.md"

    json_path.write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(
        f"# 持仓来源行业补录审计\n\n"
        f"| 字段 | 值 |\n"
        f"|---|---|\n"
        f"| 时间 | {audit['timestamp']} |\n"
        f"| 标的 | {audit['target']} |\n"
        f"| 来源行业 | {audit['source_industry']} |\n"
        f"| 信号日期 | {audit['source_signal_date'] or '(空)'} |\n"
        f"| 信号优先级 | {audit['source_signal_priority'] or '(空)'} |\n"
        f"| 信号类型 | {audit['source_signal_type'] or '(空)'} |\n"
        f"| 信号20日收益 | {audit['source_signal_ret20']} |\n"
        f"| 数据库备份 | {audit['backup_path']} |\n",
        encoding="utf-8",
    )

    print(f"\n  📄 审计文件: {json_path}")
    print(f"  📄 审计文件: {md_path}")


def main():
    rows = show_open_positions()
    if rows is None:
        return

    print("请输入要补录的 target 代码:")
    print("  示例: 513780, 512800, 159995")
    target = input("  target: ").strip()
    if not target:
        print("未输入 target，退出。")
        return

    print("\n请输入来源行业名称:")
    print("  示例: 证券, 银行, 半导体")
    source_industry = input("  source_industry: ").strip()
    if not source_industry:
        print("来源行业为必填项。退出。")
        return

    print("\n请输入来源信号日期 (YYYYMMDD 格式):")
    print("  留空则跳过")
    signal_date = input("  source_signal_date: ").strip() or None

    print("\n请输入来源信号优先级:")
    print("  示例: FIRST, REPEAT")
    signal_priority = input("  source_signal_priority: ").strip() or None

    print("\n请输入来源信号类型:")
    signal_type = input("  source_signal_type: ").strip() or None

    print("\n请输入信号日行业20日收益 (如 0.05 表示 5%):")
    ret20_str = input("  source_signal_ret20: ").strip()
    try:
        signal_ret20 = float(ret20_str) if ret20_str else None
    except ValueError:
        print("无效数字，退出。")
        return

    # 备份
    backup_path = backup_database()

    # 写入
    success = backfill_position(
        target, source_industry,
        signal_date=signal_date,
        signal_priority=signal_priority,
        signal_type=signal_type,
        signal_ret20=signal_ret20,
    )

    if success:
        write_audit(
            target, source_industry,
            backup_path,
            signal_date=signal_date,
            signal_priority=signal_priority,
            signal_type=signal_type,
            signal_ret20=signal_ret20,
        )


if __name__ == "__main__":
    main()
