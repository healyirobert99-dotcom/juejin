# v0.6 实盘交易监控——实操规则

## 1. 录入实盘交易

### 1.1 买入（首次触发）

```bash
python v0_6/scripts/log_trade.py buy <行业> <价格> <股数> <日期> 0.08
```

示例：
```bash
python v0_6/scripts/log_trade.py buy 半导体 1.234 1000 2026-06-24 0.08
```

仓位 = 8%（首次触发默认值）。

### 1.2 加仓（重复触发）

```bash
python v0_6/scripts/log_trade.py add <行业> <价格> <股数> <日期> 0.12
```

仓位 = 12%（重复触发默认值）。

### 1.3 减仓（止盈/止损）

```bash
python v0_6/scripts/log_trade.py reduce <行业> <价格> <股数> <日期>
```

### 1.4 清仓

```bash
python v0_6/scripts/log_trade.py sell <行业> <价格> <股数> <日期>
```

## 2. 查询持仓

```bash
# 监控所有持仓
python v0_6/scripts/query_state.py

# 查询某行业
python v0_6/scripts/query_state.py 半导体
```

## 3. 监控规则

### 3.1 止损（触发任一即提醒）

| 规则 | 阈值 | 触发动作 |
|---|---|---|
| 5 日 min_return | < -3% | 减半仓 |
| 10 日 min_return | < -5% | 清仓 |
| 机械止损 | < -8% | 清仓 |

### 3.2 止盈（可选）

| 规则 | 阈值 | 触发动作 |
|---|---|---|
| 浮盈 1 档 | ≥ +20% | 减 1/3 仓 |
| 浮盈 2 档 | ≥ +40% | 减 1/2 仓 |

### 3.3 优先级

- **120 日内重复触发** > **首次触发**
- 重复触发：建议仓位 12%
- 首次触发：建议仓位 8%

## 4. 每日跑日报

```bash
# 跑日报 V1.0 + 监控报告
python v0_6/scripts/run_daily_v1.py                  # 默认今天
python v0_6/scripts/run_daily_v1.py 2026-06-23       # 指定日期
```

输出：
- `reports/daily_v1/daily_v1_2026-06-24.md` — 完整日报
- `reports/monitoring/monitoring_2026-06-24.md` — 监控报告

## 5. 配置修改

`v0_6/data/rules.json`：
```json
{
  "stop_loss": {
    "mechanical": -0.08,
    "min_return_5d": -0.03,
    "min_return_10d": -0.05
  },
  "take_profit": {
    "tier1": {"pct": 0.20, "action": "REDUCE_1_3"},
    "tier2": {"pct": 0.40, "action": "REDUCE_1_2"}
  },
  "position": {
    "first_trigger": 0.08,
    "repeat_trigger": 0.12,
    "max_total": 0.30
  }
}
```

修改后所有后续计算自动用新值。

## 6. 实盘工作流（每日）

1. **早上**：跑 `python v0_6/scripts/run_daily_v1.py`
2. **看日报**：第 3 章"实盘监控"显示当前持仓状态
3. **看告警**：第 5 章"监控告警"显示触发的交易规则
4. **录入交易**：根据信号买入/卖出
5. **晚上**：再跑一次监控，确认所有告警已处理
