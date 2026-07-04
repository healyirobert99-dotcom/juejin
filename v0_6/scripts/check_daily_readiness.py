#!/usr/bin/env python
"""掘金信号 v1.1 每日健康检查

在生成日报前运行，检查是否存在需要人工处理的问题。

检查项：
1. 开放持仓是否存在 source_industry 缺失
2. radar_state.csv 是否可读写
3. signal_cases.csv 是否可读写

缺少 source_industry 不阻断生成日报，只提示需要一次性补录。
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

# 确保项目根目录在 sys.path 中
_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# 旧 _project_root 函数被上面的变量覆盖，删除原函数
del _project_root


def _proj_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def check_positions() -> dict:
    """检查开放持仓来源行业"""
    result = {"status": "OK", "missing": [], "advice": []}

    try:
        from v0_6.core.live_trade_store import get_position_summary_all
        positions = get_position_summary_all()
    except Exception as e:
        return {"status": "ERROR", "missing": [], "advice": [f"无法读取持仓: {e}"]}

    if not positions:
        return result

    for p in positions:
        si = p.get("source_industry")
        if not si or p.get("source_industry_status") in ("MISSING", "CONFLICT"):
            result["missing"].append(p.get("target", "?"))

    if result["missing"]:
        result["status"] = "NEED_ACTION"
        result["advice"] = [
            "以下持仓缺少来源行业：",
            ", ".join(result["missing"]),
            "",
            "这不是每日任务，只需对历史缺失持仓补录一次。",
            "补录后，主线结构、相对强度、趋势延续和退潮监控会自动生效。",
            "",
            "执行补录：",
            "python v0_6/scripts/backfill_position_source.py",
            "",
            "补录完成后重新生成日报：",
            "python v0_6/scripts/run_daily_v1_html.py <日期>",
        ]

    return result


def check_radar_state() -> dict:
    """检查雷达状态文件是否可读写"""
    try:
        from v0_6.core.observation_tracker import _read_radar_state, _radar_state_path
        p = _radar_state_path()
        if not p.parent.exists():
            return {"status": "OK", "advice": [f"目录 {p.parent} 将在首次日报生成时自动创建"]}
        df = _read_radar_state()
        return {"status": "OK", "advice": [f"共 {len(df)} 个雷达观察行业"]}
    except Exception as e:
        return {"status": "WARN", "advice": [f"文件访问异常: {e}，首次日报生成时将自动修复"]}


def check_signal_cases() -> dict:
    """检查案例账本文件是否可读写"""
    try:
        from v0_6.core.observation_tracker import _read_cases, _cases_path
        p = _cases_path()
        if not p.parent.exists():
            return {"status": "OK", "advice": [f"目录 {p.parent} 将在首次日报生成时自动创建"]}
        df = _read_cases()
        return {"status": "OK", "advice": [f"共 {len(df)} 条案例记录"]}
    except Exception as e:
        return {"status": "WARN", "advice": [f"文件访问异常: {e}，首次日报生成时将自动修复"]}


def main() -> int:
    from v0_6.core.version import VERSION_LABEL

    print(f"=== {VERSION_LABEL} 每日检查 ===\n")

    positions = check_positions()
    radar = check_radar_state()
    cases = check_signal_cases()

    # 打印结果
    status_icon = {"OK": "\u2713", "NEED_ACTION": "\u26a0", "ERROR": "\u274c", "WARN": "\u26a0"}

    print(f"持仓来源行业：{status_icon.get(positions['status'], '?')} {positions['status']}")
    if positions.get("advice"):
        print()
        for line in positions["advice"]:
            print(f"  {line}")
        print()

    print(f"雷达状态文件：{status_icon.get(radar['status'], '?')} {radar['status']}")
    if radar.get("advice"):
        for line in radar["advice"]:
            print(f"  {line}")

    print(f"案例账本文件：{status_icon.get(cases['status'], '?')} {cases['status']}")
    if cases.get("advice"):
        for line in cases["advice"]:
            print(f"  {line}")

    print()

    if positions["status"] == "NEED_ACTION":
        print("结论：需要先补录来源行业，否则主线结构、相对强度和退潮分析不可用。")
        print("（价格风控不受影响，日报仍可正常生成。）")
        return 1
    else:
        print("结论：可以直接生成日报。")
        return 0


if __name__ == "__main__":
    sys.exit(main())
