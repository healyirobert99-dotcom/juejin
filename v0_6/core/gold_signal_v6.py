"""
v6 实盘规则 — 进度分桶 + 掘金信号

命名约定：
- 掘金信号 = 原 B 方案 4 因子信号（重命名）
- 进度桶 = 极早期 / 早期 / 中期 / 晚期
- 每个桶对应不同的止损/止盈阈值

进度定义：progress = (entry_price - 60d_low) / 60d_low
"""

# 掘金信号 4 因子（原 B 方案）
SIGNAL_FACTORS = {
    "name": "掘金信号",
    "version": "v6",
    "factors": [
        {"id": 1, "name": "行业宽度", "operator": ">=", "threshold": 0.35},
        {"id": 2, "name": "量比", "operator": ">=", "threshold": 1.0},
        {"id": 3, "name": "行业平均 20 日涨幅", "operator": ">", "threshold": 0.0},
        {"id": 4, "name": "真从弱转强", "operator": "yes", "description": "过去 60 日多数 < 35% 且最近 1 日 ≥ 35%"},
    ],
    "filters": {
        "industry_min_stocks": 20,
        "cooldown_days": 60,
    },
}

# 进度桶定义
PROGRESS_BUCKETS = [
    {"name": "极早期", "min": 0.0, "max": 0.10, "stop": -0.18, "take_profit": 0.30, "position_first": 0.08, "position_repeat": 0.12},
    {"name": "早期", "min": 0.10, "max": 0.30, "stop": -0.15, "take_profit": 0.30, "position_first": 0.08, "position_repeat": 0.12},
    {"name": "中期", "min": 0.30, "max": 0.50, "stop": -0.12, "take_profit": 0.25, "position_first": 0.08, "position_repeat": 0.12},
    {"name": "晚期", "min": 0.50, "max": 999.0, "stop": -0.08, "take_profit": 0.20, "position_first": 0.08, "position_repeat": 0.12},
]


def get_bucket(progress: float) -> dict:
    """根据 progress 返回对应的桶规则"""
    for bucket in PROGRESS_BUCKETS:
        if bucket["min"] <= progress < bucket["max"]:
            return bucket
    return PROGRESS_BUCKETS[-1]  # fallback to 晚期


def get_position_pct(progress: float, is_repeat: bool) -> float:
    """根据 progress 和重复标记返回仓位"""
    bucket = get_bucket(progress)
    return bucket["position_repeat"] if is_repeat else bucket["position_first"]


def get_stop_threshold(progress: float) -> float:
    """根据 progress 返回止损阈值"""
    return get_bucket(progress)["stop"]


def get_take_profit_threshold(progress: float) -> float:
    """根据 progress 返回止盈阈值"""
    return get_bucket(progress)["take_profit"]


def calc_stop_price(entry_price: float, progress: float) -> float:
    """计算止损价"""
    return entry_price * (1 + get_stop_threshold(progress))


def calc_take_profit_price(entry_price: float, progress: float) -> float:
    """计算止盈价"""
    return entry_price * (1 + get_take_profit_threshold(progress))
