# signal_ledger 迁移审计报告

**日期**: 2026-07-12
**版本**: v1.2.1 修复

## 审计结论：无需迁移

### 当前状态
- `reports/observation/signal_ledger.csv` **不存在**（未生成过）
- 项目尚在使用 `reports/observation/signal_cases.csv`（来自 `observation_tracker.py`，属于雷达案例追踪，独立系统）
- `signal_ledger.py` 为 v1.2 新增模块，此前未在正式日报运行中启用

### 审计项检查

| 检查项 | 状态 |
|---|---|
| signal_date 为 nan 但状态 TRIGGERED | N/A（无账本） |
| D+N 早于对应观察日已有结果 | N/A |
| RADAR 长期未退出 | N/A |
| HOLD 回退 TRIGGERED | N/A |
| 重复 signal_id | N/A |
| 同一行业多个活跃生命周期 | N/A |

### 迁移规则确认
1. 可从已有日报明确恢复的 → N/A（无历史账本）
2. 无法明确判断的 → N/A
3. 不得编造历史信号日期 → 遵守
4. 不得补造 D+N 结果 → 遵守
5. 不得把不确定数据自动关闭 → 遵守

### 后续
- 首次运行 `update_signal_ledger` 时将自动创建 `signal_ledger.csv`
- 与现有 `signal_cases.csv` 并存，互不干扰
- `signal_cases.csv` 继续由 `observation_tracker.py` 管理
