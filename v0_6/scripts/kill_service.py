"""
杀掉后台的 8765 端口服务
"""
import os
import subprocess
import sys
import time


def kill_listeners(port: int = 8765):
    r = subprocess.run(f"netstat -ano | findstr :{port}", shell=True, capture_output=True, text=True)
    pids = set()
    for line in r.stdout.splitlines():
        if "LISTENING" in line:
            parts = line.split()
            if parts:
                try:
                    pids.add(int(parts[-1]))
                except ValueError:
                    pass
    if not pids:
        print(f"端口 {port} 当前无服务")
        return
    for pid in pids:
        print(f"杀掉 PID={pid}")
        subprocess.run(f"taskkill /F /PID {pid}", shell=True, capture_output=True)


def main():
    print("\n=== 停止掘金交易录入服务 ===\n")
    kill_listeners()
    print("\n  启动：直接双击 archive.html → 顶部按钮，或打开 http://localhost:8765/")
    print("  启动后端：python -c \"import subprocess; subprocess.Popen(['python','-m','v0_6.scripts.trade_form_server'])\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
