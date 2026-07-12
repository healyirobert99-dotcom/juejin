# signal_ledger Schema 兼容性报告

**日期**: 2026-07-12

## 测试 5.1 — 无账本首次运行

- 删除 signal_ledger.csv
- 首次调用 `_read_ledger()` → 返回空 DataFrame with 全 19 列
- 首次调用 `update_signal_ledger()` → 自动创建 CSV，字段完整，编码 UTF-8
- 无 NaN 字符串，无重复 signal_id

✅ **PASS**

## 测试 5.2 — 旧版字段账本

构造仅含 v1.2 旧字段的 CSV（16 列），新版读取后自动补充：
- `elapsed_trading_days` → ""
- `etf_match_status` → ""
- `etf_match_note` → ""

无报错，旧数据正常保留。

✅ **PASS**

## 测试 5.3 — 损坏账本

构造二进制非法 CSV，`_read_ledger()` 抛出 `RuntimeError("读取信号账本失败")`：
- 停止运行
- 不覆盖原文件
- 不返回空账本继续执行

✅ **PASS**

## 结论

三项兼容性测试全部通过。
