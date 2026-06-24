"""
行业 → ETF 智能匹配器

三层匹配：
1. 精确匹配：industry == sector_etf_map 的 sub 字段
2. 模糊匹配：industry 包含 sub 子串（如「保险」含「保险」）
3. 名称匹配：ETF 名称含 industry 关键字

返回：
- matches: 所有匹配到的 ETF
- best: 推荐首选（按规模/纯度排）
- match_type: 匹配方式
- confidence: 置信度 0-1

设计原则：
- 行业信号是 alpha，但 ETF 是 beta，**不能 100% 替代**
- 给出"覆盖度评分"，让你判断要不要下注
"""
from __future__ import annotations

import csv
import sqlite3
from pathlib import Path
from typing import Optional

from .config import DATA_DIR, DB_PATH

# 行业 → 概念板块的别名映射（部分行业名与 ETF 板块命名有差异）
INDUSTRY_ALIASES = {
    "保险": ["保险", "非银金融", "多元金融"],
    "证券": ["证券", "券商"],
    "银行": ["银行"],
    "白酒": ["白酒", "酒", "食品饮料"],
    "半导体": ["半导体", "芯片", "集成电路"],
    "医药": ["医药", "创新药", "医疗器械", "医药生物", "中药", "生物医药"],
    "新能源": ["新能源", "光伏", "锂电池", "新能源车", "电池"],
    "军工": ["国防军工", "军工", "低空经济", "通用航空", "商业航天", "卫星互联网"],
    "地产": ["房地产", "建材", "基建"],
    "消费": ["食品饮料", "白酒", "家电", "汽车整车", "旅游", "农业", "传媒", "游戏"],
}


def load_sector_etf_map() -> dict:
    """加载 行业→ETF 映射表"""
    path = DATA_DIR / "sector_etf_map.csv"
    out = {}
    if not path.exists():
        return out
    with open(path, encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader)
        for row in reader:
            if not row or row[0].startswith("==="):
                continue
            if len(row) < 6 or not row[3].strip() or row[3] == "—":
                continue
            etf = row[3].strip()
            out[etf] = {
                "sector": row[0].strip(),  # 大板块
                "sub": row[2].strip(),  # 细分
                "name": row[4].strip(),  # ETF 名称
                "index": row[5].strip() if len(row) > 5 else "",  # 跟踪指数
                "scale": row[6].strip() if len(row) > 6 else "",  # 规模
                "purity": row[7].strip() if len(row) > 7 else "",  # 纯度
                "note": row[8].strip() if len(row) > 8 else "",  # 备注
            }
    return out


def match_industry_to_etfs(industry: str, top_n: int = 3) -> dict:
    """行业 → 候选 ETF 列表（按推荐度排序）

    Returns:
        {
            "industry": "保险",
            "matches": [
                {"code": "512070", "name": "易方达沪深300非银ETF", "sector": "金融", "sub": "非银金融", "scale": "~50", "purity": "偏纯", "match_type": "alias"},
                ...
            ],
            "best": { ... },  # 最推荐
            "match_type": "exact" | "alias" | "fuzzy" | "none",
            "confidence": 0.95,  # 0-1
            "note": "无纯保险ETF，用非银512070替代但含券商"  # 备注
        }
    """
    etf_map = load_sector_etf_map()
    if not etf_map:
        return {
            "industry": industry,
            "matches": [],
            "best": None,
            "match_type": "none",
            "confidence": 0.0,
            "note": "sector_etf_map 缺失",
        }

    # 1) 精确匹配：industry == sub
    exact = [
        {"code": code, **info, "match_type": "exact", "score": 1.0}
        for code, info in etf_map.items()
        if info["sub"] == industry
    ]
    # 2) 别名匹配：industry 在 INDUSTRY_ALIASES 别名里
    aliases = INDUSTRY_ALIASES.get(industry, [])
    alias_match = [
        {"code": code, **info, "match_type": "alias", "score": 0.9}
        for code, info in etf_map.items()
        if info["sub"] in aliases and industry not in [m["sub"] for m in exact]
    ]
    # 3) 模糊匹配：industry 含 sub 子串
    fuzzy = [
        {"code": code, **info, "match_type": "fuzzy", "score": 0.7}
        for code, info in etf_map.items()
        if (industry in info["sub"] or industry in info["name"] or industry in info["sector"])
        and code not in [m["code"] for m in exact + alias_match]
    ]
    # 4) 名称反向匹配：sub 包含 industry 子串（如「银行」是「银行/保险」的子串）
    sub_match = [
        {"code": code, **info, "match_type": "sub", "score": 0.6}
        for code, info in etf_map.items()
        if (industry in info["sub"].split("/") or info["sub"].startswith(industry))
        and code not in [m["code"] for m in exact + alias_match + fuzzy]
    ]

    all_matches = exact + alias_match + fuzzy + sub_match

    if not all_matches:
        return {
            "industry": industry,
            "matches": [],
            "best": None,
            "match_type": "none",
            "confidence": 0.0,
            "note": f"行业 {industry} 在 ETF 库中无对应标的（可能是细分行业/无对应 ETF）",
        }

    # 按 (score 降序, 规模 降序) 排序
    def scale_to_num(s: str) -> float:
        """规模字段 '~300' → 300"""
        if not s:
            return 0
        s = s.replace("~", "").replace("亿", "").strip()
        try:
            return float(s)
        except ValueError:
            return 0

    all_matches.sort(key=lambda m: (m["score"], scale_to_num(m.get("scale", ""))), reverse=True)

    # 置信度
    if exact:
        confidence = 0.95
        match_type = "exact"
        note = f"行业 '{industry}' 精确匹配 {len(exact)} 个 ETF"
    elif alias_match:
        confidence = 0.85
        match_type = "alias"
        note = f"通过别名匹配到 {len(alias_match)} 个 ETF（行业名与 ETF 板块名略有差异）"
    elif fuzzy:
        confidence = 0.70
        match_type = "fuzzy"
        note = f"模糊匹配 {len(fuzzy)} 个 ETF（行业名包含于 ETF 细分名）"
    else:
        confidence = 0.60
        match_type = "sub"
        note = f"通过细分名匹配 {len(sub_match)} 个 ETF（请注意覆盖度）"

    return {
        "industry": industry,
        "matches": all_matches[:top_n],
        "best": all_matches[0] if all_matches else None,
        "match_type": match_type,
        "confidence": confidence,
        "note": note,
    }


def etf_correlation_to_industry(industry: str, etf_code: str) -> dict:
    """评估 ETF 与行业的"匹配度"（返回相关性估算）

    这是**理论评估**而非真实回测（真实需要价格序列对齐）
    返回的字段：
    - purity: 纯度（来自 sector_etf_map 的人工标注）
    - scale: 规模
    - note: 人工备注
    - 警告：纯度"偏"/"不纯"时**显著偏离**行业指数
    """
    etf_map = load_sector_etf_map()
    info = etf_map.get(etf_code)
    if not info:
        return {"etf_code": etf_code, "found": False}

    purity = info.get("purity", "")
    note = info.get("note", "")
    warnings = []
    if purity in ("偏纯", "不纯"):
        warnings.append(f"纯度 = {purity}：ETF 持仓与 {industry} 行业指数有显著偏离")
    if "含" in note or "替代" in note or "混合" in note:
        warnings.append(f"备注：{note}")

    return {
        "etf_code": etf_code,
        "found": True,
        "industry": industry,
        "etf_name": info["name"],
        "purity": purity,
        "scale": info.get("scale", ""),
        "warnings": warnings,
        "note": note,
    }
