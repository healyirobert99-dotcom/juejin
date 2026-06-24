"""
v0.6 交易录入 · 立即启动 + 开机自启（无需管理员）

策略：直接把"服务"挂到 Windows 启动文件夹
- 启动文件夹：%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
- 放一个 .vbs（不弹窗）+ .bat（实际跑）
- 用户登录时自动起

文件结构：
  Startup/
    ZhuxianTradeForm.vbs   → 静默调起 bat
  D:/zhuxian-catch-v0_6/
    start_trade_form_silent.bat  → 实际跑（不弹浏览器）
    start_trade_form.bat          → 手动启动用
    stop_trade_form.bat           → 停止

录交易入口：浏览器 http://localhost:8765/  （服务在 8765 一直跑）
档案页入口：file:///D:/zhuxian-catch-v0_6/reports/daily_v1/archive.html
            → 顶部"录入交易"按钮（服务在线时直接进，不在线时弹提示）
"""
import os
import sys
import subprocess
from pathlib import Path

V0_6_ROOT = Path(__file__).resolve().parents[2]


def get_startup_dir() -> Path:
    """Windows 启动文件夹"""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA 未设置")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def write_silent_bat():
    """无窗口启动（开机自启用）"""
    p = V0_6_ROOT / "start_trade_form_silent.bat"
    p.write_text(
        f"""@echo off
chcp 65001 >nul
cd /d {V0_6_ROOT}
:loop
    netstat -ano | findstr ":8765.*LISTENING" >nul
    if errorlevel 1 (
        python -m v0_6.scripts.trade_form_server >> "{V0_6_ROOT}\\trade_form.log" 2>&1
    )
    timeout /t 30 >nul
    goto loop
""",
        encoding="utf-8",
    )
    print(f"[写入] {p}")
    return p


def write_vbs_startup():
    """VBS 启动器（无黑窗）"""
    bat = V0_6_ROOT / "start_trade_form_silent.bat"
    vbs = get_startup_dir() / "ZhuxianTradeForm.vbs"
    vbs.write_text(
        f"""Set WshShell = CreateObject("WScript.Shell")
WshShell.Run chr(34) & "{bat}" & chr(34), 0, False
Set WshShell = Nothing
""",
        encoding="utf-8",
    )
    print(f"[写入] {vbs}")
    return vbs


def install():
    print("\n=== 安装掘金交易录入后台服务 ===\n")
    write_silent_bat()
    vbs = write_vbs_startup()
    print(f"\n[完成] 已设置开机自启：")
    print(f"       {vbs}")
    print()
    print(f"  现在浏览器打开：  http://localhost:8765/")
    print(f"  下次开机后自动起，无需任何操作")
    print(f"  停止服务：双击 stop_trade_form.bat")
    print(f"  卸载自启：python -m v0_6.scripts.install_service uninstall")
    return 0


def uninstall():
    print("\n=== 卸载掘金交易录入自启 ===\n")
    vbs = get_startup_dir() / "ZhuxianTradeForm.vbs"
    if vbs.exists():
        vbs.unlink()
        print(f"  [删] {vbs}")
    silent = V0_6_ROOT / "start_trade_form_silent.bat"
    if silent.exists():
        # bat 保留，手动可复用
        print(f"  [保留] {silent}")
    print(f"\n  停服务：双击 stop_trade_form.bat")
    return 0


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "uninstall":
        return uninstall()
    return install()


if __name__ == "__main__":
    sys.exit(main())
