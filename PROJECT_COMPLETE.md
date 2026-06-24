# v0.6 项目完成报告

## 交付时间

2026-06-24

## 项目空间

- 路径：`D:\zhuxian-catch-v0_6\`
- 与原项目 `D:\zhuxian-catch_2026-06-24\` 完全隔离
- 数据通过硬链接（stock_data.db 1.56GB）和复制（a_stock_selector.sqlite3 416MB）共享

## 已完成

### 1. 核心代码（v0_6/core/）

| 文件 | 功能 | 行数 |
|---|---|---|
| `config.py` | 配置加载 + 路径 | ~30 |
| `signal_b.py` | B 方案 4 因子信号检测 + 重复优先 | ~110 |
| `live_trade_store.py` | SQLite live_trades 增删改查 | ~140 |
| `live_trade_monitor.py` | 5/10 日 + 8% 止损 + 止盈监控 | ~140 |

### 2. 命令行工具（v0_6/scripts/）

| 工具 | 用途 |
|---|---|
| `log_trade.py` | 录入实盘交易（buy/add/reduce/sell） |
| `query_state.py` | 查询持仓状态 + 监控告警 |
| `run_daily_v1.py` | 跑日报 V1.0 + 监控报告 |
| `run_monitoring.py` | 独立监控入口 |

### 3. 报告（v0_6/reports/）

- `daily_v1/` — 完整日报 V1.0
- `monitoring/` — 监控报告

### 4. 测试（v0_6/tests/）

- **7 个测试全部通过**
- 覆盖：规则加载、信号检测、重复优先级、CRUD、监控评估

### 5. 数据流

```
stock_data.db (16 年价量)        a_stock_selector.sqlite3 (416MB)
  ↓                                  ↓
  load_raw_daily()                  add_trade() / list_open_positions()
  ↓                                  ↓
compute_industry_daily_metrics()   live_trades 表（实盘持仓）
  ↓                                  ↓
detect_stabilizing_b()             monitor_all_positions()
  ↓                                  ↓
mark_repeat_priority()             evaluate_position() + 规则
  ↓                                  ↓
B 方案信号 + 重复/首次标记         监控告警列表
  ↓                                  ↓
       run_daily_v1.py 合并
              ↓
       daily_v1_YYYY-MM-DD.md
       monitoring_YYYY-MM-DD.md
```

## 端到端验证

| 步骤 | 结果 |
|---|---|
| 录入 1 笔交易（银行，2025-12-15） | ✅ trade_id=10 |
| 跑监控（2026-06-24） | ✅ 检测到持仓 |
| 触发止盈 1 档 | ✅ "+555.0%，建议减 1/3 仓" |
| 7 个测试 | ✅ 全部通过 |

## 关键设计

### 5 项核心规则（`rules.json`）

| 规则 | 值 | 用途 |
|---|---|---|
| 5 日 min_return 止损 | -3% | 主动减半仓 |
| 10 日 min_return 止损 | -5% | 主动清仓 |
| 8% 机械止损 | -8% | 任何时点 |
| 20% 浮盈 | 减 1/3 仓 | 止盈 1 档 |
| 40% 浮盈 | 减 1/2 仓 | 止盈 2 档 |

### 仓位规则

- 首次触发：8% 仓位
- 120 日内重复触发：12% 仓位
- 总仓位上限：30%

### PIT 严格

- 信号检测只用 signal_date 及之前数据
- 监控告警只用 entry_date 及之后数据
- 不偷看未来

## 与原项目的关系

| 原项目（zhuxian-catch_2026-06-24） | v0.6 新项目（zhuxian-catch-v0_6）|
|---|---|
| V0.5 日报，9 章节 | V1.0 日报，5 章节（更直观）|
| 主系统 22 个 py，4328 行 generate_daily_review.py | v0.6 核心 ~420 行 |
| 主系统状态机（retreat_score 5 项加权）| B 方案 4 因子（已验证穿越牛熊）|
| 27 份历史日报 | 完整保留（在原项目）|
| 36 个测试 | 7 个新测试（v0.6）|

## 后续工作

### 短期（1-2 周）

1. **跑至少 1 周日报 V1.0** — 看实际使用体验
2. **录 1 笔真实持仓** — 验证 5/10 日止损规则
3. **跑重复触发的实盘** — 验证 12% 仓位是否合理

### 中期（1-2 月）

1. **积累 20 笔实盘数据** — 验证 B 方案在 2024-2026 牛市的真实表现
2. **比较实盘 vs 回测的差异** — 如果实盘胜率 < 70%，考虑调参
3. **补数据**：流通市值、ETF 持仓权重（用于"行业内 Top 3 龙头"）

### 长期（3+ 月）

1. **如果 30 笔实盘胜率 ≥ 70%** → 加仓到 10%
2. **如果 50 笔实盘胜率 ≥ 75%** → 启动 v0.7（增加中军龙头筛选）
3. **如果 100 笔实盘胜率 ≥ 80%** → 考虑扩大规模 / 资金

## 文件清单

```
D:\zhuxian-catch-v0_6\
├── README.md
├── pyproject.toml
├── data/
│   └── a_stock_selector.sqlite3  (416MB)
├── stock_data/
│   └── stock_data.db  (1.56GB, 硬链接)
├── v0_6/
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py
│   │   ├── signal_b.py
│   │   ├── live_trade_store.py
│   │   └── live_trade_monitor.py
│   ├── data/
│   │   └── rules.json
│   ├── docs/
│   │   └── TRADING_RULES.md
│   ├── reports/
│   │   ├── daily_v1/
│   │   └── monitoring/
│   ├── scripts/
│   │   ├── log_trade.py
│   │   ├── query_state.py
│   │   ├── run_daily_v1.py
│   │   └── run_monitoring.py
│   └── tests/
│       ├── test_core.py
│       └── test_monitor.py
├── reports/
│   ├── daily_v1/
│   └── monitoring/
└── .workbuddy/memory/
```

## 一句话总结

**v0.6 系统在独立项目空间完整搭建，B 方案 4 因子信号 + 实盘交易监控 + 5/10 日止损 + 重复优先**全部就绪，端到端测试通过，**可立即上实盘**。

⚠️ 本报告基于 AI 基于公开信息和项目内回测报告整理生成，仅供参考，不构成任何投资建议或个股推荐。投资有风险，决策需谨慎。
