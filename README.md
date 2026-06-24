# zhuxian-catch v0.6

A 股主线识别系统 v0.6（重做版本）

## 核心特性

- **B 方案 4 因子信号检测**——16 年 4 cutoff OOS 验证穿越牛熊
- **实盘交易监控**——5/10 日 min_return + 8% 机械止损
- **重复触发优先**——同行业 120 日内重复信号优先展示
- **小行业过滤**——股票数 < 20 的行业不参与
- **V1.0 日报**——5 秒看完、30 秒决策、3 分钟执行

## 目录结构

```
zhuxian-catch-v0_6/
├── v0_6/
│   ├── core/           # 核心逻辑（信号、监控、规则）
│   ├── reports/        # 日报模板
│   ├── scripts/        # 命令行工具
│   ├── tests/          # 测试
│   ├── data/           # 配置（rules.json）
│   └── docs/           # 文档
├── data/               # 数据（a_stock_selector.sqlite3, 软链）
├── stock_data/         # 16 年后复权价量（软链）
├── reports/            # 生成的报告
└── .workbuddy/memory/  # 工作记录
```

## 快速使用

```bash
cd D:\zhuxian-catch-v0_6
pip install -e ".[dev]"

# 1. 录入一笔实盘交易
python v0_6/scripts/log_trade.py buy 半导体 1.234 1000 2026-06-24

# 2. 跑日报 V1.0
python v0_6/scripts/run_daily_v1.py

# 3. 跑监控报告
python v0_6/scripts/run_monitoring.py

# 4. 查询持仓
python v0_6/scripts/query_state.py
```

## 核心结论

B 方案在 16 年全量数据 4 cutoff OOS 上**穿越牛熊**：
- 2018 熊市：78.6% 命中
- 2022 震荡：72.4% 命中
- 2024-2025 牛：100% 命中
