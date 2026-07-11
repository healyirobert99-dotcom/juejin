"""掘金信号 版本信息

v1.2 说明（仅日报记录能力增强，不修改四因子/交易规则/评分/过滤）：
- 策略版本（Zhuxian Catch）与规则版本（Bucket）保持不变
- 仅「日报」维度升到 v1.2（新增信号档案 / 信号验证 / ETF 可执行性 / 版本统一）
"""
from __future__ import annotations

VERSION = "1.1.0"                       # 策略版本（不随日报升级而变）
VERSION_NAME = "轻量观察与验证增强版"
VERSION_LABEL = f"掘金信号 v{VERSION}"

# ── v1.2 统一版本标识（仅展示用，不改变任何规则版本） ──
STRATEGY_VERSION = "Zhuxian Catch v1.1"   # 策略口径
RULE_VERSION = "Bucket v6"                # 规则口径
REPORT_VERSION = "Report v1.2"            # 日报口径（本次升级只动这里）
