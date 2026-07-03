"""
掘金日报 Markdown 研究版渲染器

输入与 HTML 版完全相同的 report_data，输出独立完整的 Markdown 研究报告。
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd


def render_markdown_report(
    requested_date: str = None,
    signal_data_date: str = None,
    today_signals: pd.DataFrame = None,
    monitoring: list = None,
    radar_candidates: list = None,
    validation: dict = None,
    open_positions: list = None,
) -> str:
    """生成 Markdown 研究版日报"""

    if requested_date is None:
        requested_date = datetime.now().strftime("%Y-%m-%d")
    if signal_data_date is None:
        signal_data_date = requested_date

    lines = []

    # ── 页头 ──
    sd_dt = pd.to_datetime(signal_data_date)
    today_only = today_signals[today_signals["signal_date"] == sd_dt] if today_signals is not None else pd.DataFrame()
    n_today = len(today_only)
    n_positions = len(monitoring) if monitoring else 0
    n_radar = len(radar_candidates) if radar_candidates else 0

    lines.append("# 掘金信号日报")
    lines.append("")
    lines.append(f"- 报告日期：{requested_date}")
    if signal_data_date != requested_date:
        lines.append(f"- 行情数据截至：{signal_data_date}")
    lines.append(f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- 系统版本：v0.6")

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

    # 持仓来源行业状态
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
            lines.append(f"#### 信息概要")
            lines.append("")
            lines.append(f"- 信号日期：{sig['signal_date']}")
            n_stocks = int(sig.get("n_stocks", 0))
            lines.append(f"- 行业包含 {n_stocks} 只成分股")
            lines.append(f"- 系统匹配 ETF 参考见下游标的匹配表")
            lines.append("")
            lines.append(f"该行业此前长期处于低宽度状态，当前行业参与度重新突破 35% 阈值，成交量恢复，同时 20 日收益转正，形成正式企稳重估信号。")
            lines.append("")
    else:
        lines.append("今日没有行业同时满足四项正式触发条件。")
        lines.append("")

    # ═══════ 三、信号雷达 ═══════
    lines.append("## 三、信号雷达")
    lines.append("")
    if radar_candidates:
        for i, cand in enumerate(radar_candidates, 1):
            lines.append(f"### {i}. {cand['industry']} | 3/4 观察")
            lines.append("")
            missing = cand.get("missing_conditions", "?")
            lines.append(f"**尚缺条件：{missing}**")
            lines.append("")
            lines.append("| 因子 | 当前值 | 阈值 | 状态 |")
            lines.append("|---|---:|---:|---|")
            factors = cand.get("factor_status", {}) or {}
            for fname, finfo in factors.items():
                lines.append(f"| {fname} | {finfo.get('value','—')} | {finfo.get('threshold','—')} | {finfo.get('verdict','—')} |")
            lines.append("")
            lines.append(f"*该行业仍是观察候选，不构成正式试仓信号。*")
            lines.append("")
    else:
        lines.append("今日无 3/4 观察行业。")
        lines.append("")

    # ═══════ 四、持仓总览 ═══════
    lines.append("## 四、持仓总览")
    lines.append("")
    if monitoring:
        lines.append("| 来源行业 | 标的 | 当前收益 | 主线结构 | 相对强度 | 退潮状态 | 最终操作 |")
        lines.append("|---|---:|---|---|---|---|")
        for r in monitoring:
            si = r.get("source_industry") or "待补录"
            ms = (r.get("mainline_structure") or {}).get("structure_label", "—")
            rs = (r.get("relative_strength") or {}).get("strength_label", "—")
            ret_stage = r.get("retreat_stage") or "—"
            ret_pct = r.get("return_pct", 0) * 100
            action = r.get("action", "HOLD")
            lines.append(f"| {si} | {r.get('target', '?')} | {ret_pct:+.1f}% | {ms} | {rs} | {ret_stage} | {action} |")
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
            lines.append(f"1. **{r.get('target', '?')}** 来源行业尚未补录，暂不能进行主线、强度和退潮分析。")
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
        lines.append("来源行业尚未补录，暂不能进行行业退潮和主线结构分析。")
    lines.append("")

    # 主线结构
    ms = r.get("mainline_structure") or {}
    if ms and si:
        lines.append("#### 3. 主线结构分析")
        lines.append("")
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

    # 相对强度
    rs = r.get("relative_strength") or {}
    if rs and si:
        lines.append("#### 4. 相对强度分析")
        lines.append("")
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

    # 趋势延续
    tc = r.get("trend_continuation") or {}
    if tc and tc.get("status") != "too_early" and si:
        lines.append("#### 5. 趋势延续")
        lines.append("")
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

    # 退潮
    if si:
        lines.append("#### 6. 退潮信号分析")
        lines.append("")
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
        if triggers:
            lines.append(f"**主要触发因素：** {', '.join(triggers)}")
        else:
            lines.append("**主要触发因素：** 暂无明显退潮触发项")
        lines.append("")

        offsets = []
        if float(components.get("ret5", 0)) > 0: offsets.append("5日收益仍为正")
        if float(components.get("above20", 0)) >= 0.35: offsets.append("MA20 宽度高于 35%")
        if offsets:
            lines.append(f"**抵消因素：** {', '.join(offsets)}")
        else:
            lines.append("**抵消因素：** 无")
        lines.append("")

        if ret_action == "CONFIRMED_RETREAT":
            lines.append("**退潮条件已达到确认标准，建议下一交易日减仓 1/3。**")
        elif ret_action not in ("NORMAL",):
            lines.append("**行业内部出现转弱迹象，暂不减仓，继续重点观察。**")
        else:
            lines.append("**当前尚未达到退潮预警或确认退潮标准，继续按照原有持仓规则观察。**")
        lines.append("")

    # 价格风控
    lines.append("#### 7. 价格风控")
    lines.append("")
    lines.append("| 项目 | 数值 |")
    lines.append("|---|---:|")
    lines.append(f"| 买入价 | {entry:.4f}" if entry else "| 买入价 | — |")
    lines.append(f"| 当前价 | {current:.4f}" if current else "| 当前价 | — |")
    lines.append(f"| 当前收益 | {ret_pct:+.1f}% |")
    lines.append(f"| 入场进度桶 | {r.get('progress_bucket_at_entry', '—')} |")
    lines.append(f"| 当前进度桶 | {r.get('current_bucket', '—')} |")
    lines.append(f"| 止损价 | {r.get('stop_price', 0):.4f}" if r.get("stop_price") else "| 止损价 | — |")
    lines.append(f"| 止盈价 | {r.get('take_profit_price', 0):.4f}" if r.get("take_profit_price") else "| 止盈价 | — |")
    if r.get("distance_to_stop") is not None:
        lines.append(f"| 距止损线 | {r['distance_to_stop']*100:+.1f}% |")
    if r.get("distance_to_tp") is not None:
        lines.append(f"| 距止盈线 | {r['distance_to_tp']*100:+.1f}% |")
    lines.append("")
    lines.append("价格风控是最终硬性防线，与行业退潮监控并行运行。")
    lines.append("")

    # 最终判断
    lines.append("#### 8. 最终判断")
    lines.append("")
    ms_type = (r.get("mainline_structure") or {}).get("structure_label", "—")
    rs_type = (r.get("relative_strength") or {}).get("strength_label", "—")
    lines.append(f"- 行业趋势：{'仍在' if ms.get('structure') != 'INTERNAL_WEAKENING' else '内部转弱'}")
    lines.append(f"- 主线结构：{ms_type}")
    lines.append(f"- 相对强度：{rs_type}")
    lines.append(f"- 退潮状态：{status_text.get(r.get('retreat_action', 'NORMAL'), '正常')}")
    lines.append(f"- 价格风控：{'触发' if action in ('CLEAR', 'REDUCE_1_3') else '未触发'}")
    lines.append(f"- 最终操作：{'继续持有' if action == 'HOLD' else action}")

    if action == "RETREAT_REDUCE_1_3":
        lines.append("")
        lines.append("确认退潮已达到减仓标准，建议下一交易日执行。")
    elif action == "REDUCE_1_3":
        lines.append("")
        lines.append("止盈条件触发，建议锁定部分利润。")
    elif action == "CLEAR":
        lines.append("")
        lines.append("止损条件触发，需要立即清仓离场。")
    else:
        lines.append("")
        lines.append("当前没有明确减仓或清仓条件。继续按原有策略持有，密切跟踪退潮发展。")
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
