# 掘金信号 v1.1 每日流程

## 每日固定流程

```
1. 刷新行情
   python v0_6/scripts/refresh_market_data.py

2. 运行每日检查（可选但推荐）
   python v0_6/scripts/check_daily_readiness.py

3. 生成日报（自动生成 HTML + Markdown）
   python v0_6/scripts/run_daily_v1_html.py 2026-07-03

4. 查看结果
   - HTML 快速版：reports/daily_v1/daily_v1_YYYY-MM-DD.html
   - Markdown 研究版：reports/daily_v1/daily_v1_YYYY-MM-DD.md
   - 档案索引：reports/daily_v1/archive.html
```

## 每日检查项

`check_daily_readiness.py` 检查以下三项：

| 检查项 | 说明 |
|---|---|
| 持仓来源行业 | 是否存在 source_industry 缺失的开放持仓 |
| 雷达状态文件 | radar_state.csv 是否可读写 |
| 案例账本文件 | signal_cases.csv 是否可读写 |

## 来源行业补录

**补录不是每日任务。**

只有当以下情况才需要补录：

- 日报终端提示"来源行业待补录"
- `check_daily_readiness.py` 显示 NEED_ACTION

### 补录步骤

```bash
python v0_6/scripts/backfill_position_source.py
```

补录完成后，重新生成日报即可。后续日报会自动使用该来源行业进行主线结构、相对强度、趋势延续和退潮分析。

### 什么时候不需要补录

- 所有开放持仓均已填写 source_industry
- `check_daily_readiness.py` 显示"可以直接生成日报"

### 历史持仓（如 513780）

513780 是 v1.1 之前的持仓，因此 source_industry 缺失。这是一次性修复：

1. 运行 `backfill_position_source.py`
2. 根据实际买入信号填写来源行业
3. 补录后，所有后续日报自动生效

## 新建仓

今后通过 v1.1 交易录入页面建仓时，应在"来源信号"区域填写：

- 来源行业
- 来源信号日期
- 信号类型（STABILIZING_B）
- FIRST / REPEAT

如果当时未填写，系统允许保存，但行业分析暂不可用。后续可通过补录工具修复。

## 系统版本

- 版本：掘金信号 v1.1
- 归档：reports/daily_v1/archive.html
- 观察记录：reports/observation/radar_state.csv / signal_cases.csv

---

*本系统为交易辅助工具。标的选择和最终交易由人工决定。辅助分析不阻断正式四因子信号。*
