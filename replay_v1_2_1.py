"""三日回放脚本 — v1.2.1 验收 (§6-9)

严格顺序运行 2026-07-09 → 07-10 → 07-11，输出到 reports/replay_v1_2_1/
"""
import shutil
import sys
import importlib
from pathlib import Path

# 切换输出目录
import v0_6.core.config as cfg
import v0_6.scripts.run_daily_v1_html as runner

ORIG_DAILY_DIR = cfg.DAILY_DIR
REPLAY_DIR = Path(__file__).parent / "reports" / "replay_v1_2_1"
REPLAY_DIR.mkdir(parents=True, exist_ok=True)

# 重定向 DAILY_DIR
cfg.DAILY_DIR = REPLAY_DIR
runner.DAILY_DIR = REPLAY_DIR

# 先清除 ledger，从空白开始
ledger_path = cfg.DATA_DIR.parent / "reports" / "observation" / "signal_ledger.csv"
if ledger_path.exists():
    ledger_path.unlink()
    print(f"🧹 清除旧账本: {ledger_path}")

dates = ["2026-07-09", "2026-07-10", "2026-07-11"]
all_success = True

for d in dates:
    print(f"\n{'='*60}")
    print(f"▶ 回放日期: {d}")
    print(f"{'='*60}")

    try:
        # 重导入 main 以获取最新代码（避免 importlib.reload 问题）
        run_main = runner.main
        rc = 0
        old_argv = sys.argv[:]
        sys.argv = ["run_daily_v1_html.py", d]
        try:
            # 直接调用内部函数而非 main()，避免 sys.argv 解析
            # 沿袭 main() 逻辑
            from v0_6.core.config import DAILY_DIR as current_daily_dir
            import pandas as pd
            from v0_6.core import get_position_summary_all
            from v0_6.core.market_data import validate_market_data, get_expected_trade_date

            # 获取行业数据
            signal_data_date = runner.get_signal_data_date(d)
            if signal_data_date is None:
                print(f"  ❌ {d} 无 signal_data_date，跳过")
                all_success = False
                continue

            print(f"  signal_data_date: {signal_data_date}")

            # 获取信号
            today_signals, all_signals, industry_daily = runner.get_today_signals(signal_data_date)

            # 获取雷达
            radar_candidates = None
            try:
                radar_candidates = runner.build_signal_radar(
                    industry_daily, all_signals, signal_data_date, max_items=5,
                )
                if radar_candidates:
                    from v0_6.core.observation_tracker import compute_radar_streak, reset_radar_for_triggered
                    radar_candidates = compute_radar_streak(radar_candidates, signal_data_date, industry_daily)
                    if not today_signals.empty:
                        for _, sig in today_signals.iterrows():
                            reset_radar_for_triggered(sig.get("industry", ""))
            except Exception as e:
                print(f"  ⚠ 雷达生成失败: {e}")

            # 获取持仓
            open_positions = get_position_summary_all()

            # 更新信号账本
            from v0_6.core.signal_ledger import update_signal_ledger
            ledger_df = update_signal_ledger(
                radar_candidates or [], today_signals, industry_daily, signal_data_date,
                open_positions=open_positions,
            )

            # 获取 monitoring
            monitoring = runner.monitor_all_positions_v6(
                today=d, signal_data_date=signal_data_date, industry_daily=industry_daily,
            )

            # 主线结构
            try:
                from v0_6.core.mainline_monitor import (
                    classify_mainline_structure, compute_relative_strength, compute_trend_continuation,
                )
                for m in monitoring:
                    si = m.get("source_industry")
                    if si and not industry_daily.empty:
                        m["mainline_structure"] = classify_mainline_structure(industry_daily, si, signal_data_date)
                        m["relative_strength"] = compute_relative_strength(industry_daily, si, signal_data_date)
                        m["trend_continuation"] = compute_trend_continuation(
                            industry_daily, si, signal_data_date,
                            signal_date=m.get("source_signal_date"),
                        )
            except Exception as e:
                print(f"  ⚠ 主线结构分析失败: {e}")

            # 渲染
            recent_cases = None
            try:
                from v0_6.core.observation_tracker import update_signal_cases, get_recent_cases
                recent_cases = get_recent_cases(3)
            except:
                pass

            html = runner.render_html(d, signal_data_date, today_signals, monitoring,
                                      radar_candidates, recent_cases=recent_cases,
                                      ledger_df=ledger_df)
            html_path = REPLAY_DIR / f"{d}_daily.html"
            html_path.write_text(html, encoding="utf-8")

            from v0_6.scripts.render_markdown_report import render_markdown_report as rmd
            md = rmd(
                requested_date=d, signal_data_date=signal_data_date,
                today_signals=today_signals, monitoring=monitoring,
                radar_candidates=radar_candidates, recent_cases=recent_cases,
                ledger_df=ledger_df,
            )
            md_path = REPLAY_DIR / f"{d}_daily.md"
            md_path.write_text(md, encoding="utf-8")

            # 保存账本快照
            csv_path = REPLAY_DIR / f"{d}_signal_ledger.csv"
            ledger_df.to_csv(str(csv_path), index=False, encoding="utf-8")

            print(f"  ✅ HTML: {html_path} ({html_path.stat().st_size} bytes)")
            print(f"  ✅ MD:   {md_path} ({md_path.stat().st_size} bytes)")
            print(f"  ✅ CSV:  {csv_path} ({csv_path.stat().st_size} bytes)")

            # 打印关键信息
            if not ledger_df.empty:
                for _, row in ledger_df.iterrows():
                    ind = row.get("industry","?")
                    status = row.get("current_status","?")
                    phase = row.get("current_phase","?")
                    sid = row.get("signal_id","?")
                    sd = row.get("signal_date","")
                    d1 = row.get("d1_ret","")
                    print(f"  📋 [{status}] {ind} | {phase} | {sid} | sig:{sd} | D+1:{d1}")

        finally:
            sys.argv = old_argv

    except Exception as e:
        print(f"  ❌ {d} 回放失败: {e}")
        import traceback; traceback.print_exc()
        all_success = False

print(f"\n{'='*60}")
print(f"🏁 回放{'完成' if all_success else '部分失败'}")
print(f"   输出目录: {REPLAY_DIR}")
print(f"{'='*60}")
