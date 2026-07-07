"""
v0.6 桌面交易录入表单 · 后端 HTTP 服务（v1.1 升级）

核心变化：
- "行业" → "标的"，输入 6/9 位代码或文字，后端 match_target 自动识别
- "价格" → "金额"（amount），价格/股数由后端从 daily_hfq 拉
- "进度" 完全后端算：拉 ETF 自身后复权价 60d_low → 进度 → 桶 → 止损价/止盈价
- 减仓/清仓：列出未平仓 BUY，自动预填金额 = 总股数 × 现价 × 1/3 或 100%

端口 8765
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

V0_6_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(V0_6_ROOT))

from v0_6.core import (
    add_trade,
    calc_progress_for_etf,
    calc_stop_price,
    calc_take_profit_price,
    close_trade_by_target,
    get_bucket,
    get_position_net,
    get_position_summary_all,
    init_schema,
    list_open_positions,
    load_sector_etf_map,
    match_target,
)
from v0_6.core.config import DB_PATH, STOCK_DATA_DB

PORT = 8765



def auto_calc_rules(target: str, target_type: str, trade_date: str) -> dict:
    """对 ETF/STOCK 自动算：当前价、60d_low、进度、桶、止损/止盈价

    全部使用 trade_date 当日及以前的数据（不使用该日期之后的未来价格）。
    行业（INDUSTRY）无标的价，退回让用户手动。

    如果数据陈旧，返回 market_data_ok=False，不生成自动建议。
    """
    if target_type == "INDUSTRY":
        return {}

    # 数据新鲜度校验
    from v0_6.core.market_data import get_expected_trade_date, validate_market_data
    expected = get_expected_trade_date(trade_date)
    if expected is None:
        return {"market_data_ok": False, "error": "stale_market_data",
                "message": "自动行情不可用，请按真实成交结果手动录入。"}

    # 检查该标的是否达到预期交易日
    pos_check = validate_market_data(trade_date, open_positions=[{
        "target": target, "target_type": target_type,
    }])
    if not pos_check["ok"]:
        actual_date = None
        for pe in pos_check.get("position_errors", []):
            if pe["target"] == target or target in pe["target"]:
                actual_date = pe.get("actual_date")
                break
        return {
            "market_data_ok": False,
            "error": "stale_market_data",
            "message": "自动行情不可用，请按真实成交结果手动录入。",
            "as_of": actual_date,
            "expected_date": expected,
        }

    con = sqlite3.connect(str(STOCK_DATA_DB))
    date_yyyymmdd = trade_date.replace("-", "")

    # 构造候选代码
    candidates = [target]
    if "." not in target:
        if target.startswith(("5", "1")):
            candidates.insert(0, target + ".SH")
            candidates.append(target + ".SZ")
    else:
        candidates = [target]

    # 根据 target_type 选择表
    if target_type == "ETF":
        table = "etf_daily"
    elif target_type == "STOCK":
        table = "stock_daily_raw"
    else:
        con.close()
        return {}

    rows = None
    for c in candidates:
        rows = con.execute(
            f"SELECT trade_date, close FROM {table} WHERE ts_code = ? AND trade_date <= ? ORDER BY trade_date DESC LIMIT 60",
            (c, date_yyyymmdd),
        ).fetchall()
        if rows:
            break
    con.close()

    if not rows:
        return {}

    current_price = rows[0][1]
    as_of = rows[0][0]

    # 数据不足5条时返回日期受限的最新价和价格日期，不计算进度桶
    if len(rows) < 5:
        return {"latest_price": current_price, "as_of": as_of}

    closes = [r[1] for r in rows]
    # current_price 已在上方赋值
    low_60d = min(closes)
    progress = (current_price - low_60d) / low_60d if low_60d > 0 else 0
    bucket = get_bucket(progress)
    # 止损止盈价基于当前现价（仅供参考，录入时会基于用户买入价重算）
    stop_price = calc_stop_price(current_price, progress)
    tp_price = calc_take_profit_price(current_price, progress)
    return {
        "latest_price": current_price,
        "low_60d": low_60d,
        "progress": progress,
        "bucket": bucket["name"],
        "stop_pct": bucket["stop"],
        "take_profit_pct": bucket["take_profit"],
        "stop_price": stop_price,
        "take_profit_price": tp_price,
        "as_of": rows[0][0],
    }


# ============== HTML 表单 ==============

HTML_FORM = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>实盘交易录入 · 掘金信号 v1.1</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,500;9..144,700;9..144,900&family=Noto+Serif+SC:wght@400;500;700;900&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
<style>
:root {
  --paper:        oklch(97% 0.012 80);
  --paper-2:      oklch(93% 0.014 78);
  --ink:          oklch(22% 0.018 50);
  --ink-2:        oklch(42% 0.020 50);
  --ink-3:        oklch(60% 0.018 55);
  --rule:         oklch(85% 0.015 75);
  --accent:       oklch(45% 0.16 22);
  --accent-bg:    oklch(95% 0.04 25);
  --accent-2:     oklch(62% 0.13 70);
  --accent-2-bg:  oklch(95% 0.04 80);
  --warn:         oklch(65% 0.13 50);
  --warn-bg:      oklch(95% 0.05 55);
  --neutral-bg:   oklch(92% 0.012 80);
  --ink-on-dark:  oklch(96% 0.01 80);
  --ok:           oklch(50% 0.12 145);
  --ok-bg:        oklch(95% 0.04 145);

  --font-display: "Fraunces", "Noto Serif SC", "Songti SC", "SimSun", Georgia, serif;
  --font-body:    "Noto Serif SC", "Songti SC", "SimSun", Georgia, serif;
  --font-mono:    "JetBrains Mono", "SF Mono", Consolas, monospace;

  --u: 8px; --u2: 16px; --u3: 24px; --u4: 32px; --u6: 48px;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
html { background: var(--paper-2); }
body {
  font-family: var(--font-body);
  background: var(--paper);
  color: var(--ink);
  line-height: 1.6;
  font-size: 16px;
  -webkit-font-smoothing: antialiased;
  min-height: 100vh;
  padding: var(--u4);
}

.sheet {
  max-width: 720px;
  margin: 0 auto;
  background: var(--paper);
  position: relative;
}
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
.dl-title {
  font-family: var(--font-display);
  font-weight: 900;
  font-size: clamp(32px, 6vw, 48px);
  line-height: 0.95;
  letter-spacing: -0.03em;
  margin: var(--u3) 0 var(--u2);
}
.dl-title .amp { color: var(--accent); font-style: italic; font-weight: 400; }
.dl-sub {
  font-family: var(--font-display);
  font-style: italic;
  font-size: 13px;
  color: var(--ink-2);
  margin-bottom: var(--u6);
  padding-bottom: var(--u3);
  border-bottom: 1px solid var(--rule);
}

/* Action 切换 */
.actions {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 0;
  border: 1px solid var(--ink);
  margin-bottom: var(--u4);
}
.action {
  padding: var(--u2) var(--u2);
  text-align: center;
  cursor: pointer;
  border-right: 1px solid var(--ink);
  background: var(--paper);
  font-family: var(--font-display);
  font-weight: 700;
  font-size: 15px;
  color: var(--ink-2);
  letter-spacing: 0.03em;
  transition: background 120ms ease, color 120ms ease;
  user-select: none;
}
.action:last-child { border-right: none; }
.action:hover { background: var(--neutral-bg); }
.action.active { background: var(--ink); color: var(--ink-on-dark); }
.action[data-action="SELL"].active   { background: var(--accent); }
.action[data-action="REDUCE"].active { background: var(--warn); }
.action[data-action="ADD"].active    { background: var(--accent-2); }

/* 表单 */
.form-section {
  margin-bottom: var(--u4);
  padding-bottom: var(--u3);
  border-bottom: 1px solid var(--rule);
}
.sec-num {
  font-family: var(--font-display);
  font-weight: 400;
  font-style: italic;
  font-size: 13px;
  color: var(--accent);
  letter-spacing: 0.05em;
  margin-bottom: var(--u);
  display: block;
}
.sec-title {
  font-family: var(--font-display);
  font-weight: 700;
  font-size: 18px;
  color: var(--ink);
  margin-bottom: var(--u3);
}

.field {
  display: grid;
  grid-template-columns: 80px 1fr 90px;
  gap: var(--u2);
  align-items: center;
  padding: var(--u2) 0;
}
.field label {
  font-family: var(--font-display);
  font-weight: 500;
  font-size: 13px;
  color: var(--ink-2);
  text-align: right;
}
.field input, .field select, .field textarea {
  font-family: var(--font-mono);
  font-size: 14px;
  padding: 6px 10px;
  border: 1px solid var(--rule);
  background: var(--paper);
  color: var(--ink);
  border-radius: 0;
  outline: none;
  width: 100%;
}
.field input:focus, .field select:focus, .field textarea:focus {
  border-color: var(--ink);
  background: var(--paper-2);
}
.field .hint {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--ink-3);
  text-align: right;
}

/* 标的匹配结果卡 */
.target-card {
  margin-top: var(--u2);
  padding: var(--u3);
  background: var(--ok-bg);
  border-left: 3px solid var(--ok);
  display: none;
}
.target-card.show { display: block; }
.target-card.fail { background: var(--accent-bg); border-left-color: var(--accent); }
.target-card .row {
  display: flex;
  justify-content: space-between;
  font-family: var(--font-mono);
  font-size: 13px;
  padding: 2px 0;
}
.target-card .row b { color: var(--ink); font-weight: 700; }
.target-card .title {
  font-family: var(--font-display);
  font-weight: 700;
  font-size: 18px;
  color: var(--ink);
  margin-bottom: var(--u2);
  letter-spacing: -0.01em;
}
.target-card .badge {
  display: inline-block;
  font-family: var(--font-display);
  font-style: italic;
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.15em;
  padding: 2px 8px;
  background: var(--ink);
  color: var(--ink-on-dark);
  margin-left: var(--u);
  vertical-align: middle;
}

/* 计算预览 */
.preview {
  margin-top: var(--u2);
  padding: var(--u3);
  background: var(--neutral-bg);
  border-left: 3px solid var(--accent-2);
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.7;
  display: none;
}
.preview.show { display: block; }
.preview .row { display: flex; justify-content: space-between; }
.preview .row b { color: var(--ink); font-weight: 700; }
.preview .row.red b { color: var(--accent); }
.preview .row.gold b { color: var(--accent-2); }
.preview .head {
  font-family: var(--font-display);
  font-style: italic;
  font-size: 11px;
  color: var(--ink-3);
  text-transform: uppercase;
  letter-spacing: 0.15em;
  border-bottom: 1px dashed var(--rule);
  padding-bottom: 4px;
  margin-bottom: 6px;
}

/* 持仓列表（REDUCE/SELL） */
.open-list {
  margin-top: var(--u2);
  font-family: var(--font-mono);
  font-size: 12px;
  background: var(--paper-2);
  border: 1px dashed var(--rule);
  max-height: 240px;
  overflow-y: auto;
  display: none;
}
.open-list.show { display: block; }
.open-list-item {
  padding: 8px 10px;
  cursor: pointer;
  display: grid;
  grid-template-columns: 40px 1fr 70px 70px;
  gap: 6px;
  border-bottom: 1px dashed var(--rule);
  align-items: center;
}
.open-list-item:hover { background: var(--neutral-bg); }
.open-list-item .oid { color: var(--accent); font-weight: 700; }
.open-list-item .name { color: var(--ink); }
.open-list-item .name b { font-weight: 700; }
.open-list-item .name i { color: var(--ink-3); font-style: normal; font-size: 11px; }
.open-list-item .meta { color: var(--ink-2); text-align: right; font-size: 11px; }
.open-list-item .meta b { color: var(--ink); font-weight: 700; }
.empty-list {
  padding: var(--u3);
  text-align: center;
  color: var(--ink-3);
  font-style: italic;
  font-size: 13px;
}

/* 提交 */
.submit {
  display: block;
  width: 100%;
  padding: var(--u3);
  background: var(--ink);
  color: var(--ink-on-dark);
  border: none;
  font-family: var(--font-display);
  font-weight: 700;
  font-size: 18px;
  letter-spacing: 0.05em;
  cursor: pointer;
  margin-top: var(--u3);
  transition: background 120ms ease;
}
.submit:hover { background: var(--accent); }
.submit:disabled { background: var(--ink-3); cursor: not-allowed; }

/* Toast */
.toast {
  position: fixed;
  bottom: var(--u4);
  right: var(--u4);
  padding: var(--u3) var(--u4);
  background: var(--ink);
  color: var(--ink-on-dark);
  font-family: var(--font-display);
  font-weight: 500;
  font-size: 14px;
  box-shadow: 0 4px 12px rgba(0,0,0,0.15);
  transform: translateX(150%);
  transition: transform 200ms ease;
  max-width: 400px;
  z-index: 1000;
}
.toast.show { transform: translateX(0); }
.toast.ok   { background: var(--ok); }
.toast.err  { background: var(--accent); }
.toast.warn { background: var(--warn); }
.toast b { display: block; font-weight: 900; font-size: 16px; margin-bottom: 2px; }

.colophon {
  margin-top: var(--u6);
  padding-top: var(--u3);
  border-top: 1px solid var(--rule);
  display: flex;
  justify-content: space-between;
  font-family: var(--font-display);
  font-style: italic;
  font-size: 11px;
  color: var(--ink-3);
}
</style>
</head>
<body>
<div class="sheet">
  <header class="masthead">
    <span>Trade Entry · v1.1</span>
    <span>实盘交易录入</span>
    <span id="today-date">—</span>
  </header>

  <h1 class="dl-title">录入<span class="amp"> / </span>交易</h1>
  <div class="dl-sub">v1.1 · 标的代码自动匹配 · 进度/桶自动算 · 止损价基于你填的买入价 · 来源行业记录</div>

  <div class="actions" id="actions">
    <div class="action active" data-action="BUY">首次建仓</div>
    <div class="action" data-action="ADD">加仓</div>
    <div class="action" data-action="REDUCE">减仓 1/3</div>
    <div class="action" data-action="SELL">清仓离场</div>
  </div>

  <form id="trade-form">
    <input type="hidden" name="action" id="f-action" value="BUY">
    <input type="hidden" name="target_type" id="f-target-type" value="ETF">
    <input type="hidden" name="target_name" id="f-target-name" value="">
    <input type="hidden" name="target" id="f-target" value="">

    <div class="form-section">
      <span class="sec-num">§ I</span>
      <div class="sec-title">标的识别</div>
      <div class="field">
        <label>标的代码</label>
        <input type="text" name="target_input" id="f-target-input" placeholder="6位数字 / 9位.SH.SZ / 名称" autocomplete="off" required>
        <span class="hint">必填</span>
      </div>
      <div class="target-card" id="target-card">
        <div class="title"><span id="tc-name">—</span><span class="badge" id="tc-type">—</span></div>
        <div class="row"><span>代码</span><b id="tc-code">—</b></div>
        <div class="row"><span>板块</span><b id="tc-sector">—</b></div>
        <div class="row"><span>细分</span><b id="tc-sub">—</b></div>
        <div class="row" id="tc-price-row" style="display:none;"><span>最新价</span><b id="tc-price">—</b></div>
      </div>
    </div>

    <div class="form-section">
      <span class="sec-num">§ II</span>
      <div class="sec-title">交易明细</div>
      <div class="field">
        <label>买入价</label>
        <input type="number" name="price" id="f-price" step="0.0001" min="0.0001" placeholder="你实际买入的价格" required>
        <span class="hint">元/份</span>
      </div>
      <div class="field">
        <label>金额</label>
        <input type="number" name="amount" id="f-amount" step="100" min="100" placeholder="1000 = 1000元" required>
        <span class="hint">元</span>
      </div>
      <div class="field">
        <label>日期</label>
        <input type="date" name="date" id="f-date" required>
        <span class="hint">交易日</span>
      </div>
      <div class="field" style="grid-template-columns: 80px 1fr;">
        <label>备注</label>
        <textarea name="notes" id="f-notes" rows="1" placeholder="如：4 因子首次触发 / 突破前高 / 减仓兑现"></textarea>
      </div>
    </div>

    <!-- § IIm: 来源信号（BUY/ADD 专用） -->
    <div class="form-section" id="source-section">
      <span class="sec-num">§ IIm</span>
      <div class="sec-title">来源信号</div>
      <div class="field">
        <label>来源行业</label>
        <input type="text" name="source_industry" id="f-source-industry" placeholder="如：证券 / 生物制药">
        <span class="hint">不填可保存，但主线/退潮分析不可用</span>
      </div>
      <div class="field">
        <label>信号日期</label>
        <input type="date" name="source_signal_date" id="f-source-signal-date">
        <span class="hint">触发信号的交易日</span>
      </div>
      <div class="field">
        <label>信号类型</label>
        <select name="source_signal_type" id="f-source-signal-type">
          <option value="STABILIZING_B" selected>STABILIZING_B</option>
        </select>
        <span class="hint">暂只支持四因子正式信号</span>
      </div>
      <div class="field">
        <label>优先级</label>
        <select name="source_signal_priority" id="f-source-signal-priority">
          <option value="FIRST">FIRST (试仓 8%)</option>
          <option value="REPEAT">REPEAT (建仓 12%)</option>
        </select>
        <span class="hint">FIRST = 首次触发，REPEAT = 再度触发</span>
      </div>
    </div>

    <!-- 预览：系统自动算 -->
    <div class="form-section">
      <span class="sec-num">§ III</span>
      <div class="sec-title">系统自动计算</div>
      <div class="preview" id="preview">
        <div class="head">— 实时拉取 etf_daily 后复权价 —</div>
        <div class="row"><span>现价</span><b id="pv-price">—</b></div>
        <div class="row"><span>60 日最低</span><b id="pv-low">—</b></div>
        <div class="row"><span>进度</span><b id="pv-progress">—</b></div>
        <div class="row"><span>进度桶</span><b id="pv-bucket">—</b></div>
        <div class="row red"><span>止损价</span><b id="pv-stop">—</b></div>
        <div class="row gold"><span>止盈价</span><b id="pv-tp">—</b></div>
        <div class="row"><span>建议仓位</span><b id="pv-pos">—</b></div>
      </div>
    </div>

    <!-- REDUCE/SELL 选持仓 -->
    <div class="form-section" id="parent-section" style="display:none;">
      <span class="sec-num">§ III</span>
      <div class="sec-title">关联原持仓</div>
      <div class="field">
        <label>trade_id</label>
        <input type="number" name="parent_trade_id" id="f-parent" placeholder="点选下方自动填">
        <span class="hint">必填</span>
      </div>
      <div class="open-list" id="open-list">
        <div class="empty-list">加载中…</div>
      </div>
    </div>

    <button type="submit" class="submit" id="submit-btn">提交录入</button>
  </form>

  <footer class="colophon">
    <span>掘金信号 v1.1 · 本地服务端口 8765</span>
    <span>数据写入 SQLite live_trades</span>
  </footer>
</div>

<div class="toast" id="toast"></div>

<script>
const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);

let currentAction = 'BUY';
let targetInfo = null;  // 后端返回的标的匹配结果
let openPositions = [];

// === 1. Action 切换 ===
$$('.action').forEach(el => {
  el.addEventListener('click', () => {
    $$('.action').forEach(x => x.classList.remove('active'));
    el.classList.add('active');
    currentAction = el.dataset.action;
    $('#f-action').value = currentAction;
    toggleSections();
    if (currentAction === 'REDUCE' || currentAction === 'SELL') {
      loadOpenPositions();
    }
  });
});

function toggleSections() {
  const showParent = (currentAction === 'REDUCE' || currentAction === 'SELL');
  $('#parent-section').style.display = showParent ? '' : 'none';
  // v1.1: 来源信号仅 BUY/ADD 可见
  const showSource = (currentAction === 'BUY' || currentAction === 'ADD');
  const srcSec = $('#source-section');
  if (srcSec) srcSec.style.display = showSource ? '' : 'none';
}

// === 2. 默认日期 = 今天 ===
const today = new Date().toISOString().slice(0, 10);
$('#f-date').value = today;
$('#today-date').textContent = today;

// === 3. 标的匹配 + 自动算 ===
let matchTimer = null;
$('#f-target-input').addEventListener('input', () => {
  clearTimeout(matchTimer);
  matchTimer = setTimeout(lookupTarget, 250);
});

// 买入价变化时重算止损/止盈
$('#f-price').addEventListener('input', recalcPreview);

function recalcPreview() {
  if (!targetInfo || targetInfo.progress === undefined) return;
  const price = parseFloat($('#f-price').value);
  if (!price || price <= 0) return;
  // 桶规则（来自 lookup）
  const stopPct = targetInfo.stop_pct;
  const tpPct = targetInfo.take_profit_pct;
  $('#pv-stop').textContent = '¥' + (price * (1 + stopPct)).toFixed(4) + '  (' + (stopPct * 100).toFixed(0) + '%)';
  $('#pv-tp').textContent = '¥' + (price * (1 + tpPct)).toFixed(4) + '  (' + (tpPct * 100).toFixed(0) + '%)';
}

async function lookupTarget() {
  const inp = $('#f-target-input').value.trim();
  const card = $('#target-card');
  const preview = $('#preview');

  if (!inp) {
    card.classList.remove('show', 'fail');
    preview.classList.remove('show');
    return;
  }

  try {
    const res = await fetch('/api/lookup?input=' + encodeURIComponent(inp));
    const data = await res.json();
    if (data.error) {
      card.classList.add('show', 'fail');
      card.classList.remove('show');
      card.classList.add('show', 'fail');
      $('#tc-name').textContent = '⚠️ ' + data.error;
      $('#tc-code').textContent = inp;
      $('#tc-type').textContent = '?';
      $('#tc-sector').textContent = '—';
      $('#tc-sub').textContent = '—';
      $('#tc-price-row').style.display = 'none';
      preview.classList.remove('show');
      targetInfo = null;
      $('#f-target').value = '';
      $('#f-target-type').value = 'INDUSTRY';
      $('#f-target-name').value = '';
      return;
    }

    targetInfo = data;
    card.classList.add('show');
    card.classList.remove('fail');
    $('#tc-name').textContent = data.target_name || data.target;
    $('#tc-code').textContent = data.target;
    $('#tc-type').textContent = data.target_type;
    $('#tc-sector').textContent = data.sector || data.industry || '—';
    $('#tc-sub').textContent = data.sub || '—';

    if (data.latest_price) {
      $('#tc-price-row').style.display = '';
      $('#tc-price').textContent = '¥' + data.latest_price.toFixed(4) + '  (' + data.as_of + ')';
    } else {
      $('#tc-price-row').style.display = 'none';
    }

    $('#f-target').value = data.target;
    $('#f-target-type').value = data.target_type;
    $('#f-target-name').value = data.target_name || data.target;

    // 实时预览：进度/桶参考现价算，止损止盈价占位
    if (data.progress !== undefined) {
      preview.classList.add('show');
      $('#pv-price').textContent = '¥' + data.latest_price.toFixed(4);
      $('#pv-low').textContent = '¥' + data.low_60d.toFixed(4);
      $('#pv-progress').textContent = (data.progress * 100).toFixed(2) + '%';
      $('#pv-bucket').textContent = data.bucket + '  (停 ' + (data.stop_pct*100).toFixed(0) + '% / 盈 ' + (data.take_profit_pct*100).toFixed(0) + '%)';
      // 止损止盈占位：等用户填买入价
      const buyPrice = parseFloat($('#f-price').value);
      if (buyPrice > 0) {
        recalcPreview();
      } else {
        $('#pv-stop').textContent = '填买入价后算';
        $('#pv-tp').textContent = '填买入价后算';
      }
      $('#pv-pos').textContent = '首次 8% / 重复 12%';
    } else {
      preview.classList.remove('show');
    }
  } catch (e) {
    console.error(e);
  }
}

// === 4. 加载未平仓持仓 ===
async function loadOpenPositions() {
  try {
    const url = (currentAction === 'BUY' || currentAction === 'ADD') ? '/api/open' : '/api/open_net';
    const res = await fetch(url);
    openPositions = await res.json();
    const list = $('#open-list');
    if (!openPositions.length) {
      list.innerHTML = '<div class="empty-list">📭 当前无未平仓持仓</div>';
      list.classList.add('show');
      return;
    }
    list.innerHTML = openPositions.map(p => {
      const isAgg = (currentAction === 'REDUCE' || currentAction === 'SELL');
      const tradeId = isAgg ? (p.parent_trade_id || '—') : p.trade_id;
      const displayShares = isAgg ? (p.total_shares || 0) : (p.shares || 0);
      const displayPrice = isAgg ? (p.avg_cost || 0) : (p.price || 0);
      const displayAmount = isAgg ? (displayShares * displayPrice) : (p.amount || 0);
      return `
      <div class="open-list-item" data-tid="${tradeId}">
        <span class="oid">#${String(tradeId).padStart(2, '0')}</span>
        <span class="name"><b>${p.target_name || p.target}</b><br><i>${p.target} · ${p.target_type} · ${p.first_buy || p.trade_date || ''}</i></span>
        <span class="meta">${displayShares.toFixed(0)}股<br>¥${displayPrice.toFixed(3)}</span>
        <span class="meta"><b>${p.progress_bucket || '—'}</b><br>¥${displayAmount.toFixed(0)}</span>
      </div>
    `}).join('');
    list.classList.add('show');

    $$('.open-list-item').forEach(item => {
      item.addEventListener('click', async () => {
        const tid = item.dataset.tid;
        $('#f-parent').value = tid;
        const pos = openPositions.find(p => String(p.trade_id || p.parent_trade_id) === tid);
        if (pos) {
          // 预填标的
          $('#f-target-input').value = pos.target;
          await lookupTarget();
          // 用最新价和净股数算建议金额
          if (targetInfo && targetInfo.latest_price) {
            const latestPrice = targetInfo.latest_price;
            $('#f-price').value = latestPrice.toFixed(4);
            const ratio = (currentAction === 'REDUCE') ? 1/3 : 1;
            const totalShares = pos.total_shares || pos.shares || 0;
            const suggestedShares = totalShares * ratio;
            const suggestedAmount = suggestedShares * latestPrice;
            $('#f-amount').value = suggestedAmount.toFixed(2);
          } else {
            // 无最新价时不自动填入
            $('#f-price').value = '';
            $('#f-amount').value = '';
          }
        }
      });
    });
  } catch (e) {
    console.error('loadOpenPositions failed', e);
  }
}

// === 5. 提交 ===
$('#trade-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const btn = $('#submit-btn');
  btn.disabled = true;
  btn.textContent = '提交中…';

  const form = new FormData(e.target);
  const payload = {};
  form.forEach((v, k) => { if (v) payload[k] = v; });

  if (!targetInfo && currentAction !== 'REDUCE' && currentAction !== 'SELL') {
    showToast('err', '\u274c 标的未识别', '请先输入有效代码或名称');
    btn.disabled = false; btn.textContent = '提交录入';
    return;
  }

  // v1.1: BUY 来源行业检查
  if (currentAction === 'BUY' && !payload.source_industry) {
    showToast('warn', '\u26a0\ufe0f 未填写来源行业',
      '持仓可以保存，但主线结构、相对强度和退潮监控将不可用。建议通过人工补录工具稍后填写。');
    // 不阻断，只警告
  }

  try {
    const res = await fetch('/api/trade', {
      method: 'POST',
      headers: {'Content-Type': 'application/json; charset=utf-8'},
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (data.ok) {
      let msg = `trade_id = ${data.trade_id} \u00b7 ${data.target_name || data.target} ${payload.action}`;
      if (data.warning) {
        msg += ' \u00b7 ' + data.warning;
        showToast('warn', '\u2705 录入成功(\u6709警告)', msg);
      } else {
        showToast('ok', '\u2705 录入成功', msg);
      }
      e.target.reset();
      $('#f-date').value = today;
      $('#target-card').classList.remove('show');
      $('#preview').classList.remove('show');
      targetInfo = null;
      if (currentAction === 'REDUCE' || currentAction === 'SELL') {
        loadOpenPositions();
      }
    } else {
      showToast('err', '\u274c 录入失败', data.error || '未知错误');
    }
  } catch (err) {
    showToast('err', '\u274c 网络错误', err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = '提交录入';
  }
});

// === 6. Toast ===
function showToast(type, title, msg) {
  const t = $('#toast');
  t.className = 'toast show ' + type;
  t.innerHTML = `<b>${title}</b>${msg}`;
  setTimeout(() => t.classList.remove('show'), 5000);
}
</script>
</body>
</html>
"""


# ============== HTTP Handler ==============

class TradeFormHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {fmt % args}", file=sys.stderr)

    def _send(self, code: int, content_type: str, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index.html"):
            self._send(200, "text/html; charset=utf-8", HTML_FORM.encode("utf-8"))
            return
        if self.path == "/api/health":
            self._send(200, "application/json", b'{"ok":true}')
            return
        if self.path == "/api/open":
            try:
                positions = list_open_positions()
                self._send(200, "application/json", json.dumps(positions, ensure_ascii=False).encode("utf-8"))
            except Exception as e:
                self._send(500, "application/json", json.dumps({"error": str(e)}).encode("utf-8"))
            return
        if self.path == "/api/open_net":
            try:
                from v0_6.core import get_position_summary_all
                summaries = get_position_summary_all()
                # 补查每标的的 parent_trade_id（最早一笔未关 BUY/ADD）
                con = sqlite3.connect(str(DB_PATH))
                for s in summaries:
                    row = con.execute(
                        "SELECT trade_id FROM live_trades "
                        "WHERE target = ? AND target_type = ? AND action IN ('BUY','ADD') AND closed = 0 "
                        "ORDER BY trade_id ASC LIMIT 1",
                        (s["target"], s["target_type"]),
                    ).fetchone()
                    s["parent_trade_id"] = row[0] if row else None
                con.close()
                self._send(200, "application/json", json.dumps(summaries, ensure_ascii=False).encode("utf-8"))
            except Exception as e:
                self._send(500, "application/json", json.dumps({"error": str(e)}).encode("utf-8"))
            return
        if self.path.startswith("/api/lookup"):
            # 解析 query
            qs = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
            inp = qs.get("input", [""])[0]
            try:
                m = match_target(inp)
                if "error" in m:
                    self._send(200, "application/json", json.dumps({"error": m["error"]}, ensure_ascii=False).encode("utf-8"))
                    return
                # 自动算进度
                if m["target_type"] in ("ETF", "STOCK"):
                    rules = auto_calc_rules(m["target"], m["target_type"], datetime.now().strftime("%Y-%m-%d"))
                    m.update(rules)
                self._send(200, "application/json", json.dumps(m, ensure_ascii=False).encode("utf-8"))
            except Exception as e:
                self._send(500, "application/json", json.dumps({"error": str(e)}).encode("utf-8"))
            return

    def do_POST(self):
        if self.path != "/api/trade":
            self._send(404, "text/plain", b"not found")
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length).decode("utf-8")
            data = json.loads(raw)

            action = data.get("action", "BUY").upper()
            target = (data.get("target") or "").strip()
            target_type = data.get("target_type", "INDUSTRY")
            target_name = data.get("target_name") or target
            amount = float(data.get("amount", 0))
            price = float(data.get("price", 0))  # 用户的买入价（必填）
            date = data.get("date") or datetime.now().strftime("%Y-%m-%d")
            notes = data.get("notes")
            parent_trade_id = data.get("parent_trade_id")
            if isinstance(parent_trade_id, str) and parent_trade_id.isdigit():
                parent_trade_id = int(parent_trade_id)

            if not target or amount <= 0 or price <= 0:
                raise ValueError("标的/金额/买入价 不能为空或为零")

            init_schema()

            if action in ("BUY", "ADD"):
                # ── v1.1: 来源行业（仅 BUY 需要，ADD 自动继承） ──
                source_industry = data.get("source_industry", "").strip() or None
                source_signal_date = data.get("source_signal_date", "").strip() or None
                source_signal_priority = data.get("source_signal_priority", "").strip() or None
                source_signal_type = data.get("source_signal_type", "").strip() or None

                # ADD 时自动继承当前持仓的来源行业
                if action == "ADD" and not source_industry:
                    con_chk = sqlite3.connect(str(DB_PATH))
                    si_row = con_chk.execute(
                        "SELECT source_industry FROM live_trades WHERE target=? AND action='BUY' AND closed=0 ORDER BY trade_id ASC LIMIT 1",
                        (target,),
                    ).fetchone()
                    con_chk.close()
                    if si_row and si_row[0]:
                        source_industry = si_row[0]

                # 1) 拉取进度/桶（基于现价，仅作参考）
                rules = auto_calc_rules(target, target_type, date) if target_type != "INDUSTRY" else {}
                progress = rules.get("progress")
                bucket_name = rules.get("bucket")
                stop_pct = rules.get("stop_pct")
                tp_pct = rules.get("take_profit_pct")
                # 2) 关键：止损/止盈价基于用户的"买入价"算
                if bucket_name and stop_pct is not None:
                    stop_price = calc_stop_price(price, 0)  # 用 0 调用仅取 stop_pct 百分比
                    # 上面 hack：直接重写 calc
                    from v0_6.core.gold_signal_v6 import get_stop_threshold, get_take_profit_threshold
                    stop_pct = get_stop_threshold(progress) if progress is not None else stop_pct
                    tp_pct = get_take_profit_threshold(progress) if progress is not None else tp_pct
                    stop_price = price * (1 + stop_pct)
                    tp_price = price * (1 + tp_pct)
                else:
                    stop_price = None
                    tp_price = None

                shares = amount / price if price > 0 else 0
                trade_id = add_trade(
                    target=target,
                    target_type=target_type,
                    target_name=target_name,
                    action=action,
                    amount=amount,
                    price=price,
                    shares=shares,
                    trade_date=date,
                    signal_type="GOLD",
                    position_pct=None,
                    progress=progress,
                    progress_bucket=bucket_name,
                    stop_threshold=stop_pct,
                    take_profit_threshold=tp_pct,
                    stop_price=stop_price,
                    take_profit_price=tp_price,
                    notes=notes,
                    source_industry=source_industry,
                    source_signal_date=source_signal_date,
                    source_signal_priority=source_signal_priority,
                    source_signal_type=source_signal_type,
                )
                # ── v1.1 警告 ──
                warn = ""
                if action == "BUY" and not source_industry:
                    warn = "未填写来源行业。持仓可以保存，但主线结构、相对强度和退潮监控将不可用。建议通过人工补录工具稍后填写。"
            elif action == "REDUCE":
                if not parent_trade_id:
                    raise ValueError("REDUCE 必须提供 parent_trade_id")
                if not price or price <= 0:
                    raise ValueError("REDUCE 必须提供实际成交价")

                # 查原持仓
                con = sqlite3.connect(str(DB_PATH))
                con.row_factory = sqlite3.Row
                row = con.execute(
                    "SELECT * FROM live_trades WHERE trade_id = ? AND closed = 0",
                    (parent_trade_id,),
                ).fetchone()
                if not row:
                    con.close()
                    raise ValueError(f"trade_id {parent_trade_id} 不存在或已平仓")

                # 校验减仓股数不超过净持仓
                shares = amount / price
                net = get_position_net(row["target"])
                if net["net_shares"] <= 0:
                    con.close()
                    raise ValueError(f"标的 '{row['target']}' 当前无持仓，不可减仓")
                if shares > net["net_shares"]:
                    con.close()
                    raise ValueError(
                        f"减仓股数 {shares:.0f} 超过净持仓 {net['net_shares']:.0f}"
                    )

                trade_id = add_trade(
                    target=row["target"],
                    target_type=row["target_type"],
                    target_name=row["target_name"],
                    action="REDUCE",
                    amount=amount,
                    price=price,
                    shares=shares,
                    trade_date=date,
                    notes=notes or f"减仓 from #{parent_trade_id}",
                )
                # reduced_1_3 标记是 per-target 级，只要任意 REDUCE 就算
                con.execute("UPDATE live_trades SET reduced_1_3 = 1 WHERE target = ?", (row["target"],))
                con.commit()
                con.close()
            elif action == "SELL":
                if not parent_trade_id:
                    raise ValueError("SELL 必须提供 parent_trade_id")
                if not price or price <= 0:
                    raise ValueError("SELL 必须提供实际成交价")

                # 查原持仓
                con = sqlite3.connect(str(DB_PATH))
                con.row_factory = sqlite3.Row
                row = con.execute(
                    "SELECT * FROM live_trades WHERE trade_id = ? AND closed = 0",
                    (parent_trade_id,),
                ).fetchone()
                con.close()
                if not row:
                    raise ValueError(
                        f"标的 '#{parent_trade_id}' 当前无未平仓持仓，不允许清仓。"
                        "请检查 trade_id 是否已平仓。"
                    )

                # 获取真实剩余净持仓
                net = get_position_net(row["target"])
                sell_shares = net["net_shares"]
                if sell_shares <= 0:
                    raise ValueError(f"标的 '{row['target']}' 当前无净持仓，不允许清仓")
                sell_amount = sell_shares * price

                # 先写 SELL 记录（closed=0）
                trade_id = add_trade(
                    target=row["target"],
                    target_type=row["target_type"],
                    target_name=row["target_name"],
                    action="SELL",
                    amount=sell_amount,
                    price=price,
                    shares=sell_shares,
                    trade_date=date,
                    notes=notes or f"清仓离场 {row['target']}",
                )

                # 再关闭该 target 的全部未平仓记录（包括刚写入的 SELL）
                close_trade_by_target(row["target"], price, date)
            else:
                raise ValueError(f"未知 action: {action}")

            resp = {"ok": True, "trade_id": trade_id, "target_name": target_name}
            if action in ("BUY",) and 'warn' in dir() and warn:
                resp["warning"] = warn
            self._send(
                200,
                "application/json",
                json.dumps(resp, ensure_ascii=False).encode("utf-8"),
            )
        except Exception as e:
            self._send(400, "application/json", json.dumps({"ok": False, "error": str(e)}).encode("utf-8"))


def main():
    print(f"\n=== 掘金信号 v1.1 实盘录入服务 | 端口 {PORT} ===\n")
    print(f"  📝 浏览器：http://localhost:{PORT}/")
    print(f"  📊 日报：file:///D:/zhuxian-catch-v0_6/reports/daily_v1/archive.html")
    print(f"  🛑 停止：Ctrl+C\n")
    init_schema()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), TradeFormHandler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n关闭服务。")
        srv.shutdown()


if __name__ == "__main__":
    main()
