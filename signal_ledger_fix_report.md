# signal_ledger 修复报告

**日期**: 2026-07-12
**版本**: v1.2.1（从 v1.2 修复）
**分支**: `feature/v1.2-report-enhancements`（待提交）
**基于**: main `aec69b9`（v1.2 日报记录能力增强）

---

## 修复问题清单

| # | 问题 | 修复 | 对应条款 |
|---|---|---|---|
| 1 | CSV 空值变成 NaN | `pd.read_csv(keep_default_na=False)` + `_safe_str()` 统一为 `""` | §4.1 |
| 2 | 裸 `if df.at[idx, "signal_date"]:` 判断 | 统一使用 `_has_value()` 函数 | §4.2 |
| 3 | `_compute_signal_outcome` 可能读取未来数据 | 增加 `as_of_date` 参数截断 | §5.1 |
| 4 | `_trading_day_offset` 无截止日期 | 增加 `as_of_date` 参数 | §5.2 |
| 5 | 交易日计数包含信号日本身（`>=`） | 改为 `>`（不含当日） | §6.1 |
| 6 | 新增 `elapsed_trading_days` 字段 | 数值字段，仅展示与校验 | §6.1 |
| 7 | RADAR 无退出逻辑 | 基于 `seen_industries` 实现雷达退出 → CLOSED（close_reason="雷达退出"） | §7.1 |
| 8 | 旧生命周期 CLOSED 后可能复用 ID | CLOSED 后 `active_by_industry` 清除，重新进入生成新 `signal_id` | §7.2 |
| 9 | `signal_date` 被直接覆盖（REPEAT 覆盖 FIRST） | `_has_value()` 检查，仅空时设置 | §8.1 |
| 10 | 持仓关闭仅通过"未出现在 open_positions"推断 | 增加 `closed_positions` 参数，按 `signal_id`/行业明确匹配 | §9.1 |
| 11 | HOLD 可能回退到 TRIGGERED | `_STATUS_ORDER` 防回退检查 | §9.3 |
| 12 | 退潮 CONFIRMED_RETREAT 直接关闭生命周期 | 移除退潮→CLOSED 分支，仅记录不关闭 | §10 |
| 13 | CSV 损坏返回空表清空历史 | 改为 `raise RuntimeError` | §11 |
| 14 | ETF 匹配无 `etf_match_status`/`etf_match_note` | 增加字段，三态：MATCHED/NOT_FOUND/ERROR | §12 |
| 15 | `get_recent_ledger()` 用 `tail(n)` 而非排序 | 改为按 `updated_at`, `signal_id` 倒序排列取 `head(n)` | §15 |
| 16 | ETF 匹配错误被显示为"无 ETF" | 日报展示区分"不可执行"/"匹配异常"/"可执行" | §16 |

## 修改文件

| 文件 | 操作 | 说明 |
|---|---|---|
| `v0_6/core/signal_ledger.py` | 重写 | 核心修复（>340 行增量） |
| `v0_6/tests/test_signal_ledger.py` | 重写 | 23 个测试用例（原 6 个 → 新增 17 个） |
| `v0_6/scripts/render_markdown_report.py` | 修改 | ETF 三态显示（信号档案表） |
| `v0_6/scripts/run_daily_v1_html.py` | 修改 | ETF 三态显示 + 交易状态行（信号档案段） |
| `signal_ledger_migration_audit.md` | 新增 | 迁移审计报告 |
| `signal_ledger_fix_report.md` | 新增 | 本报告 |
| `archive_before_signal_ledger_fix/` | 新增 | 修复前文件快照（9 个文件） |

## 测试结果

- **signal_ledger 测试**: 23 passed
- **全量测试**: 167 passed, 2 skipped
- 四因子/v1.1 相关测试：通过数不变（与修复前完全一致）

### 新增测试覆盖

| 测试 | 覆盖条款 | 状态 |
|---|---|---|
| test_null_roundtrip_preserved | §17.1 空值重读 | ✅ |
| test_d0_no_future_leak | §17.2 D0 无未来结果 | ✅ |
| test_staged_outcome_d1 | §17.3 分阶段 D+1 | ✅ |
| test_staged_outcome_d4 | §17.3 分阶段 D+4 | ✅ |
| test_staged_outcome_d5 | §17.3 分阶段 D+5 | ✅ |
| test_staged_outcome_d19 | §17.3 分阶段 D+19 | ✅ |
| test_staged_outcome_d20 | §17.3 分阶段 D+20 | ✅ |
| test_radar_exit_close | §17.4 雷达退出 | ✅ |
| test_radar_reentry_new_id | §17.5 雷达重新进入 | ✅ |
| test_hold_no_regress | §17.6 HOLD 不回退 | ✅ |
| test_explicit_close_via_signal_id | §17.7 明确平仓(signal_id) | ✅ |
| test_explicit_close_via_industry | §17.7 明确平仓(行业) | ✅ |
| test_retreat_does_not_close | §17.8 退潮≠平仓 | ✅ |
| test_repeat_preserves_original_signal_date | §17.9 REPEAT 保持原日期 | ✅ |
| test_csv_corrupt_raises | §17.10 CSV 损坏抛异常 | ✅ |
| test_etf_matched | §17.11 ETF MATCHED | ✅ |
| test_etf_not_found | §17.11 ETF NOT_FOUND | ✅ |
| test_etf_error | §17.11 ETF ERROR | ✅ |
| test_phase_boundaries | §17.12 阶段边界 | ✅ |
| test_multi_industry_sequence | §17.13 多行业序号 | ✅ |
| test_same_day_idempotent | §17.14 同日幂等 | ✅ |
| test_signal_id_format_and_radar_trigger_assoc | (原有) 格式+关联 | ✅ |
| test_hold_status_with_open_position | (原有) HOLD | ✅ |

## 是否改变四因子结果

**否。** 未修改四因子定义、行业宽度阈值、量比阈值、20 日收益阈值、弱势背景条件、60 日冷却、FIRST/REPEAT 判定、8%/12% 仓位、进度桶区间、止损止盈阈值、退潮评分、退潮交易动作、ETF 匹配规则、正式交易指令。

`signal_ledger.py` 保持"只记录，不参与信号计算，不参与交易决策"。

## 是否改变交易动作

**否。** 退潮 CONFIRMED_RETREAT 不再通过账本直接关闭生命周期。实际平仓仅通过 `closed_positions` 显式传入。不存在账本状态异常触发交易动作的路径。

## 信号数对比

- 修复前正式信号数：N/A（signal_ledger 此前未在正式日报中启用，无历史信号数）
- 修复后正式信号数：首次启用后将自动追踪
- **差异：0（无历史数据变化，四因子触发数不变）**

## 未解决问题

1. **`_write_ledger_atomic` 锁机制为基础实现**（文件锁 + 30 秒超时），在真正高并发场景下可能不够完善。如项目后续引入正式文件锁库，建议替换。
2. **`closed_positions` 参数需调用方传入**。当前 `run_daily_v1_html.py` 的调用未传递此参数（默认 None），平仓仅由 `monitor_v6.py` 处理。后续可在主流程中接入。
3. **`elapsed_trading_days` 为新字段**，旧版本代码读取新版 CSV 时此字段为 `""`（兼容）。

## 验收标准对照（§21）

| # | 标准 | 状态 |
|---|---|---|
| 1 | 雷达 CSV 重读后不误变 TRIGGERED | ✅ |
| 2 | 信号当天不出现 D+N 结果 | ✅ |
| 3 | D+N 只在对应交易日到达后出现 | ✅ |
| 4 | 雷达退出会关闭旧生命周期 | ✅ |
| 5 | 重新进入生成新 signal_id | ✅ |
| 6 | HOLD 不会无故退回 TRIGGERED | ✅ |
| 7 | 明确平仓后状态变 CLOSED | ✅ |
| 8 | REPEAT 不覆盖首次 signal_date | ✅ |
| 9 | ETF 无匹配与程序异常可区分 | ✅ |
| 10 | CSV 损坏不静默清空账本 | ✅ |
| 11 | 同日重跑无重复 | ✅ |
| 12 | 日报 MD 和 HTML 展示结果一致 | ✅ |
| 13 | 四因子信号数量与修复前完全一致 | ✅ |
| 14 | 交易动作与修复前完全一致 | ✅ |
