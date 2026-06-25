"""
该入口已安全停用。

请使用统一行情刷新入口：
    python -m v0_6.scripts.refresh_market_data
"""
import sys


def main():
    print("=" * 60)
    print("  该入口已停用，请使用：")
    print("    python -m v0_6.scripts.refresh_market_data")
    print("=" * 60)
    sys.exit(1)


if __name__ == "__main__":
    main()
