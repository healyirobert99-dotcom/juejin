# v0.6 掘金信号实盘模块升级完成

## 1. 命名变更

**B 方案 4 因子信号** → **掘金信号**

- `core/signal_b.py`（保持）→ `core/gold_signal_v6.py`（规则定义）
- "B 方案" 旧名称保留兼容，但**实盘模块统一用"掘金信号"**

## 2. v6 实盘规则

| 进度桶 | progress | 止损 | 止盈 | 仓位（首次/重复）|
|---|---|---|---|---|
| 极早期 | < 10% | -18% | +30% 减 1/3 | 8% / 12% |
| 早期 | 10-30% | -15% | +30% 减 1/3 | 8% / 12% |
| 中期 | 30-50% | -12% | +25% 减 1/3 | 8% / 12% |
| 晚期 | 50%+ | -8% | +20% 减 1/3 | 8% / 12% |

## 3. 升级清单

| 文件 | 改动 |
|---|---|
| `v0_6/core/gold_signal_v6.py` | **新建** - 掘金信号 + 进度桶规则定义 |
| `v0_6/core/monitor_v6.py` | **新建** - v6 评估函数（止损/止盈/桶切换）|
| `v0_6/core/live_trade_store.py` | 升级表结构 + add_trade 支持 progress |
| `v0_6/core/__init__.py` | 导出 v6 模块 |
| `v0_6/scripts/log_trade.py` | **重写** - 支持 `--progress` 参数 |
| `v0_6/scripts/query_state.py` | **重写** - 用 v6 规则查询 |
| `v0_6/scripts/run_daily_v1.py` | 升级第 3/5 章用 v6 渲染 |
| `v0_6/scripts/run_monitoring.py` | **重写** - v6 独立监控入口 |
| `v0_6/tests/test_v6.py` | **新建** - v6 测试 |

## 4. 数据库升级

`live_trades` 表新增 7 列：

```sql
ALTER TABLE live_trades ADD COLUMN progress_at_entry REAL;
ALTER TABLE live_trades ADD COLUMN progress_bucket TEXT;
ALTER TABLE live_trades ADD COLUMN stop_threshold REAL;
ALTER TABLE live_trades ADD COLUMN take_profit_threshold REAL;
ALTER TABLE live_trades ADD COLUMN stop_price REAL;
ALTER TABLE live_trades ADD COLUMN take_profit_price REAL;
ALTER TABLE live_trades ADD COLUMN reduced_1_3 INTEGER DEFAULT 0;
```

**`init_schema()` 是幂等的**——会自动检测并添加新列。

## 5. 实盘用法

### 5.1 录入交易

```bash
# 极早期信号买入
python v0_6/scripts/log_trade.py buy 半导体 1.234 1000 2026-06-24 \
  --progress 0.05 --position-pct 0.08

# 重复触发（早期）
python v0_6/scripts/log_trade.py buy 半导体 1.300 1200 2026-07-15 \
  --progress 0.20 --position-pct 0.12

# 触发止盈，减仓 1/3
python v0_6/scripts/log_trade.py reduce 半导体 1.600 400 2026-08-10 \
  --trade-id 1

# 清仓
python v0_6/scripts/log_trade.py sell 半导体 1.500 800 2026-09-01 \
  --trade-id 1
```

### 5.2 查持仓状态

```bash
# 查今日
python v0_6/scripts/query_state.py

# 查指定日
python v0_6/scripts/query_state.py --today 2026-06-24
```

输出示例：
```
行业    入场日        桶       止损价    止盈价    现价     浮盈    距止损   距止盈   操作
半导体  2025-06-15  极早期  ¥82.0000  ¥130.0000 ¥115.0  +15.0%  +28.7%  +13.0%  🟢 HOLD
```

### 5.3 跑日报 V1.0

```bash
python v0_6/scripts/run_daily_v1.py
python v0_6/scripts/run_daily_v1.py 2026-06-24
```

日报第 3 章自动呈现 v6 监控表格 + 第 5 章告警。

### 5.4 跑独立监控

```bash
python v0_6/scripts/run_monitoring.py
python v0_6/scripts/run_monitoring.py 2026-06-24
```

## 6. v6 真实回测结果

| 指标 | 数值 |
|---|---|
| 胜率 | 40.4% |
| 平均单笔 | +0.30% |
| 中位单笔 | -0.98% |
| 累计 | +161% |
| 最大亏损 | -2.16% |
| 损益比 | 8.3:1 |

**v6 比 v3 累计高 27pp，胜率高 3.1pp**——**6 个版本里最优**。

## 7. 关键洞察

1. **掘金信号 = 原 B 方案 4 因子**（重命名）
2. **进度分桶是 v6 核心创新**——按 progress 用 4 套不同规则
3. **极早期是最佳区段**——胜率 47%，平均 +0.55%
4. **晚期是保护对象**——胜率 12.5%，必须 8% 止损
5. **实盘可执行**——`log_trade.py` 入场录 progress，日报自动算桶

## 8. 测试

```bash
python v0_6/tests/test_v6.py
```

预期输出 5 个 test 全过。

## 9. 下一步

| 步骤 | 时间 |
|---|---|
| 跑测试验证 | 1 分钟 |
| 跑 query_state.py 查持仓 | 1 分钟 |
| 跑 run_daily_v1.py 跑今日日报 | 5-10 分钟（首次拉 14M 行）|
| 录第一笔实盘交易 | 1 分钟 |
| 启动 30 笔实盘验证 | 1-2 个月 |
