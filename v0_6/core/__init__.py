"""
v0.6 核心模块
"""
from .config import RULES, load_rules
from .signal_b import (
    compute_industry_daily_metrics,
    compute_per_stock_indicators,
    detect_stabilizing_b,
    load_raw_daily,
    mark_repeat_priority,
)
from .live_trade_store import (
    add_trade,
    calc_progress_for_etf,
    close_trade,
    close_trade_by_target,
    get_position_net,
    get_position_summary_all,
    init_schema,
    list_open_positions,
    load_sector_etf_map,
    match_target,
)
from .live_trade_monitor import evaluate_position, monitor_all_positions
from .monitor_v6 import evaluate_position_v6, monitor_all_positions_v6
from .gold_signal_v6 import (
    PROGRESS_BUCKETS,
    SIGNAL_FACTORS,
    calc_stop_price,
    calc_take_profit_price,
    get_bucket,
    get_position_pct,
    get_stop_threshold,
    get_take_profit_threshold,
)

__all__ = [
    "RULES",
    "load_rules",
    "load_raw_daily",
    "compute_per_stock_indicators",
    "compute_industry_daily_metrics",
    "detect_stabilizing_b",
    "mark_repeat_priority",
    "init_schema",
    "add_trade",
    "close_trade",
    "close_trade_by_target",
    "get_position_summary_all",
    "list_open_positions",
    "get_position_net",
    "match_target",
    "load_sector_etf_map",
    "calc_progress_for_etf",
    "evaluate_position",
    "monitor_all_positions",
    "evaluate_position_v6",
    "monitor_all_positions_v6",
    "PROGRESS_BUCKETS",
    "SIGNAL_FACTORS",
    "get_bucket",
    "get_position_pct",
    "get_stop_threshold",
    "get_take_profit_threshold",
    "calc_stop_price",
    "calc_take_profit_price",
]
