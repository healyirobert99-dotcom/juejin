"""
该命令行交易入口已安全停用。

原因：旧入口使用第二套 BUY/ADD/REDUCE/SELL 逻辑（industry 字段、单笔 close_trade），
无法保证与网页端 trade_form_server.py 的净持仓聚合、REDUCE 周期标记、
SELL 周期隔离等逻辑一致。

请使用浏览器表单作为正式交易录入入口：
    http://localhost:8765/
"""
import sys


def main():
    print("=" * 60)
    print("  该命令行交易入口已停用。")
    print()
    print("  原因：旧入口无法保证与网页端的净持仓、")
    print("  REDUCE 和 SELL 周期逻辑一致。")
    print()
    print("  请使用：")
    print("    python -m v0_6.scripts.trade_form_server")
    print("    或浏览器访问：")
    print("    http://localhost:8765/")
    print("=" * 60)
    sys.exit(1)


if __name__ == "__main__":
    main()
