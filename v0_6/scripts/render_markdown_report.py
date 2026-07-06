"""
掘金日报 Markdown 研究版渲染器

输入与 HTML 版完全相同的 report_data，输出独立完整的 Markdown 研究报告。
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd


def render_markdown_report(
    requested_date: str = None,
    signal_data_date: str = None,
    today_signals: pd.DataFrame = None,
    monitoring: list = None,
    radar_candidates: list = None,
    validation: dict = None,
    open_positions: list = None,
    group_linkage: list = None,
    recent_cases = None,
) -> str:
    """生成 Markdown 研究版日报"""

    if requested_date is None:
        requested_date = datetime.now().strftime("%Y-%m-%d")
    if signal_data_date is None:
        signal_data_date = requested_date

    lines = []
    sd_dt = pd.to_datetime(signal_data_date)
    today_only = today_signals[today_signals["signal_date"] == sd_dt] if today_signals is not None else pd.DataFrame()
    n_today = len(today_only)
    n_positions = len(monitoring) if monitoring else 0
    n_radar = len(radar_candidates) if radar_candidates else 0

    # ── 页头（始终显示行情日期） ──
    lines.append("# 掘金信号日报")
    lines.append("")
    lines.append(f"- 报告日期：{requested_date}")
    lines.append(f"- 行情数据截至：{signal_data_date}")
    lines.append(f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- 系统版本：掘金信号 v1.1")
    if validation:
        lines.append(f"- 数据状态：{'正常' if validation.get('ok') else '异常'}")
    lines.append("")

    # ═══════ 一、摘要 ═══════
    lines.append("## 一、当日结论摘要")
    lines.append("")
    lines.append(f"- 正式信号：{n_today} 个")
    lines.append(f"- 信号雷达：{n_radar} 个行业进入 3/4 观察")
    lines.append(f"- 当前持仓：{n_positions} 笔")

    urgent = sum(1 for r in (monitoring or [])
                 if r.get("action") in ("CLEAR", "RETREAT_REDUCE_1_3", "REDUCE_1_3"))
    lines.append(f"- 立即操作：{urgent} 项" if urgent > 0 else "- 立即操作：无")
    missing_si = sum(1 for m in (monitoring or []) if not m.get("source_industry"))
    if missing_si > 0:
        lines.append(f"- 持仓结论：{missing_si} 笔来源行业尚未补录，暂不能进行行业退潮和主线结构分析")
    else:
        lines.append("- 持仓结论：所有持仓来源行业正常")
    lines.append("")

    # ═══════ 二、正式信号 ═══════
    lines.append("## 二、正式主线信号")
    lines.append("")
    if today_only is not None and not today_only.empty:
        for _, sig in today_only.iterrows():
            priority = sig.get("priority", "FIRST")
            pct_str = "试仓 8%" if priority == "FIRST" else "建仓 12%"
            lines.append(f"### {sig['industry']} | STABILIZING_B | {priority}")
            lines.append("")
            lines.append(f"**操作建议：{pct_str}**")
            lines.append("")
            lines.append("#### 四因子结果")
            lines.append("")
            lines.append("| 因子 | 当前值 | 阈值 | 结果 |")
            lines.append("|---|---:|---:|---|")
            breadth = float(sig.get("breadth_at_signal", 0)) * 100
            lines.append(f"| 行业宽度 | {breadth:.1f}% | ≥35% | 通过 |")
            vol = float(sig.get("vol_ratio_at_signal", 0))
            lines.append(f"| 量比 | {vol:.2f} | ≥1.00 | 通过 |")
            ret20 = float(sig.get("avg_ret20_at_signal", 0)) * 100
            lines.append(f"| 20日收益 | {ret20:+.1f}% | >0 | 通过 |")
            low_days = int(sig.get("n_low_in_past_60d", 0))
            lines.append(f"| 弱势背景 | {low_days}/60日 | ≥30日 | 通过 |")
            lines.append("")
            lines.append("#### 信息概要")
            lines.append("")
            lines.append(f"- 信号日期：{sig['signal_date']}")
            n_stocks = int(sig.get("n_stocks", 0))
            lines.append(f"- 行业包含 {n_stocks} 只成分股")
            lines.append(f"- 系统匹配 ETF 参考见下游标的匹配表")
            lines.append("")
            lines.append("该行业此前长期处于低宽度状态，当前行业参与度重新突破 35% 阈值，成交量恢复，同时 20 日收益转正，形成正式企稳重估信号。")
            lines.append("")
    else:
        lines.append("今日没有行业同时满足四项正式触发条件。")
        lines.append("")

    # ═══════ 三、信号雷达 ═══════
    lines.append("## 三、信号雷达")
    lines.append("")
    if radar_candidates:
        for i, cand in enumerate(radar_candidates, 1):
            _append_radar_detail(lines, cand, i)
    else:
        lines.append("今日无新进入 3/4 观察的行业。")
        # ── v1.1: 显示此前仍在观察期的行业 ──
        try:
            from v0_6.core.observation_tracker import _read_radar_state
            prev_state = _read_radar_state()
            if not prev_state.empty:
                lines.append("")
                lines.append("**此前观察中的行业：**")
                lines.append("")
                lines.append("| 行业 | 连续天数 | 缺失条件 | 最近宽度 | 状态 |")
                lines.append("|---|---:|---:|---|")
                for _, row in prev_state.iterrows():
                    ind = row.get("industry", "?")
                    streak = row.get("consecutive_radar_days", "1")
                    missing = row.get("last_missing_text", "")
                    breadth = row.get("last_breadth", "")
                    b_str = f"{float(breadth)*100:.1f}%" if breadth and breadth not in ("", "nan") else "—"
                    wl = row.get("last_watch_level", "WATCH")
                    wl_text = "强观察" if wl == "STRONG_WATCH" else "观察中"
                    lines.append(f"| {ind} | {streak} | {missing} | {b_str} | {wl_text} |")
                lines.append("")
                lines.append("以上行业今日未满足 3/4 条件，已退出雷达观察。若条件恢复可重新进入。")
        except Exception:
            pass
        lines.append("")

    # ── v1.1: 行业大类联动 ──
    if group_linkage:
        lines.append("## 三½、行业大类联动")
        lines.append("")
        for gl in group_linkage:
            lines.append(f"### {gl['group']}｜{gl['count']} 个细分行业同步进入雷达")
            lines.append("")
            lines.append("| 细分行业 | 状态 | 连续天数 | 缺失条件 |")
            lines.append("|---|---:|---|")
            for cand in (radar_candidates or []):
                if cand["industry"] in gl["members"]:
                    wl = cand.get("watch_level", "WATCH")
                    wl_label = "强观察" if wl == "STRONG_WATCH" else "普通观察"
                    streak = cand.get("consecutive_radar_days", 1)
                    missing = cand.get("missing_text", "")
                    lines.append(f"| {cand['industry']} | {wl_label} | {streak} | {missing} |")
            lines.append("")
            lines.append("当前属于同一大类多个细分行业同步改善，但正式信号仍按各细分行业独立判断。")
            lines.append("")

    # ── v1.1: 近期案例跟踪 ──
    if recent_cases is not None and not recent_cases.empty:
        lines.append("## 三½、近期信号案例跟踪")
        lines.append("")
        lines.append("| 行业 | 首次雷达 | 正式触发 | 5日 | 10日 | 20日 | 最大涨幅 | 最大回撤 |")
        lines.append("|---|---:|---:|---:|---:|---:|")
        for _, case in recent_cases.iterrows():
            ind = case.get("industry", "?")
            frd = case.get("first_radar_date", "")
            fts = "是" if case.get("formal_triggered") == "1" else "—"

            def _safe_pct(val, suffix=""):
                """安全格式化百分比，处理 nan/空/None"""
                if val is None or val == "" or val == "nan" or val == "None":
                    return "待更新"
                try:
                    v = float(val)
                    if np.isnan(v):
                        return "待更新"
                    return f"{v*100:+.1f}%"
                except (ValueError, TypeError):
                    return "待更新"

            f5 = _safe_pct(case.get("forward_ret_5d"))
            f10 = _safe_pct(case.get("forward_ret_10d"))
            f20 = _safe_pct(case.get("forward_ret_20d"))
            mr = _safe_pct(case.get("max_return_20d"))
            dd = _safe_pct(case.get("max_drawdown_20d"))
            lines.append(f"| {ind} | {frd} | {fts} | {f5} | {f10} | {f20} | {mr} | {dd} |")
        lines.append("")

    # ═══════ 四、持仓总览 ═══════
    lines.append("## 四、持仓总览")
    lines.append("")
    if monitoring:
        lines.append("| 来源行业 | 标的 | 当前收益 | 主线结构 | 相对强度 | 退潮状态 | 最终操作 |")
        lines.append("|---|---:|---|---|---|---|")
        for r in monitoring:
            si = r.get("source_industry")
            si_display = si if si else "待补录"
            if si:
                ms = (r.get("mainline_structure") or {}).get("structure_label", "暂不可判断")
                rs = (r.get("relative_strength") or {}).get("strength_label", "暂不可判断")
                ret_stage = r.get("retreat_stage") or "—"
            else:
                ms = "暂不可判断"
                rs = "暂不可判断"
                ret_stage = "暂不可判断"
            ret_pct = r.get("return_pct", 0) * 100
            action = r.get("action", "HOLD")
            lines.append(f"| {si_display} | {r.get('target', '?')} | {ret_pct:+.1f}% | {ms} | {rs} | {ret_stage} | {action} |")
        lines.append("")
    else:
        lines.append("当前无开放持仓。")
        lines.append("")

    # ═══════ 五、逐笔持仓分析 ═══════
    lines.append("## 五、持仓逐项详细分析")
    lines.append("")
    if monitoring:
        for i, r in enumerate(monitoring, 1):
            _append_position_detail(lines, r, i)
    else:
        lines.append("当前无持仓。")
        lines.append("")

    # ═══════ 六、风险与待办 ═══════
    lines.append("## 六、当日风险与待办")
    lines.append("")
    has_items = False
    for r in (monitoring or []):
        if not r.get("source_industry"):
            lines.append(f"1. **{r.get('target', '?')}** 来源行业尚未补录——这是一次性修复任务，不是每日任务。补录后后续日报会自动使用该来源行业，主线、强度和退潮分析自动生效。")
            has_items = True
        action = r.get("action", "")
        if action == "CLEAR":
            lines.append(f"1. **{r.get('target', '?')}** 已触发止损 — 需要人工确认清仓。")
            has_items = True
        elif action in ("RETREAT_REDUCE_1_3", "REDUCE_1_3"):
            lines.append(f"1. **{r.get('target', '?')}** 建议下一交易日减仓 1/3。")
            has_items = True
    if not has_items:
        lines.append("当前无需要人工处理的紧急事项。")
    lines.append("")

    # ═══════ 七、方法说明 ═══════
    lines.append("## 七、数据与方法说明")
    lines.append("")
    lines.append("### 四因子正式信号")
    lines.append("- 行业宽度 ≥35%、量比 ≥1.0、20日收益 >0、过去60日多数时间宽度 <35%")
    lines.append("- FIRST：120日内首次触发 → 试仓8%；REPEAT：再度触发 → 建仓12%")
    lines.append("")
    lines.append("### 信号雷达")
    lines.append("- 满足四因子中的3项，尚未完全触发。仅观察，不构成买入。")
    lines.append("")
    lines.append("### 主线结构")
    lines.append("- 扩散上涨：广泛上涨；集中抱团：少数强股主导；内部转弱：参与度下降")
    lines.append("")
    lines.append("### 相对强度")
    lines.append("- 行业横截面 20 日收益排名及变化趋势。仅辅助持有。")
    lines.append("")
    lines.append("### 退潮状态")
    lines.append("- 正常/预警/确认退潮。确认退潮建议减仓1/3。")
    lines.append("")
    lines.append("### 进度分桶")
    lines.append("- 极早期(-18%/+30%)、早期(-15%/+30%)、中期(-12%/+25%)、晚期(-8%/+20%)")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("*本系统为交易辅助工具。标的选择和最终交易由人工决定。辅助分析不阻断正式四因子信号。*")
    lines.append("")

    return "\n".join(lines)


def _append_radar_detail(lines: list, cand: dict, idx: int):
    """追加一个雷达候选详情（v1.1 含连续跟踪）"""
    ind = cand['industry']
    wl = cand.get("watch_level", "WATCH")
    wl_label = "强观察" if wl == "STRONG_WATCH" else "普通观察"
    streak = cand.get("consecutive_radar_days", 1)
    first_date = cand.get("first_radar_date", "")

    title = f"### {idx}. {ind} | {wl_label}"
    if streak >= 2:
        title += f" | 连续 {streak} 个交易日"
    lines.append(title)
    lines.append("")

    if first_date:
        lines.append(f"- 首次进入雷达：{first_date}")

    missing = cand.get("missing", [])
    missing_text = "；".join(missing) if missing else "无"
    lines.append(f"- 当前缺失条件：{missing_text}")

    # 距离阈值
    dist_val = cand.get("distance_to_threshold")
    dist_unit = cand.get("distance_unit", "")
    if dist_val is not None and dist_val > 0:
        lines.append(f"- 当前 {cand.get('missing_key', '?')}：距阈值 {dist_val}{dist_unit}")

    dist_change = cand.get("distance_change")
    is_improving = cand.get("is_improving", False)
    if dist_change is not None:
        change_dir = "改善" if is_improving else "扩大"
        lines.append(f"- 较上一交易日：{change_dir} {abs(dist_change)}{dist_unit}")

    imp_str = f"{cand['breadth_improvement_5d']*100:+.1f} 个百分点"
    lines.append(f"- 近 5 日行业宽度变化：{imp_str}")
    lines.append("")
    lines.append("| 因子 | 当前值 | 阈值 | 状态 |")
    lines.append("|---|---:|---:|---|")

    # 弱势背景
    low_days = int(cand.get("n_low_in_past_60d", 0))
    weakness = "满足" if cand.get("weakness_ok") else "未满足"
    lines.append(f"| 弱势背景 | {low_days}/60日 | ≥30日 | {weakness} |")

    # 行业宽度
    breadth = float(cand.get("breadth", 0)) * 100
    cond_b = "满足" if cand.get("cond_breadth") else "未满足"
    lines.append(f"| 行业宽度 | {breadth:.1f}% | ≥35% | {cond_b} |")

    # 平均量比
    vol = float(cand.get("vol_ratio", 1))
    cond_v = "满足" if cand.get("cond_vol") else "未满足"
    lines.append(f"| 平均量比 | {vol:.2f} | ≥1.00 | {cond_v} |")

    # 20日收益
    ret20 = float(cand.get("avg_ret20", 0)) * 100
    cond_r = "满足" if cand.get("cond_ret") else "未满足"
    lines.append(f"| 20日收益 | {ret20:+.1f}% | >0 | {cond_r} |")
    lines.append("")

    # 近5日宽度变化
    bw_chg = cand.get("breadth_improvement_5d")
    if bw_chg is not None:
        lines.append(f"- 近5日宽度变化：{bw_chg:+.1f} 个百分点")
    lines.append("")
    lines.append("*该行业仍是观察候选，不构成正式试仓信号。*")
    lines.append("")


def _append_position_detail(lines: list, r: dict, idx: int):
    """追加一笔持仓的完整分析"""
    si = r.get("source_industry")
    target = r.get("target", "?")
    title = f"{si} | {target}" if si else target

    lines.append(f"### 持仓{idx}：{title}")
    lines.append("")

    # 基本信息
    action = r.get("action", "HOLD")
    ret_pct = r.get("return_pct", 0) * 100
    entry = r.get("entry_price", 0)
    current = r.get("current_price", 0)
    sig_type = r.get("source_signal_type") or "—"
    sig_priority = r.get("source_signal_priority") or "—"

    lines.append("#### 1. 基本信息")
    lines.append("")
    lines.append("| 项目 | 内容 |")
    lines.append("|---|---|")
    lines.append(f"| 实际标的 | {target} |")
    lines.append(f"| 来源行业 | {si or '待补录'} |")
    lines.append(f"| 入场信号 | {sig_type} |")
    lines.append(f"| 信号优先级 | {sig_priority} |")
    lines.append(f"| 当前操作 | {action} |")
    lines.append("")

    # 核心结论
    lines.append("#### 2. 当前核心结论")
    lines.append("")
    if si:
        ms = r.get("mainline_structure") or {}
        rs = r.get("relative_strength") or {}
        lines.append(f"行业趋势：{'仍在' if ms.get('structure') != 'INTERNAL_WEAKENING' else '内部转弱'}。"
                     f"主线结构表现为 {ms.get('structure_label', '—')}，"
                     f"相对强度 {rs.get('strength_label', '—')}，"
                     f"退潮状态 {r.get('retreat_stage') or '正常'}。"
                     f"当前{'建议 ' + action if action != 'HOLD' else '继续持有'}。")
    else:
        lines.append("来源行业尚未补录，暂不能进行行业趋势、主线结构和退潮分析。")
    lines.append("")

    # 以下章节始终保留，内容根据 si 是否有值决定
    _append_mainline_section(lines, r, si)
    _append_strength_section(lines, r, si)
    _append_trend_section(lines, r, si)
    _append_retreat_section(lines, r, si)
    _append_price_section(lines, r, si, action, ret_pct)
    _append_final_judgment(lines, r, si, action)


def _append_mainline_section(lines, r, si):
    lines.append("#### 3. 主线结构分析")
    lines.append("")
    if not si:
        lines.append("暂不可判断：来源行业尚未补录。")
        lines.append("")
        return
    ms = r.get("mainline_structure") or {}
    lines.append(f"**当前结构：{ms.get('structure_label', '—')}**")
    lines.append("")
    lines.append(f"**持仓参考：{ms.get('holding_guidance', '—')}**")
    lines.append("")
    metrics = ms.get("key_metrics") or {}
    if metrics:
        lines.append("| 指标 | 数值 |")
        lines.append("|---|---:|")
        for k, v in list(metrics.items())[:7]:
            if isinstance(v, float):
                lines.append(f"| {k} | {v:.3f} |")
            else:
                lines.append(f"| {k} | {v} |")
        lines.append("")
    evidence = ms.get("supporting_evidence") or []
    if evidence:
        lines.append("**支持依据：**")
        lines.append("")
        for e in evidence:
            lines.append(f"1. {e}")
        lines.append("")
    risk = ms.get("risk_evidence") or []
    if risk:
        lines.append("**风险或矛盾：**")
        lines.append("")
        for e in risk:
            lines.append(f"1. {e}")
        lines.append("")
    lines.append(f"**分析结论：** {ms.get('summary', '—')}")
    lines.append("")


def _append_strength_section(lines, r, si):
    lines.append("#### 4. 相对强度分析")
    lines.append("")
    if not si:
        lines.append("暂不可判断：来源行业尚未补录。")
        lines.append("")
        return
    rs = r.get("relative_strength") or {}
    lines.append(f"**当前状态：{rs.get('strength_label', '—')}**")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("|---|---:|")
    if rs.get("pct_20d") is not None:
        lines.append(f"| 20日收益排名 | 前 {rs['pct_20d']}% |")
        lines.append(f"| 20日排名百分位 | {rs.get('ret20_rank', '—')} |")
    if rs.get("rank_change_10d") is not None:
        lines.append(f"| 近10日排名变化 | {rs['rank_change_10d']:+.3f} |")
    if rs.get("pct_60d") is not None:
        lines.append(f"| 60日收益排名 | 前 {rs['pct_60d']}% |")
    else:
        lines.append("| 60日排名 | 暂无可靠数据 |")
    lines.append("")
    lines.append(f"**分析：** {rs.get('explanation', '—')}")
    lines.append("")
    lines.append("*相对强度仅用于辅助持有，不产生独立交易动作。*")
    lines.append("")


def _append_trend_section(lines, r, si):
    lines.append("#### 5. 趋势延续")
    lines.append("")
    if not si:
        lines.append("暂不可判断：来源行业或来源信号日期尚未补录。")
        lines.append("")
        return
    tc = r.get("trend_continuation") or {}
    if tc.get("status") == "too_early":
        lines.append("**状态：观察期不足**")
        lines.append("")
        lines.append(f"- 信号触发后仅经过 {tc.get('days_since', 0)} 个交易日")
        lines.append("")
        lines.append("*该状态只用于解释，不影响交易动作。*")
        lines.append("")
        return
    lines.append(f"**状态：{tc.get('label', '—')}**")
    lines.append("")
    days = tc.get("days_since", 0)
    lines.append(f"- 信号触发后经过：{days} 个交易日")
    if tc.get("breadth_change") is not None:
        lines.append(f"- 信号后 MA20 宽度变化：{tc['breadth_change']:+.1%}")
    if tc.get("ret_change") is not None:
        lines.append(f"- 信号后行业中位数收益变化：{tc['ret_change']:+.1%}")
    lines.append("")
    lines.append("*该状态只用于解释，不影响交易动作。*")
    lines.append("")


def _append_retreat_section(lines, r, si):
    lines.append("#### 6. 退潮信号分析")
    lines.append("")
    if not si:
        lines.append("暂不可判断：来源行业尚未补录，不能计算行业退潮状态。")
        lines.append("")
        return
    ret_action = r.get("retreat_action", "NORMAL")
    ret_score = r.get("retreat_score", 0)
    ret_stage = r.get("retreat_stage") or "无"
    ret_reduced = r.get("retreat_reduced", False)
    status_text = {"NORMAL": "正常", "CONFIRMED_RETREAT": "确认退潮"}

    lines.append(f"**退潮状态：{status_text.get(ret_action, '预警')}**")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("|---|---:|")
    lines.append(f"| 退潮评分 | {ret_score:.1f} |")
    lines.append(f"| 原始阶段 | {ret_stage} |")
    lines.append(f"| 业务状态 | {ret_action} |")
    lines.append(f"| 是否已退潮减仓 | {'是' if ret_reduced else '否'} |")
    lines.append("")

    components = r.get("retreat_components") or {}
    triggers = []
    if float(components.get("ret5", 0)) < -0.08: triggers.append("5日急跌")
    if float(components.get("drawdown20", 0)) < -0.10: triggers.append("回撤过大")
    if float(components.get("above20", 0)) < 0.40: triggers.append("宽度不足")
    lines.append(f"**主要触发因素：** {', '.join(triggers) if triggers else '暂无明显退潮触发项'}")
    lines.append("")

    offsets = []
    if float(components.get("ret5", 0)) > 0: offsets.append("5日收益仍为正")
    if float(components.get("above20", 0)) >= 0.35: offsets.append("MA20 宽度高于 35%")
    lines.append(f"**抵消因素：** {', '.join(offsets) if offsets else '无'}")
    lines.append("")

    if ret_action == "CONFIRMED_RETREAT":
        lines.append("**退潮条件已达到确认标准，建议下一交易日减仓 1/3。**")
    elif ret_action not in ("NORMAL",):
        lines.append("**行业内部出现转弱迹象，暂不减仓，继续重点观察。**")
    else:
        lines.append("**当前尚未达到退潮预警或确认退潮标准，继续按照原有持仓规则观察。**")
    lines.append("")


def _append_price_section(lines, r, si, action, ret_pct):
    lines.append("#### 7. 价格风控")
    lines.append("")
    lines.append("| 项目 | 数值 |")
    lines.append("|---|---:|")
    entry = r.get("entry_price", 0) or 0
    current = r.get("current_price", 0) or 0
    stop_price = r.get("stop_price", 0) or 0
    tp_price = r.get("take_profit_price", 0) or 0
    lines.append(f"| 买入价 | {entry:.4f} |")
    lines.append(f"| 当前价 | {current:.4f} |")
    lines.append(f"| 当前收益 | {ret_pct:+.1f}% |")
    lines.append(f"| 入场进度桶 | {r.get('progress_bucket_at_entry', '—')} |")
    lines.append(f"| 当前进度桶 | {r.get('current_bucket', '—')} |")
    lines.append(f"| 止损价 | {stop_price:.4f} |")
    lines.append(f"| 止盈价 | {tp_price:.4f} |")
    ds = r.get("distance_to_stop")
    dt = r.get("distance_to_tp")
    lines.append(f"| 距止损线 | {ds*100:+.1f}% |" if ds is not None else "| 距止损线 | — |")
    lines.append(f"| 距止盈线 | {dt*100:+.1f}% |" if dt is not None else "| 距止盈线 | — |")
    lines.append("")
    lines.append("价格风控是最终硬性防线，与行业退潮监控并行运行。")
    lines.append("")


def _append_final_judgment(lines, r, si, action):
    lines.append("#### 8. 最终判断")
    lines.append("")
    if si:
        ms = r.get("mainline_structure") or {}
        rs = r.get("relative_strength") or {}
        status_text = {"NORMAL": "正常", "CONFIRMED_RETREAT": "确认退潮"}
        lines.append(f"- 行业趋势：{'仍在' if ms.get('structure') != 'INTERNAL_WEAKENING' else '内部转弱'}")
        lines.append(f"- 主线结构：{ms.get('structure_label', '—')}")
        lines.append(f"- 相对强度：{rs.get('strength_label', '—')}")
        lines.append(f"- 趋势延续：{(r.get('trend_continuation') or {}).get('label', '—')}")
        lines.append(f"- 退潮状态：{status_text.get(r.get('retreat_action', 'NORMAL'), '正常')}")
        lines.append(f"- 价格风控：{'触发' if action in ('CLEAR', 'REDUCE_1_3') else '未触发'}")
        lines.append(f"- 最终操作：{'继续持有' if action == 'HOLD' else action}")
        lines.append("")
        if action == "RETREAT_REDUCE_1_3":
            lines.append("确认退潮已达到减仓标准，建议下一交易日执行。")
        elif action == "REDUCE_1_3":
            lines.append("止盈条件触发，建议锁定部分利润。")
        elif action == "CLEAR":
            lines.append("止损条件触发，需要立即清仓离场。")
        else:
            lines.append("当前没有明确减仓或清仓条件。继续按原有策略持有，密切跟踪退潮发展。")
    else:
        lines.append("- 行业趋势：暂不可判断")
        lines.append("- 主线结构：暂不可判断")
        lines.append("- 相对强度：暂不可判断")
        lines.append("- 趋势延续：暂不可判断")
        lines.append("- 退潮状态：暂不可判断")
        lines.append("- 价格风控：未触发")
        lines.append("- 最终操作：HOLD")
        lines.append("")
        lines.append("来源行业尚未补录，因此当前只能确认价格止损和止盈均未触发，不能判断行业主线逻辑和退潮状态。HOLD 仅来自当前价格风控未触发，不代表行业逻辑已确认正常。")
    lines.append("")


def render_blocked_report_md(
    requested_date: str,
    signal_data_date: str,
    validation: dict,
) -> str:
    """数据阻断版 Markdown"""
    lines = [
        "# 掘金信号数据阻断报告",
        "",
        f"- 请求日期：{requested_date}",
        f"- 预期交易日：{validation.get('expected_trade_date', '不可用')}",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## 数据状态",
        "",
    ]
    for k in ["daily_hfq_date", "stock_daily_raw_date", "etf_daily_date"]:
        v = validation.get(k, "—")
        lines.append(f"- {k}：{v}")
    lines.append("")

    if validation.get("errors"):
        lines.append("## 错误")
        lines.append("")
        for e in validation["errors"]:
            lines.append(f"- {e}")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("*本报告不提供任何买卖建议。请先完成行情数据更新后重新生成。*")
    lines.append("")

    return "\n".join(lines)
