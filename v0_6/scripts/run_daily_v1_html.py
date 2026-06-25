"""
v0.6 日报 V1.0 HTML 渲染器（最终版）

设计原则：
- 5 秒看完：4 张摘要卡
- 30 秒决策：今日操作 + 持仓监控
- 1 分钟研究：详细数据（点开展开）

设计风格：
- 极简卡片化布局
- 颜色编码：红/橙/黄/绿
- 响应式：手机/平板/桌面自适应
- 打印友好：可导出 PDF

用法：
    python v0_6/scripts/run_daily_v1_html.py 2026-06-24
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

V0_6_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(V0_6_ROOT))

from v0_6.core import (
    compute_industry_daily_metrics,
    compute_per_stock_indicators,
    detect_stabilizing_b,
    load_raw_daily,
    mark_repeat_priority,
    monitor_all_positions_v6,
    RULES,
)
from v0_6.core.config import DAILY_DIR
from v0_6.core.etf_matcher import match_industry_to_etfs

import pandas as pd


# ============== 完整设计系统（Design Tokens）==============

HTML_STYLE = """
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,500;9..144,700;9..144,900&family=Noto+Serif+SC:wght@400;500;700;900&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
<style>
:root {
  /* === 编辑风档案色板（OKLCH，避开纯灰） === */
  --paper:        oklch(97% 0.012 80);     /* 奶白，带极轻暖 */
  --paper-2:      oklch(94% 0.014 78);     /* 略深底色 */
  --ink:          oklch(22% 0.018 50);     /* 暖墨主文字 */
  --ink-2:        oklch(42% 0.020 50);     /* 次级文字 */
  --ink-3:        oklch(60% 0.018 55);     /* 注释 */
  --rule:         oklch(85% 0.015 75);     /* 暖灰分隔线 */
  --rule-2:       oklch(90% 0.013 78);

  /* 勃艮第 + 暗金（金融专业色，避开 AI 紫蓝渐变） */
  --accent:       oklch(45% 0.16 22);      /* 勃艮第红——告警、止损 */
  --accent-bg:    oklch(95% 0.04 25);
  --accent-2:     oklch(62% 0.13 70);      /* 暗金——止盈、顺 */
  --accent-2-bg:  oklch(95% 0.04 80);
  --warn:         oklch(65% 0.13 50);      /* 橙——关注 */
  --warn-bg:      oklch(95% 0.05 55);
  --neutral-bg:   oklch(92% 0.012 80);
  --ink-on-dark:  oklch(96% 0.01 80);

  /* 字体 */
  --font-display: "Fraunces", "Noto Serif SC", "Songti SC", "SimSun", Georgia, serif;
  --font-body:    "Noto Serif SC", "Songti SC", "SimSun", Georgia, serif;
  --font-mono:    "JetBrains Mono", "SF Mono", Consolas, monospace;

  /* 间距（基于 1.5 行高节奏，base 16px → 24px 单元） */
  --u: 8px;
  --u2: 16px; --u3: 24px; --u4: 32px; --u6: 48px; --u8: 64px; --u12: 96px;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
html { background: var(--paper-2); }
body {
  font-family: var(--font-body);
  background: var(--paper);
  color: var(--ink);
  line-height: 1.6;
  font-size: 16px;
  font-feature-settings: "kern", "liga";
  -webkit-font-smoothing: antialiased;
}

/* === 页面骨架 === */
.sheet {
  max-width: 780px;
  margin: 0 auto;
  padding: var(--u6) var(--u4) var(--u8);
  background: var(--paper);
  position: relative;
}
.sheet::before {
  /* 左侧装订线 */
  content: "";
  position: absolute;
  left: 32px;
  top: var(--u6);
  bottom: var(--u6);
  width: 1px;
  background: var(--accent);
  opacity: 0.35;
}
@media (max-width: 720px) {
  .sheet { padding: var(--u4) var(--u3) var(--u6); }
  .sheet::before { left: 14px; }
}

/* === 顶部刊头（编辑风） === */
.masthead {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  padding-bottom: var(--u3);
  border-bottom: 2px solid var(--ink);
  margin-bottom: var(--u2);
  text-transform: uppercase;
  letter-spacing: 0.18em;
  font-family: var(--font-display);
  font-size: 11px;
  font-weight: 500;
  color: var(--ink-2);
}
.masthead .vol { font-style: italic; }
.masthead .issue { font-variant-numeric: tabular-nums; }

.dateline {
  display: grid;
  grid-template-columns: 1.2fr 1fr;
  gap: var(--u4);
  align-items: end;
  padding: var(--u4) 0 var(--u6);
  border-bottom: 1px solid var(--rule);
  margin-bottom: var(--u6);
}
@media (max-width: 600px) { .dateline { grid-template-columns: 1fr; gap: var(--u2); } }
.dl-date {
  font-family: var(--font-display);
  font-weight: 900;
  font-size: clamp(48px, 11vw, 96px);
  line-height: 0.9;
  letter-spacing: -0.04em;
  color: var(--ink);
  font-variant-numeric: tabular-nums;
  font-feature-settings: "tnum", "lnum";
}
.dl-date .yr { color: var(--ink-3); font-weight: 400; font-style: italic; }
.dl-title {
  font-family: var(--font-display);
  font-size: clamp(28px, 5vw, 42px);
  font-weight: 700;
  line-height: 1.05;
  letter-spacing: -0.02em;
  color: var(--ink);
  text-align: right;
}
@media (max-width: 600px) { .dl-title { text-align: left; } }
.dl-subtitle {
  margin-top: var(--u2);
  font-size: 13px;
  color: var(--ink-2);
  font-style: italic;
  line-height: 1.5;
}

/* === 摘要数字墙（4 个并排，editorial 排列） === */
.metrics {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 0;
  margin-bottom: var(--u8);
  border-top: 1px solid var(--rule);
  border-bottom: 1px solid var(--rule);
}
@media (max-width: 600px) { .metrics { grid-template-columns: repeat(2, 1fr); } }
.metric {
  padding: var(--u3) var(--u2) var(--u2);
  border-right: 1px solid var(--rule);
  position: relative;
}
.metric:last-child { border-right: none; }
@media (max-width: 600px) {
  .metric:nth-child(2) { border-right: none; }
  .metric:nth-child(1), .metric:nth-child(2) { border-bottom: 1px solid var(--rule); }
}
.metric-num {
  font-family: var(--font-display);
  font-weight: 900;
  font-size: clamp(40px, 7vw, 56px);
  line-height: 0.9;
  letter-spacing: -0.03em;
  font-variant-numeric: tabular-nums;
  color: var(--ink);
}
.metric.alert .metric-num { color: var(--accent); }
.metric.up   .metric-num { color: var(--accent-2); }
.metric-key {
  margin-top: var(--u2);
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.2em;
  color: var(--ink-3);
  font-weight: 500;
  font-family: var(--font-display);
}
.metric-note {
  margin-top: 2px;
  font-size: 11px;
  color: var(--ink-2);
  font-style: italic;
}

/* === 章节（编辑风：编号 + 标题） === */
.section { margin-bottom: var(--u8); }
.sec-head {
  display: flex;
  align-items: baseline;
  gap: var(--u2);
  margin-bottom: var(--u3);
  padding-bottom: var(--u2);
  border-bottom: 1px solid var(--ink);
}
.sec-num {
  font-family: var(--font-display);
  font-weight: 400;
  font-style: italic;
  font-size: 14px;
  color: var(--accent);
  letter-spacing: 0.05em;
}
.sec-title {
  font-family: var(--font-display);
  font-weight: 700;
  font-size: 24px;
  letter-spacing: -0.01em;
  color: var(--ink);
  line-height: 1.1;
}
.sec-tag {
  margin-left: auto;
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--ink-3);
  text-transform: uppercase;
  letter-spacing: 0.15em;
}

/* === 操作清单（日报风，左边竖线） === */
.dispatch { display: flex; flex-direction: column; }
.dispatch-item {
  display: grid;
  grid-template-columns: 90px 1fr auto;
  gap: var(--u3);
  align-items: baseline;
  padding: var(--u3) 0;
  border-bottom: 1px dashed var(--rule);
  position: relative;
  padding-left: var(--u3);
  border-left: 3px solid var(--accent-2);
}
.dispatch-item.FIRST { border-left-color: var(--ink); }
.dispatch-item.REPEAT { border-left-color: var(--accent); }
.dispatch-num {
  font-family: var(--font-display);
  font-style: italic;
  font-size: 13px;
  color: var(--ink-3);
  font-variant-numeric: tabular-nums;
}
.dispatch-name {
  font-family: var(--font-display);
  font-weight: 700;
  font-size: 22px;
  color: var(--ink);
  letter-spacing: -0.01em;
}
.dispatch-meta {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--ink-2);
  text-align: right;
  line-height: 1.7;
}
.dispatch-meta b { color: var(--ink); font-weight: 700; }
.dispatch-tag {
  display: inline-block;
  font-family: var(--font-display);
  font-weight: 700;
  font-style: italic;
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.15em;
  padding: 2px 6px;
  margin-right: var(--u);
  vertical-align: middle;
}
.dispatch-tag.FIRST  { background: var(--ink); color: var(--ink-on-dark); }
.dispatch-tag.REPEAT { background: var(--accent); color: var(--ink-on-dark); }

/* ETF 行 */
.etf-line {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 0;
  font-family: var(--font-mono);
  font-size: 12px;
  border-top: 1px dashed var(--rule);
  margin-top: 4px;
  flex-wrap: wrap;
}
.etf-line.main { color: var(--ink); }
.etf-line.alt { color: var(--ink-2); padding-left: 4px; }
.etf-line.none { color: var(--accent); font-style: italic; }
.etf-tag {
  display: inline-block;
  background: var(--ink);
  color: var(--ink-on-dark);
  padding: 2px 6px;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.05em;
}
.etf-tag.light {
  background: transparent;
  color: var(--ink-3);
  border: 1px solid var(--rule);
  font-weight: 500;
}
.etf-name { font-weight: 600; }
.etf-code {
  color: var(--accent);
  font-weight: 700;
  background: var(--accent-bg);
  padding: 1px 5px;
  font-size: 11px;
}
.etf-purity {
  display: inline-block;
  padding: 1px 5px;
  font-size: 9px;
  font-weight: 700;
  background: var(--ink-3);
  color: white;
  letter-spacing: 0.05em;
}
.etf-purity.pure { background: var(--accent-2); }
.etf-purity.impure { background: var(--warn); }
.etf-purity.bad { background: var(--accent); }
.etf-conf {
  margin-left: auto;
  font-size: 10px;
  color: var(--ink-3);
  font-style: italic;
}

.empty {
  padding: var(--u4) var(--u3);
  background: transparent;
  border: 1px dashed var(--rule);
  font-style: italic;
  color: var(--ink-3);
  text-align: center;
  font-size: 14px;
}

/* === 持仓卡（报纸分栏） === */
.ledgers { display: flex; flex-direction: column; gap: 0; }
.ledger {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--u3);
  padding: var(--u3) 0;
  border-top: 1px solid var(--rule);
  position: relative;
}
.ledger:last-child { border-bottom: 1px solid var(--rule); }
.ledger::before {
  content: "";
  position: absolute;
  top: var(--u3);
  left: -8px;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--accent-2);
}
.ledger.CLEAR::before    { background: var(--accent); }
.ledger.REDUCE_1_3::before{ background: var(--warn); }
.ledger.WATCH::before     { background: var(--warn); }
.ledger.ADJUST::before    { background: var(--ink-3); }
.ledger.HOLD::before      { background: var(--accent-2); }

@media (max-width: 600px) { .ledger { grid-template-columns: 1fr; gap: var(--u2); } }

.led-left .led-name {
  font-family: var(--font-display);
  font-weight: 700;
  font-size: 24px;
  letter-spacing: -0.01em;
  color: var(--ink);
  margin-bottom: 2px;
}
.led-left .led-action-tag {
  display: inline-block;
  font-family: var(--font-display);
  font-weight: 700;
  font-style: italic;
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.15em;
  padding: 2px 8px;
  margin-bottom: var(--u);
  background: var(--ink-3);
  color: var(--ink-on-dark);
}
.led-left .led-action-tag.CLEAR    { background: var(--accent); }
.led-left .led-action-tag.REDUCE_1_3{ background: var(--warn); }
.led-left .led-action-tag.WATCH    { background: var(--warn); }
.led-left .led-action-tag.ADJUST   { background: var(--ink); }
.led-left .led-action-tag.HOLD     { background: var(--accent-2); }

.led-left .led-bucket {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--ink-2);
  text-transform: uppercase;
  letter-spacing: 0.1em;
}
.led-left .led-bucket b { color: var(--ink); font-weight: 700; }

.led-right {
  text-align: right;
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--ink-2);
  line-height: 1.6;
}
.led-pct {
  font-family: var(--font-display);
  font-weight: 900;
  font-size: 30px;
  letter-spacing: -0.02em;
  font-variant-numeric: tabular-nums;
  line-height: 1;
  margin-bottom: 4px;
}
.led-pct.positive { color: var(--accent-2); }
.led-pct.negative { color: var(--accent); }
.led-right .led-stops b { color: var(--ink); font-weight: 700; }

.led-advice {
  grid-column: 1 / -1;
  margin-top: var(--u2);
  padding: var(--u2) var(--u3);
  font-size: 13px;
  font-style: italic;
  border-left: 2px solid var(--rule);
  color: var(--ink-2);
  background: var(--neutral-bg);
}
.led-advice.CLEAR     { background: var(--accent-bg);  border-color: var(--accent); color: var(--accent); font-weight: 600; font-style: normal; }
.led-advice.REDUCE_1_3{ background: var(--warn-bg);     border-color: var(--warn);    color: var(--ink);     font-weight: 600; }

/* === 规则速查（4 桶排版：横向小册子） === */
.buckets {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 0;
  border: 1px solid var(--ink);
}
@media (max-width: 600px) { .buckets { grid-template-columns: repeat(2, 1fr); } }
.bucket {
  padding: var(--u3) var(--u2);
  border-right: 1px solid var(--ink);
  text-align: center;
  background: var(--paper);
  position: relative;
}
.bucket:last-child { border-right: none; }
@media (max-width: 600px) {
  .bucket:nth-child(2) { border-right: none; }
  .bucket:nth-child(1), .bucket:nth-child(2) { border-bottom: 1px solid var(--ink); }
}
.bucket-num {
  font-family: var(--font-display);
  font-style: italic;
  font-size: 11px;
  color: var(--accent);
  letter-spacing: 0.1em;
}
.bucket-name {
  font-family: var(--font-display);
  font-weight: 700;
  font-size: 16px;
  margin: var(--u) 0 var(--u2);
  color: var(--ink);
}
.bucket-trigger {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--ink-3);
  text-transform: uppercase;
  letter-spacing: 0.1em;
  margin-bottom: var(--u2);
  padding-bottom: var(--u2);
  border-bottom: 1px dashed var(--rule);
}
.bucket-stop {
  font-family: var(--font-display);
  font-weight: 900;
  font-size: 22px;
  color: var(--accent);
  font-variant-numeric: tabular-nums;
  line-height: 1;
  margin-bottom: 2px;
}
.bucket-stop-label, .bucket-tp-label {
  font-family: var(--font-display);
  font-style: italic;
  font-size: 9px;
  color: var(--ink-3);
  text-transform: uppercase;
  letter-spacing: 0.15em;
  margin-bottom: var(--u2);
}
.bucket-tp {
  font-family: var(--font-display);
  font-weight: 900;
  font-size: 22px;
  color: var(--accent-2);
  font-variant-numeric: tabular-nums;
  line-height: 1;
}

/* === Footer（编辑风收尾） === */
.colophon {
  margin-top: var(--u8);
  padding-top: var(--u3);
  border-top: 1px solid var(--rule);
  display: flex;
  justify-content: space-between;
  font-family: var(--font-display);
  font-style: italic;
  font-size: 11px;
  color: var(--ink-3);
  letter-spacing: 0.05em;
}
.colophon .stamp {
  text-transform: uppercase;
  letter-spacing: 0.2em;
  font-style: normal;
  font-weight: 700;
}

/* === 红色高亮块（重要告警用） === */
.alert-banner {
  background: var(--accent);
  color: var(--ink-on-dark);
  padding: var(--u3) var(--u4);
  margin-bottom: var(--u4);
  display: flex;
  align-items: center;
  gap: var(--u3);
}
.alert-banner .alert-icon {
  font-family: var(--font-display);
  font-weight: 900;
  font-size: 32px;
  line-height: 1;
}
.alert-banner .alert-text {
  font-family: var(--font-display);
  font-weight: 500;
  font-size: 14px;
  line-height: 1.4;
}
.alert-banner .alert-text b {
  font-weight: 900;
  font-size: 18px;
  display: block;
  font-style: italic;
  margin-bottom: 2px;
}

/* === 录入入口按钮（masthead 旁边） === */
.entry-cta {
  display: inline-flex;
  align-items: center;
  gap: var(--u);
  padding: 6px 12px;
  background: var(--ink);
  color: var(--ink-on-dark);
  text-decoration: none;
  font-family: var(--font-display);
  font-weight: 700;
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.15em;
  transition: background 120ms ease;
  border: none;
  cursor: pointer;
}
.entry-cta:hover { background: var(--accent); }
.entry-cta::before {
  content: "+";
  font-size: 16px;
  line-height: 1;
}
.entry-cta-inline {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  background: transparent;
  color: var(--accent);
  border: 1px solid var(--accent);
  text-decoration: none;
  font-family: var(--font-display);
  font-weight: 500;
  font-style: italic;
  font-size: 12px;
  letter-spacing: 0.05em;
  transition: all 120ms ease;
}
.entry-cta-inline:hover { background: var(--accent); color: var(--ink-on-dark); }

/* 数据日期提示（当生成日期 != 数据日期时） */
.data-note {
  font-size: 11px;
  color: var(--ink-3);
  font-family: var(--font-mono);
  font-style: italic;
  margin-top: 4px;
  letter-spacing: 0.02em;
}
</style>
"""


# ============== 数据获取 ==============

def get_signal_data_date(requested_date: str) -> str | None:
    """返回 daily_hfq 中不晚于 requested_date 的最大 trade_date。
    返回格式 YYYY-MM-DD，无可用数据时返回 None。"""
    import sqlite3
    from v0_6.core.config import STOCK_DATA_DB
    if not STOCK_DATA_DB.exists():
        return None
    con = sqlite3.connect(str(STOCK_DATA_DB))
    row = con.execute(
        "SELECT MAX(trade_date) FROM daily_hfq WHERE trade_date <= ?",
        (requested_date.replace("-", ""),),
    ).fetchone()
    con.close()
    if row and row[0]:
        d = row[0]
        return f"{d[:4]}-{d[4:6]}-{d[6:]}"
    return None


def get_today_signals(signal_data_date: str, lookback_days: int = 3):
    sd = load_raw_daily("2010-01-01", signal_data_date)
    sd_ind = compute_per_stock_indicators(sd)
    daily = compute_industry_daily_metrics(sd_ind)
    signals = detect_stabilizing_b(daily)
    signals = mark_repeat_priority(signals)
    # 信号按 signal_data_date 精确过滤（非自然日）
    # 改为返回 lookback 天内的信号，让 render_html 按 signal_data_date 筛选
    today_dt = pd.to_datetime(signal_data_date)
    return signals[signals["signal_date"] >= (today_dt - pd.Timedelta(days=lookback_days))]


def fmt_pct(x, signed=True):
    if x is None or pd.isna(x):
        return "—"
    s = f"{x*100:+.1f}%" if signed else f"{x*100:.1f}%"
    return s


def fmt_price(x):
    if x is None or pd.isna(x):
        return "—"
    return f"¥{x:.3f}"


# ============== 持仓卡片渲染 ==============

def render_position_card(r: dict) -> str:
    """单笔持仓——编辑风双栏（行业名 + 收益率；下方落建议）

    异常持仓（error）显示最小异常卡片，不显示价格/盈亏/止损止盈。
    """
    if "error" in r:
        error_type = r["error"]
        target_label = r.get("name") or r.get("target_name") or r.get("target", "?")
        entry_date = r.get("entry_date", "?")
        target_type = r.get("target_type", "?")
        # 优先使用 alert 消息
        alerts = r.get("alerts", [])
        err_msg = alerts[0].get("message", "") if alerts else ""
        if not err_msg:
            err_map = {
                "no_price_data": "暂无可用行情，无法计算盈亏、止损和止盈，请检查行情数据。",
                "industry_not_executable": "该记录是行业而非可交易标的，请人工核对。",
                "unknown_target_type": "标的类型无法识别，请人工核对历史记录。",
            }
            err_msg = err_map.get(error_type, f"状态异常: {error_type}")
        return f"""
<article class="ledger ERROR">
  <div class="led-left">
    <div class="led-name" style="color:var(--ink-3);">{target_label}</div>
    <span class="led-action-tag ERROR">⚠ {error_type}</span>
  </div>
  <div class="led-right">
    <div class="led-stops">类型 {target_type} · 入场 {entry_date}</div>
    <div class="led-stops" style="font-size:11px;color:var(--accent);">{err_msg}</div>
  </div>
  <div class="led-advice" style="background:transparent;color:var(--ink-3);">暂不计算盈亏 · 请人工处理</div>
</article>
"""
    action = r.get("action", "HOLD")
    ret = r.get("return_pct", 0)
    ret_class = "positive" if ret >= 0 else "negative"
    stop_pct = abs(r.get("stop_threshold", 0)) * 100
    tp_pct = r.get("take_profit_threshold", 0) * 100
    distance_to_stop = r.get("distance_to_stop", 0) * 100
    distance_to_tp = r.get("distance_to_tp", 0) * 100
    bucket = r.get("progress_bucket_at_entry", "")
    current_bucket = r.get("current_bucket", bucket)
    stop_price = r.get("stop_price", 0)
    tp_price = r.get("take_profit_price", 0)
    current_price = r.get("current_price", 0)
    price_date = r.get("today", "")

    # 操作建议文案
    if action == "CLEAR":
        advice = f"触发 {stop_pct:.0f}% 止损线 — 立即清仓离场"
    elif action == "REDUCE_1_3":
        advice = f"触发 {tp_pct:.0f}% 止盈线 — 减仓 1/3 锁定利润"
    elif action == "WATCH":
        if distance_to_stop < 5 and distance_to_stop > 0:
            advice = f"距止损线仅 {distance_to_stop:.0f}% — 保持密切关注"
        elif distance_to_tp < 5 and distance_to_tp > 0:
            advice = f"距止盈线仅 {distance_to_tp:.0f}% — 准备兑现"
        else:
            advice = "持仓观察中 — 无明显触发"
    elif action == "ADJUST":
        advice = f"进度桶由「{bucket}」切换至「{current_bucket}」 — 评估新规则适用性"
    else:
        advice = "按当前规则正常持有"

    bucket_indicator = f" → {current_bucket}" if bucket != current_bucket else ""

    return f"""
<article class="ledger {action}">
  <div class="led-left">
    <div class="led-name">{r.get('name') or r.get('target_name') or r.get('target', '?')}</div>
    <span class="led-action-tag {action}">{action}</span>
    <div class="led-bucket"><b>{bucket}</b>{bucket_indicator}</div>
  </div>
  <div class="led-right">
    <div class="led-pct {ret_class}">{fmt_pct(ret)}</div>
    <div class="led-stops">入场 {r['entry_date']}</div>
    <div class="led-stops">现价 {fmt_price(current_price)}</div>
    <div class="led-stops" style="font-size:10px;color:var(--ink-3);">价格截至 {price_date}</div>
    <div class="led-stops">止损 <b>{fmt_price(stop_price)}</b> · 止盈 <b>{fmt_price(tp_price)}</b></div>
  </div>
  <div class="led-advice {action}">{advice}</div>
</article>
"""


# ============== 主渲染 ==============

def render_html(requested_date: str, signal_data_date: str, today_signals: pd.DataFrame, monitoring: list) -> str:
    sd_dt = pd.to_datetime(signal_data_date)
    today_only = today_signals[today_signals["signal_date"] == sd_dt]
    n_today = len(today_only)
    n_repeat = len(today_only[today_only["priority"] == "REPEAT"])
    n_alerts = sum(1 for r in monitoring if r.get("priority") in ("RED", "YELLOW"))
    n_red = sum(1 for r in monitoring if r.get("priority") == "RED")
    n_positions = len(monitoring)

    # 解析日期为刊头需要的形式
    y, m, d = requested_date.split("-")

    # 空报告标记
    empty_mark = " · 空" if n_today == 0 else ""

    # 数据日期与生成日期不一致时，增加数据日期提示
    date_diff = requested_date != signal_data_date
    data_line = ""
    if date_diff:
        data_line = f'  <div class="data-note">信号数据截至 {signal_data_date}</div>'

    html = [f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>掘金日报 · {requested_date}{empty_mark} | Zhuxian Catch v0.6</title>
{HTML_STYLE}
</head>
<body>
<div class="sheet">
  <header class="masthead">
    <span class="vol">Vol. I · No. {int(d)}</span>
    <span class="issue">掘金日报 · THE DAILY DIGEST</span>
    <span>Edition of {requested_date}</span>
  </header>

  <div style="text-align: right; margin-bottom: var(--u3);">
    <a class="entry-cta" href="http://localhost:8765/" target="_blank">录入交易</a>
    <a class="entry-cta-inline" href="archive.html" style="margin-left: 8px;">→ 档案室</a>
  </div>

  <div class="dateline">
    <div class="dl-date">{int(m)}<span style="color:var(--rule)">·</span>{int(d)}<div class="yr">A.D. {y}</div></div>
    <div>
      <div class="dl-title">掘金<br>信号日报</div>
      <div class="dl-subtitle">基于 4 因子宽度回看的板块信号 · 配 4 进度桶<br>止盈止损分桶规则 · 第 VI 期{empty_mark}</div>
      {data_line}
    </div>
  </div>
"""]

    # ===== 重要告警（红色横幅，只在有红色告警时出现） =====
    if n_red > 0:
        html.append(f"""
  <div class="alert-banner">
    <div class="alert-icon">!</div>
    <div class="alert-text">
      <b>紧急离场信号</b>
      {n_red} 个持仓已突破止损线，请在今日收盘前完成清仓操作
    </div>
  </div>
""")

    # ===== 1. 摘要数字墙 =====
    html.append('  <div class="metrics">')
    html.append(f'    <div class="metric"><div class="metric-num">{n_today}</div><div class="metric-key">今日信号</div><div class="metric-note">首次建仓机会</div></div>')
    html.append(f'    <div class="metric alert"><div class="metric-num">{n_red}</div><div class="metric-key">需立即操作</div><div class="metric-note">已破止损</div></div>')
    html.append(f'    <div class="metric"><div class="metric-num">{n_positions}</div><div class="metric-key">当前持仓</div><div class="metric-note">4 桶规则监控中</div></div>')
    html.append(f'    <div class="metric up"><div class="metric-num">{n_repeat}</div><div class="metric-key">重复触发</div><div class="metric-note">优先建仓</div></div>')
    html.append('  </div>')

    # ===== 2. 今日操作 =====
    html.append('  <section class="section">')
    html.append('    <div class="sec-head">')
    html.append('      <span class="sec-num">§ I</span>')
    html.append('      <span class="sec-title">今日操作 · 建仓指令</span>')
    html.append('      <span class="sec-tag">Dispatch</span>')
    html.append('    </div>')
    if today_only.empty:
        html.append('    <div class="empty">今日暂无新信号 · 保持现有持仓与监控</div>')
    else:
        html.append('    <div class="dispatch">')
        for idx, (_, sig) in enumerate(today_only.iterrows(), 1):
            pos_pct = RULES["position"]["repeat_trigger"] if sig["priority"] == "REPEAT" else RULES["position"]["first_trigger"]
            tag = 'REPEAT' if sig["priority"] == "REPEAT" else 'FIRST'
            tag_label = '重复' if sig["priority"] == "REPEAT" else '首次'
            # 行业→ETF 匹配
            etf_r = match_industry_to_etfs(sig["industry"], top_n=2)
            etf_html = ""
            conf_pct = int(etf_r['confidence'] * 100)
            if etf_r["best"]:
                purity_badge = ""
                purity = etf_r["best"].get("purity", "")
                if purity == "纯":
                    purity_badge = '<span class="etf-purity pure">纯</span>'
                elif purity == "偏纯":
                    purity_badge = '<span class="etf-purity impure">偏纯</span>'
                elif purity == "不纯":
                    purity_badge = '<span class="etf-purity bad">不纯</span>'
                else:
                    purity_badge = '<span class="etf-purity">—</span>'
                # 主推
                b = etf_r["best"]
                etf_html = f"""
          <div class="etf-line main">
            <span class="etf-tag">建议买入</span>
            <span class="etf-name">{b['name']}</span>
            <span class="etf-code">{b['code']}</span>
            {purity_badge}
            <span class="etf-conf">覆盖度 {conf_pct}%</span>
          </div>"""
                # 备选
                for alt in etf_r["matches"][1:]:
                    ap = alt.get("purity", "")
                    ap_badge = ""
                    if ap == "纯":
                        ap_badge = '<span class="etf-purity pure">纯</span>'
                    elif ap == "偏纯":
                        ap_badge = '<span class="etf-purity impure">偏纯</span>'
                    etf_html += f"""
          <div class="etf-line alt">
            <span class="etf-tag light">备选</span>
            <span class="etf-name">{alt['name']}</span>
            <span class="etf-code">{alt['code']}</span>
            {ap_badge}
          </div>"""
            else:
                etf_html = f"""
          <div class="etf-line none">
            <span class="etf-tag light">⚠ 无 ETF</span>
            <span class="etf-name">{etf_r['note']}</span>
          </div>"""
            html.append(f"""
      <article class="dispatch-item {tag}">
        <div>
          <div class="dispatch-num">No. {idx:02d}</div>
        </div>
        <div>
          <div class="dispatch-name"><span class="dispatch-tag {tag}">{tag_label}</span>{sig["industry"]}</div>
          {etf_html}
        </div>
        <div class="dispatch-meta">
          建仓 <b>{pos_pct*100:.0f}%</b><br>
          宽度 <b>{sig["breadth_at_signal"]*100:.0f}%</b> · 量比 <b>{sig["vol_ratio_at_signal"]:.2f}</b>
        </div>
      </article>""")
        html.append('    </div>')
    html.append('  </section>')

    # ===== 3. 持仓监控 =====
    html.append('  <section class="section">')
    sec_subtitle = f"🔴 {n_red} 个需立即操作" if n_red > 0 else "按优先级排序 · 风险由上至下"
    html.append('    <div class="sec-head">')
    html.append('      <span class="sec-num">§ II</span>')
    html.append('      <span class="sec-title">持仓监控</span>')
    html.append(f'      <span class="sec-tag">{sec_subtitle}</span>')
    html.append('    </div>')

    if not monitoring:
        html.append('    <div class="empty">📭 当前无持仓</div>')
    else:
        # 按优先级排序
        priority_order = {"RED": 0, "YELLOW": 1, "GREEN": 2}
        sorted_monitoring = sorted(
            monitoring,
            key=lambda r: (priority_order.get(r.get("priority", "GREEN"), 2), -abs(r.get("return_pct", 0))),
        )
        html.append('    <div class="ledgers">')
        for r in sorted_monitoring:
            html.append(render_position_card(r))
        html.append('    </div>')
    html.append('  </section>')

    # ===== 4. 规则速查（4 桶小册子） =====
    html.append('  <section class="section">')
    html.append('    <div class="sec-head">')
    html.append('      <span class="sec-num">§ III</span>')
    html.append('      <span class="sec-title">规则速查 · 进度分桶</span>')
    html.append('      <span class="sec-tag">v6</span>')
    html.append('    </div>')
    html.append('    <div class="buckets">')
    buckets = [
        ("01", "极早期", "进度 < 10%",  "-18",  "+30"),
        ("02", "早期",   "10 ~ 30%",   "-15",  "+30"),
        ("03", "中期",   "30 ~ 50%",   "-12",  "+25"),
        ("04", "晚期",   "50% 以上",   "-8",   "+20"),
    ]
    for n, name, trig, stop, tp in buckets:
        html.append(f"""
      <div class="bucket">
        <div class="bucket-num">No. {n}</div>
        <div class="bucket-name">{name}</div>
        <div class="bucket-trigger">触发 · {trig}</div>
        <div class="bucket-stop">{stop}%</div>
        <div class="bucket-stop-label">— stop —</div>
        <div class="bucket-tp">{tp}%</div>
        <div class="bucket-tp-label">— take —</div>
      </div>""")
    html.append('    </div>')
    html.append('  </section>')

    # ===== Footer（编辑风收尾） =====
    html.append('  <footer class="colophon">')
    html.append('    <span>掘金信号 · 4 因子 + 4 进度桶 + 仓位 8%/12%</span>')
    html.append(f'    <span class="stamp">SET IN FRAUNCES &amp; NOTO SERIF</span>')
    html.append(f'    <span>生成于 {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</span>')
    html.append('  </footer>')

    html.append('</div>')
    html.append('</body></html>')
    return "\n".join(html)


def render_blocked_report(requested_date: str, signal_data_date: str, validation: dict,
                           today_signals: pd.DataFrame, monitoring: list) -> str:
    """数据不合格时生成阻断报告（不含可执行买卖建议）"""
    n_today = len(today_signals)
    n_positions = len(monitoring)
    y, m, d = requested_date.split("-")

    errors_html = "".join(f'<li>{e}</li>' for e in validation.get("errors", []))
    warnings_html = "".join(f'<li>{w}</li>' for w in validation.get("warnings", []))

    html = [f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>⚠ 数据阻断 · {requested_date} | Zhuxian Catch v0.6</title>
{HTML_STYLE}
</head>
<body>
<div class="sheet">
  <header class="masthead">
    <span class="vol">Vol. I · No. {int(d)}</span>
    <span class="issue">数据阻断 · DATA BLOCKED</span>
    <span>Edition of {requested_date}</span>
  </header>

  <div class="dateline">
    <div class="dl-date">{int(m)}<span style="color:var(--rule)">·</span>{int(d)}<div class="yr">A.D. {y}</div></div>
    <div>
      <div class="dl-title">数据<br>阻断报告</div>
      <div class="dl-subtitle">行情数据未就绪 · 本报告不提供任何买卖建议</div>
    </div>
  </div>

  <div class="alert-banner" style="background:var(--accent);border-color:var(--accent);">
    <div class="alert-icon" style="color:var(--ink-on-dark);">!</div>
    <div class="alert-text" style="color:var(--ink-on-dark);">
      <b>行情数据未就绪</b>
      本报告不提供任何买卖建议，请先完成数据更新。
    </div>
  </div>

  <section class="section">
    <div class="sec-head">
      <span class="sec-num">§ I</span>
      <span class="sec-title">数据状态</span>
    </div>
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
      <tr><td style="padding:6px 0;color:var(--ink-2);">请求日期</td><td>{requested_date}</td></tr>
      <tr><td style="padding:6px 0;color:var(--ink-2);">预期交易日</td><td>{validation.get('expected_trade_date', '不可用')}</td></tr>
      <tr><td style="padding:6px 0;color:var(--ink-2);">daily_hfq 最新</td><td>{validation.get('daily_hfq_date', '无数据')}（{validation.get('daily_hfq_count', 0)} 行）</td></tr>
      <tr><td style="padding:6px 0;color:var(--ink-2);">stock_daily_raw 最新</td><td>{validation.get('stock_daily_raw_date', '无数据')}（{validation.get('stock_daily_raw_count', 0)} 行）</td></tr>
      <tr><td style="padding:6px 0;color:var(--ink-2);">etf_daily 最新</td><td>{validation.get('etf_daily_date', '无数据')}（{validation.get('etf_daily_count', 0)} 行）</td></tr>
      <tr><td style="padding:6px 0;color:var(--ink-2);">信号数</td><td>{n_today}</td></tr>
      <tr><td style="padding:6px 0;color:var(--ink-2);">持仓数</td><td>{n_positions}</td></tr>
    </table>
  </section>

  <section class="section">
    <div class="sec-head">
      <span class="sec-num">§ II</span>
      <span class="sec-title">错误与警告</span>
    </div>
    <ul style="font-size:13px;">
      {errors_html}
      {warnings_html}
    </ul>
  </section>
</div>
</body></html>"""
    ]
    return "\n".join(html)


def main():
    if len(sys.argv) >= 2:
        requested_date = sys.argv[1]
    else:
        requested_date = datetime.now().strftime("%Y-%m-%d")

    # 数据校验
    from v0_6.core.market_data import validate_market_data, get_expected_trade_date

    expected = get_expected_trade_date(requested_date)
    if expected is None:
        print(f"\n❌ 交易日历不可用，无法判断预期交易日")
        return 1

    # 尝试刷新数据（不阻塞，仅当数据明显落后时）
    if expected:
        from v0_6.core.market_data import get_table_latest_date
        hfq_date = get_table_latest_date("daily_hfq")
        if hfq_date and expected.replace("-", "") > hfq_date:
            print(f"  ⚠ daily_hfq 最新 {hfq_date}，预期 {expected}，尝试刷新...")
            try:
                from v0_6.scripts.refresh_market_data import main as refresh_main
                refresh_main()
            except Exception as e:
                print(f"  ⚠ 刷新失败（不影响日报生成）: {e}")

    # 解析信号数据日期
    signal_data_date = get_signal_data_date(requested_date)
    if signal_data_date is None:
        print(f"\n❌ 未找到 {requested_date} 当日及以前的 daily_hfq 数据，无法生成信号日报。")
        return 1

    # 校验数据完整性
    validation = validate_market_data(requested_date)
    if not validation["ok"]:
        print(f"\n⚠️ 行情数据未就绪，生成数据阻断报告。")
        for err in validation["errors"]:
            print(f"  ❌ {err}")
        for warn in validation["warnings"]:
            print(f"  ⚠ {warn}")

        # 生成阻断报告仍可继续（信号和监控会显示在阻断报告中）
        today_signals = get_today_signals(signal_data_date)
        monitoring = monitor_all_positions_v6(requested_date)

        html = render_blocked_report(requested_date, signal_data_date, validation, today_signals, monitoring)
        out_path = DAILY_DIR / f"daily_v1_{requested_date}.html"
        out_path.write_text(html, encoding="utf-8")
        print(f"  ⚠ 阻断报告已生成: {out_path}")
        print(f"\n⚠️ 数据未就绪，已生成阻断报告（不含可执行买卖建议）。")
        return 0

    date_note = f" (数据截至 {signal_data_date})" if signal_data_date != requested_date else ""
    print(f"\n=== 掘金日报 V1.0 HTML | {requested_date}{date_note} ===\n")

    if signal_data_date != requested_date:
        print(f"  信号数据截至 {signal_data_date}（请求日期 {requested_date} 非交易日或数据落后）")

    today_signals = get_today_signals(signal_data_date)
    monitoring = monitor_all_positions_v6(requested_date)

    html = render_html(requested_date, signal_data_date, today_signals, monitoring)
    out_path = DAILY_DIR / f"daily_v1_{requested_date}.html"
    out_path.write_text(html, encoding="utf-8")
    print(f"  ✓ {out_path}")

    # 同时重建档案索引
    try:
        from scripts.build_archive import main as build_archive_main
        build_archive_main()
    except Exception as e:
        print(f"  ⚠️ 档案索引更新失败: {e}")

    print(f"\n🎉 完成！浏览器打开：file:///{out_path.as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
