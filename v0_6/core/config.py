"""
v0.6 核心配置加载
"""
from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RULES_PATH = PROJECT_ROOT / "v0_6" / "data" / "rules.json"
DATA_DIR = PROJECT_ROOT / "data"
STOCK_DATA_DIR = PROJECT_ROOT / "stock_data"
STOCK_DATA_DB = STOCK_DATA_DIR / "stock_data.db"
DB_PATH = DATA_DIR / "a_stock_selector.sqlite3"
REPORTS_DIR = PROJECT_ROOT / "reports"
MONITORING_DIR = REPORTS_DIR / "monitoring"
DAILY_DIR = REPORTS_DIR / "daily_v1"

MONITORING_DIR.mkdir(parents=True, exist_ok=True)
DAILY_DIR.mkdir(parents=True, exist_ok=True)


def load_rules() -> dict:
    """加载交易规则配置"""
    with open(RULES_PATH, encoding="utf-8") as f:
        return json.load(f)


RULES = load_rules()
