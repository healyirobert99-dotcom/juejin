# update_signal_ledger 调用方审计

**日期**: 2026-07-12  
**分支**: `feature/v1.2.1-signal-ledger-fix`  
**提交**: `95013e8`

## 调用位置总览

| 文件 | 行号 | 调用场景 | open_positions 来源 | closed_positions 来源 | 是否适配 |
|---|---|---|---|---|---|
| `v0_6/scripts/run_daily_v1_html.py` | 1751 | **正式日报主线** | `get_position_summary_all()` | **未传入（默认 None）** | ✅ 参数顺序正确 |
| `v0_6/tests/test_signal_ledger.py` | 各处 | 23 个单元测试 | 测试构造 | 测试构造（含 None） | ✅ 全部适配 |

## 正式日报调用详情

**文件**: `v0_6/scripts/run_daily_v1_html.py:1751-1754`

```python
ledger_df = update_signal_ledger(
    radar_candidates or [], today_signals, industry_daily, signal_data_date,
    open_positions=open_positions,
)
```

参数检查：
- `radar_candidates`: 来自 `build_signal_radar()` → 正确
- `today_signals`: 来自 `get_today_signals()` → 正确
- `industry_daily`: 来自 `get_today_signals()` → 正确
- `signal_data_date`: 来自 `get_signal_data_date(requested_date)` → 正确
- `open_positions`: 来自 `get_position_summary_all()` → 正确
- `closed_positions`: **未传入** → 默认 None

## closed_positions 正式来源

**当前状态：尚未接入正式平仓数据源。**

正式日报调用中未传递 `closed_positions` 参数。这意味着：
- 平仓生命周期自动关闭**尚未接入**
- HOLD 状态的信号将保持原状态，直到通过 `open_positions` 消失（但 HOLD 受防回退保护）
- 实际平仓由 `monitor_v6.py` 处理，不依赖账本

**建议**（非本轮任务）：
- 接入 `live_trade_store` 的卖出记录（`action=SELL` 或 `action=REDUCE`）
- 或从 `get_position_summary_all()` 返回的已平仓记录中提取

## 总体评价

✅ 所有调用方已适配新版 signal_ledger（v1.2.1）  
⚠️ closed_positions 在正式日报中未接入（非阻塞，生命周期仍可正常记录）
