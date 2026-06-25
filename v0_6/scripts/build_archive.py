"""
掘金日报存档索引

扫描 reports/daily_v1/ 下所有 HTML 文件，生成一份可桌面访问的索引页。
设计原则：
- "档案室目录柜" 风格——按月份分组，文件按日期倒序
- 一眼能看出哪几天有红色告警（用 HSL 提示色，但仍是档案室的色）
- 永远指向 file:// 本地路径，离线可访问

用法：
    python -m scripts.build_archive
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

V0_6_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(V0_6_ROOT))

from v0_6.core.config import DAILY_DIR

ARCHIVE_PATH = DAILY_DIR / "archive.html"


# ============== 解析日报提取摘要 ==============

def parse_daily(path: Path) -> dict:
    """从一个 daily_v1_YYYY-MM-DD.html 文件里抓出关键摘要数据"""
    html = path.read_text(encoding="utf-8")
    # 日期从文件名取
    m = re.search(r"daily_v1_(\d{4}-\d{2}-\d{2})\.html", path.name)
    date = m.group(1) if m else path.stem

    # 解析 4 个 metric（顺序：今日信号 / 需立即操作 / 当前持仓 / 重复触发）
    metric_nums = re.findall(r'<div class="metric-num">(\d+)</div>', html)
    n_signal, n_alert, n_pos, n_repeat = (metric_nums + ["0", "0", "0", "0"])[:4]

    # 检测告警横幅
    has_alert_banner = "紧急离场信号" in html

    # 检测持仓类型
    actions = re.findall(r'<article class="ledger (\w+)">', html)
    # 检测信号类型
    sig_types = re.findall(r'<article class="dispatch-item (\w+)">', html)

    return {
        "date": date,
        "filename": path.name,
        "path": path,
        "n_signal": int(n_signal),
        "n_alert": int(n_alert),
        "n_pos": int(n_pos),
        "n_repeat": int(n_repeat),
        "has_alert": has_alert_banner,
        "actions": actions,
        "sig_types": sig_types,
    }


# ============== 渲染 ==============

def render_archive(entries: list[dict]) -> str:
    """渲染档案索引页——"档案室目录柜"风格"""
    if not entries:
        body_empty = '<div class="empty">尚未生成任何日报</div>'
    else:
        # 按月份分组
        by_month: dict[str, list[dict]] = {}
        for e in entries:
            month = e["date"][:7]  # YYYY-MM
            by_month.setdefault(month, []).append(e)

        body_parts = []
        for month in sorted(by_month.keys(), reverse=True):
            month_entries = sorted(by_month[month], key=lambda x: x["date"], reverse=True)
            month_label = f"{month[:4]} 年 {int(month[5:])} 月"
            year = month[:4]
            month_num = int(month[5:])

            body_parts.append(f"""
  <div class="month">
    <div class="month-head">
      <span class="month-num">{month_num:02d}</span>
      <span class="month-label">{month_label}</span>
      <span class="month-count">{len(month_entries)} 期</span>
    </div>
    <div class="entries">""")

            for e in month_entries:
                # 提取日期 day
                day = int(e["date"][8:])
                # alert 状态决定外观
                state = "alert" if e["has_alert"] else ("quiet" if e["n_signal"] == 0 else "normal")
                # 摘要：信号数、告警数、持仓数
                cells = []
                if e["n_signal"]:
                    cells.append(f'<span class="cell sig">信号 <b>{e["n_signal"]}</b></span>')
                if e["n_repeat"]:
                    cells.append(f'<span class="cell rep">重复 <b>{e["n_repeat"]}</b></span>')
                if e["n_alert"]:
                    cells.append(f'<span class="cell red">告警 <b>{e["n_alert"]}</b></span>')
                if e["n_pos"]:
                    cells.append(f'<span class="cell pos">持仓 <b>{e["n_pos"]}</b></span>')
                cells_html = " ".join(cells) if cells else '<span class="cell quiet">— 无活动 —</span>'

                # 周几（仅算中英对照）
                weekday_cn = ["一", "二", "三", "四", "五", "六", "日"][
                    datetime.strptime(e["date"], "%Y-%m-%d").weekday()
                ]

                body_parts.append(f"""
      <a class="entry {state}" href="{e['filename']}">
        <div class="entry-day">
          <span class="day-num">{day:02d}</span>
          <span class="day-wd">周{weekday_cn}</span>
        </div>
        <div class="entry-body">
          <div class="entry-title">掘金日报 · {e['date']}{' <span class="empty-badge">空</span>' if e['n_signal'] == 0 and not e['has_alert'] else ''}</div>
          <div class="entry-cells">{cells_html}</div>
        </div>
        <div class="entry-arrow">→</div>
      </a>""")

            body_parts.append("""
    </div>
  </div>""")
        body_empty = "\n".join(body_parts)

    # 顶部统计
    total = len(entries)
    total_alerts = sum(1 for e in entries if e["has_alert"])
    total_signals = sum(e["n_signal"] for e in entries)
    total_repeats = sum(e["n_repeat"] for e in entries)
    last_date = max((e["date"] for e in entries), default="—")
    first_date = min((e["date"] for e in entries), default="—")
    span_days = "—"
    if entries:
        try:
            d1 = datetime.strptime(first_date, "%Y-%m-%d")
            d2 = datetime.strptime(last_date, "%Y-%m-%d")
            span_days = f"{(d2 - d1).days + 1} 天"
        except Exception:
            pass

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>掘金日报 · 档案室 | Zhuxian Catch v0.6</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,500;9..144,700;9..144,900&family=Noto+Serif+SC:wght@400;500;700;900&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
<style>
:root {{
  --paper:        oklch(97% 0.012 80);
  --paper-2:      oklch(93% 0.014 78);
  --ink:          oklch(22% 0.018 50);
  --ink-2:        oklch(42% 0.020 50);
  --ink-3:        oklch(60% 0.018 55);
  --rule:         oklch(85% 0.015 75);
  --rule-2:       oklch(90% 0.013 78);
  --accent:       oklch(45% 0.16 22);
  --accent-bg:    oklch(95% 0.04 25);
  --accent-2:     oklch(62% 0.13 70);
  --accent-2-bg:  oklch(95% 0.04 80);
  --warn:         oklch(65% 0.13 50);
  --warn-bg:      oklch(95% 0.05 55);
  --neutral-bg:   oklch(92% 0.012 80);
  --ink-on-dark:  oklch(96% 0.01 80);

  --font-display: "Fraunces", "Noto Serif SC", "Songti SC", "SimSun", Georgia, serif;
  --font-body:    "Noto Serif SC", "Songti SC", "SimSun", Georgia, serif;
  --font-mono:    "JetBrains Mono", "SF Mono", Consolas, monospace;

  --u: 8px; --u2: 16px; --u3: 24px; --u4: 32px; --u6: 48px; --u8: 64px; --u12: 96px;
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
html {{ background: var(--paper-2); }}
body {{
  font-family: var(--font-body);
  background: var(--paper);
  color: var(--ink);
  line-height: 1.6;
  font-size: 16px;
  -webkit-font-smoothing: antialiased;
  min-height: 100vh;
}}

.sheet {{
  max-width: 920px;
  margin: 0 auto;
  padding: var(--u6) var(--u4) var(--u8);
  background: var(--paper);
  position: relative;
}}
.sheet::before {{
  content: "";
  position: absolute;
  left: 32px;
  top: var(--u6);
  bottom: var(--u6);
  width: 1px;
  background: var(--accent);
  opacity: 0.35;
}}
@media (max-width: 720px) {{
  .sheet {{ padding: var(--u4) var(--u3) var(--u6); }}
  .sheet::before {{ left: 14px; }}
}}

/* 顶部刊头 */
.masthead {{
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
}}
.masthead .vol {{ font-style: italic; }}

/* 主标题 */
.dateline {{
  padding: var(--u6) 0 var(--u6);
  border-bottom: 1px solid var(--rule);
  margin-bottom: var(--u6);
  text-align: center;
}}
.dl-eyebrow {{
  font-family: var(--font-display);
  font-style: italic;
  font-size: 14px;
  color: var(--accent);
  letter-spacing: 0.05em;
  margin-bottom: var(--u2);
}}
.dl-title {{
  font-family: var(--font-display);
  font-weight: 900;
  font-size: clamp(40px, 8vw, 72px);
  line-height: 0.95;
  letter-spacing: -0.04em;
  color: var(--ink);
  font-variant-numeric: tabular-nums;
}}
.dl-title .amp {{
  color: var(--accent);
  font-style: italic;
  font-weight: 400;
}}
.dl-subtitle {{
  margin-top: var(--u3);
  font-family: var(--font-display);
  font-style: italic;
  font-size: 15px;
  color: var(--ink-2);
  max-width: 560px;
  margin-left: auto;
  margin-right: auto;
  line-height: 1.5;
}}

/* 概要数据行 */
.census {{
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 0;
  border-top: 1px solid var(--rule);
  border-bottom: 1px solid var(--rule);
  margin-bottom: var(--u8);
}}
@media (max-width: 600px) {{ .census {{ grid-template-columns: repeat(2, 1fr); }} }}
.cen {{
  padding: var(--u3) var(--u2) var(--u2);
  border-right: 1px solid var(--rule);
  text-align: center;
}}
.cen:last-child {{ border-right: none; }}
@media (max-width: 600px) {{
  .cen:nth-child(2) {{ border-right: none; }}
  .cen:nth-child(1), .cen:nth-child(2) {{ border-bottom: 1px solid var(--rule); }}
}}
.cen-num {{
  font-family: var(--font-display);
  font-weight: 900;
  font-size: clamp(32px, 5vw, 44px);
  line-height: 0.9;
  letter-spacing: -0.03em;
  font-variant-numeric: tabular-nums;
  color: var(--ink);
}}
.cen.alert .cen-num {{ color: var(--accent); }}
.cen-key {{
  margin-top: var(--u2);
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.2em;
  color: var(--ink-3);
  font-weight: 500;
  font-family: var(--font-display);
}}
.cen-note {{
  margin-top: 2px;
  font-size: 11px;
  color: var(--ink-2);
  font-style: italic;
}}

/* 章节 */
.section {{ margin-bottom: var(--u8); }}
.sec-head {{
  display: flex;
  align-items: baseline;
  gap: var(--u2);
  margin-bottom: var(--u4);
  padding-bottom: var(--u2);
  border-bottom: 1px solid var(--ink);
}}
.sec-num {{
  font-family: var(--font-display);
  font-weight: 400;
  font-style: italic;
  font-size: 14px;
  color: var(--accent);
  letter-spacing: 0.05em;
}}
.sec-title {{
  font-family: var(--font-display);
  font-weight: 700;
  font-size: 24px;
  letter-spacing: -0.01em;
  color: var(--ink);
  line-height: 1.1;
}}
.sec-tag {{
  margin-left: auto;
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--ink-3);
  text-transform: uppercase;
  letter-spacing: 0.15em;
}}

/* 月份 */
.month {{ margin-bottom: var(--u6); }}
.month-head {{
  display: flex;
  align-items: baseline;
  gap: var(--u3);
  padding: var(--u3) 0 var(--u2);
  border-bottom: 1px solid var(--ink);
  margin-bottom: var(--u2);
}}
.month-num {{
  font-family: var(--font-display);
  font-weight: 900;
  font-size: 36px;
  line-height: 1;
  color: var(--accent);
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.02em;
}}
.month-label {{
  font-family: var(--font-display);
  font-weight: 700;
  font-size: 18px;
  color: var(--ink);
}}
.month-count {{
  margin-left: auto;
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--ink-3);
  text-transform: uppercase;
  letter-spacing: 0.15em;
}}

/* 日报条目（档案卡） */
.entries {{ display: flex; flex-direction: column; }}
.entry {{
  display: grid;
  grid-template-columns: 80px 1fr 24px;
  gap: var(--u3);
  align-items: center;
  padding: var(--u3) 0;
  border-bottom: 1px dashed var(--rule);
  text-decoration: none;
  color: var(--ink);
  transition: background 120ms ease;
  position: relative;
}}
.entry::before {{
  content: "";
  position: absolute;
  left: -8px;
  top: 50%;
  transform: translateY(-50%);
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--rule-2);
}}
.entry.alert::before {{ background: var(--accent); box-shadow: 0 0 0 3px var(--accent-bg); }}
.entry.normal::before {{ background: var(--accent-2); }}
.entry.quiet::before {{ background: var(--ink-3); }}
.entry:hover {{ background: var(--neutral-bg); }}
.entry:hover .entry-arrow {{ color: var(--accent); transform: translateX(4px); }}

.entry-day {{
  text-align: right;
  font-family: var(--font-display);
}}
.day-num {{
  display: block;
  font-weight: 900;
  font-size: 32px;
  line-height: 0.9;
  letter-spacing: -0.02em;
  font-variant-numeric: tabular-nums;
  color: var(--ink);
}}
.entry.alert .day-num {{ color: var(--accent); }}
.entry.quiet .day-num {{ color: var(--ink-3); font-weight: 400; }}
.day-wd {{
  display: block;
  margin-top: 2px;
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--ink-3);
  text-transform: uppercase;
  letter-spacing: 0.1em;
}}

.entry-body {{ min-width: 0; }}
.entry-title {{
  font-family: var(--font-display);
  font-weight: 700;
  font-size: 18px;
  color: var(--ink);
  margin-bottom: 4px;
  letter-spacing: -0.01em;
}}
.entry-cells {{
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}}
.cell {{
  font-family: var(--font-mono);
  font-size: 10px;
  padding: 2px 6px;
  background: var(--neutral-bg);
  color: var(--ink-2);
  border-radius: 2px;
  letter-spacing: 0.05em;
}}
.cell b {{ color: var(--ink); font-weight: 700; }}
.cell.sig {{ background: var(--paper-2); color: var(--ink-2); }}
.cell.rep {{ background: var(--accent-2-bg); color: var(--accent-2); }}
.cell.rep b {{ color: var(--accent-2); }}
.cell.red {{ background: var(--accent); color: var(--ink-on-dark); }}
.cell.red b {{ color: var(--ink-on-dark); font-weight: 900; }}
.cell.pos {{ background: var(--paper-2); color: var(--ink-2); }}
.cell.quiet {{ background: transparent; color: var(--ink-3); font-style: italic; }}
.empty-badge {{
  display: inline-block;
  font-family: var(--font-mono);
  font-size: 10px;
  padding: 1px 6px;
  background: var(--ink-3);
  color: var(--ink-on-dark);
  letter-spacing: 0.1em;
  vertical-align: middle;
  margin-left: 6px;
}}

.entry-arrow {{
  font-family: var(--font-display);
  font-size: 20px;
  color: var(--ink-3);
  transition: color 120ms ease, transform 120ms ease;
}}

.empty {{
  padding: var(--u6) var(--u4);
  background: transparent;
  border: 1px dashed var(--rule);
  font-style: italic;
  color: var(--ink-3);
  text-align: center;
  font-size: 14px;
}}

/* Footer */
.colophon {{
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
  flex-wrap: wrap;
  gap: var(--u2);
}}
.colophon .stamp {{
  text-transform: uppercase;
  letter-spacing: 0.2em;
  font-style: normal;
  font-weight: 700;
}}

/* 图例 */
.legend {{
  display: flex;
  gap: var(--u3);
  padding: var(--u2) 0;
  margin-top: var(--u2);
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--ink-3);
  flex-wrap: wrap;
}}
.legend-item {{ display: inline-flex; align-items: center; gap: 6px; }}
.legend-dot {{ width: 8px; height: 8px; border-radius: 50%; }}

/* 录入按钮 */
.entry-cta {{
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
  cursor: pointer;
}}
.entry-cta:hover {{ background: var(--accent); color: var(--ink-on-dark); }}

/* 录入服务未启动提示 modal */
.modal {{
  position: fixed;
  inset: 0;
  background: oklch(20% 0.02 50 / 0.65);
  display: none;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}}
.modal.show {{ display: flex; }}
.modal-card {{
  background: var(--paper);
  max-width: 480px;
  width: 90%;
  padding: var(--u4);
  border: 2px solid var(--accent);
  font-family: var(--font-body);
}}
.modal-card h2 {{
  font-family: var(--font-display);
  font-weight: 700;
  font-size: 22px;
  color: var(--ink);
  margin-bottom: var(--u2);
  letter-spacing: -0.01em;
}}
.modal-card p {{
  font-size: 14px;
  line-height: 1.6;
  color: var(--ink-2);
  margin-bottom: var(--u2);
}}
.modal-card code {{
  font-family: var(--font-mono);
  background: var(--paper-2);
  padding: 2px 6px;
  border: 1px solid var(--rule);
  font-size: 13px;
  display: block;
  margin: var(--u) 0;
  word-break: break-all;
}}
.modal-card .actions {{
  display: flex;
  gap: var(--u2);
  margin-top: var(--u3);
  justify-content: flex-end;
}}
.modal-card button {{
  font-family: var(--font-display);
  font-weight: 700;
  font-size: 14px;
  padding: 8px 16px;
  border: 1px solid var(--ink);
  background: var(--paper);
  color: var(--ink);
  cursor: pointer;
  letter-spacing: 0.05em;
}}
.modal-card button.primary {{
  background: var(--ink);
  color: var(--ink-on-dark);
  border-color: var(--ink);
}}
.modal-card button:hover {{ background: var(--accent); color: var(--ink-on-dark); border-color: var(--accent); }}
</style>
</head>
<body>
<div class="sheet">
  <header class="masthead">
    <span class="vol">The Archive</span>
    <span>掘金日报 · 历史档案室</span>
    <span><a class="entry-cta" href="http://localhost:8765/" target="_blank" id="entry-cta">+ 录入交易</a></span>
  </header>

  <div class="dateline">
    <div class="dl-eyebrow">— DAILY DIGEST ARCHIVE —</div>
    <h1 class="dl-title">掘金 <span class="amp">&amp;</span> 档案</h1>
    <p class="dl-subtitle">所有历史日报的目录索引。点击任一日期进入当日完整报告。<br>本档案为本地静态文件，断网亦可访问。</p>
  </div>

  <div class="census">
    <div class="cen"><div class="cen-num">{total}</div><div class="cen-key">归档期数</div><div class="cen-note">{span_days}</div></div>
    <div class="cen"><div class="cen-num">{total_signals}</div><div class="cen-key">累计信号</div><div class="cen-note">首次建仓机会</div></div>
    <div class="cen"><div class="cen-num">{total_repeats}</div><div class="cen-key">重复触发</div><div class="cen-note">优先建仓</div></div>
    <div class="cen alert"><div class="cen-num">{total_alerts}</div><div class="cen-key">告警日报</div><div class="cen-note">有止损触发</div></div>
    <div class="cen"><div class="cen-num">{first_date[-5:] or "—"}</div><div class="cen-key">首期 → 末期</div><div class="cen-note">至 {last_date[-5:] or "—"}</div></div>
  </div>

  <div class="legend">
    <span class="legend-item"><span class="legend-dot" style="background: var(--accent);"></span> 告警日（持仓破止损）</span>
    <span class="legend-item"><span class="legend-dot" style="background: var(--accent-2);"></span> 正常（有信号）</span>
    <span class="legend-item"><span class="legend-dot" style="background: var(--ink-3);"></span> 静默日（无新信号）</span>
  </div>

  <section class="section" style="margin-top: var(--u4);">
    <div class="sec-head">
      <span class="sec-num">§ I</span>
      <span class="sec-title">档案 · 按月归档</span>
      <span class="sec-tag">By Month</span>
    </div>
    {body_empty}
  </section>

  <footer class="colophon">
    <span>本档案基于 reports/daily_v1/ 目录自动生成</span>
    <span class="stamp">SET IN FRAUNCES &amp; NOTO SERIF</span>
    <span>生成于 {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</span>
  </footer>
</div>

<!-- 服务未启动提示 -->
<div class="modal" id="service-modal">
  <div class="modal-card">
    <h2>⚠ 录入服务未启动</h2>
    <p>录入交易需要本地 Python HTTP 服务（端口 8765）。请双击启动：</p>
    <code>D:\zhuxian-catch-v0_6\start_trade_form.bat</code>
    <p>启动后浏览器会自动打开录入页面。再次回到此档案室点击按钮即可。</p>
    <div class="actions">
      <button onclick="document.getElementById('service-modal').classList.remove('show')">关闭</button>
      <button class="primary" onclick="retryOpen()">重试</button>
    </div>
  </div>
</div>

<script>
// 录入按钮：先检查服务是否在线
document.getElementById('entry-cta').addEventListener('click', async function(e) {{
  e.preventDefault();
  try {{
    const r = await fetch('http://localhost:8765/api/health', {{ mode: 'no-cors', cache: 'no-store' }});
    // no-cors 不读 response，只判断能不能连上
    window.open('http://localhost:8765/', '_blank');
  }} catch (err) {{
    document.getElementById('service-modal').classList.add('show');
  }}
}});

function retryOpen() {{
  document.getElementById('entry-cta').click();
}}
</script>
</body></html>
"""


def main():
    print("\n=== 掘金日报 · 档案室索引生成 ===\n")
    files = sorted(DAILY_DIR.glob("daily_v1_*.html"))
    entries = [parse_daily(p) for p in files]
    # 倒序
    entries.sort(key=lambda e: e["date"], reverse=True)

    html = render_archive(entries)
    ARCHIVE_PATH.write_text(html, encoding="utf-8")
    print(f"  ✓ 索引 {len(entries)} 期日报")
    print(f"  ✓ {ARCHIVE_PATH}")
    print(f"\n🎉 桌面访问：file:///{ARCHIVE_PATH.as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
