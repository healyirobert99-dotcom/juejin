"""
全链路历史回放 CLI 入口

用法：
    python replay_full_chain.py                          # 完整回放
    python replay_full_chain.py --smoke 20               # 前20个交易日冒烟
    python replay_full_chain.py --output ./my_replay     # 输出目录
"""
import sys
from pathlib import Path

V0_6_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(V0_6_ROOT))

from v0_6.core.replay_engine import ReplayContext, run_replay, OUTPUT_DIR


def main():
    import argparse
    parser = argparse.ArgumentParser(description="全链路历史回放")
    parser.add_argument("--smoke", type=int, default=None, help="冒烟模式：只跑 N 个交易日")
    parser.add_argument("--source", type=str, default=None, help="源行情库路径（默认 stock_data/stock_data.db）")
    parser.add_argument("--output", type=str, default=None, help="输出目录（默认 artifacts/replay）")
    args = parser.parse_args()

    from v0_6.core.config import STOCK_DATA_DIR
    source_db = Path(args.source) if args.source else (STOCK_DATA_DIR / "stock_data.db")
    if not source_db.exists():
        print(f"❌ 源行情库不存在: {source_db}")
        sys.exit(1)

    output_dir = Path(args.output) if args.output else OUTPUT_DIR

    # 真实数据库 SHA256 备份
    import hashlib
    from v0_6.core.config import DB_PATH
    real_db_hashes = {}
    for name, path in [("trade", DB_PATH), ("stock", source_db)]:
        if path.exists():
            real_db_hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest().upper()
            print(f"  {name} DB SHA256 (before): {real_db_hashes[name]}")

    ctx = ReplayContext(source_db=source_db, replay_dir=output_dir)
    ctx.setup()

    try:
        result = run_replay(ctx, max_days=args.smoke)
    finally:
        ctx.teardown()

    # 核对真实数据库未被修改
    for name, path in [("trade", DB_PATH), ("stock", source_db)]:
        if name in real_db_hashes and path.exists():
            after = hashlib.sha256(path.read_bytes()).hexdigest().upper()
            if after != real_db_hashes[name]:
                print(f"❌ 真实 {name} 数据库已被修改！")
                sys.exit(1)
            else:
                print(f"  ✓ 真实 {name} 数据库未被修改")

    print(f"\n✅ 回放完成")
    print(f"  交易日: {result['total_days']}")
    print(f"  阻断日: {result['blocked_days']}")
    print(f"  信号: {result['total_signals']}")
    print(f"  订单: {result['total_orders']}")
    print(f"  不变量违反: {result['invariant_violations']}")
    print(f"  报告: {output_dir / 'replay_report.md'}")

    if result["invariant_violations"] > 0:
        print("\n⚠️ 存在不变量违反，请检查 invariant_violations.csv")
        sys.exit(1)

    return 0


if __name__ == "__main__":
    sys.exit(main())
