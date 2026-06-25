"""
行业 → ETF 智能匹配器

三层匹配：
1. 精确匹配：industry == row.sub
2. 别名匹配：通过 INDUSTRY_ALIASES
3. 模糊匹配：子串覆盖

ETF 多对多支持：同一 ETF 可对应多个行业（如 512170→医疗、CXO、医美），
全部 CSV 行独立参与匹配，结果中同一 ETF 只出现一次（取最高分）。

设计原则：
- 行业信号是 alpha，但 ETF 是 beta，**不能 100% 替代**
- 给出"覆盖度评分"，让你判断要不要下注
"""
from __future__ import annotations

from typing import Optional

from .market_data import load_sector_etf_rows

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
    """（兼容旧接口）加载 行业→ETF 映射表

    注意：旧接口会因 dict 覆盖丢失多对多关系。
    新代码请使用 market_data.load_sector_etf_rows()。
    """
    rows = load_sector_etf_rows()
    out = {}
    for r in rows:
        out.setdefault(r["code"], r)
    return out


def match_industry_to_etfs(industry: str, top_n: int = 3) -> dict:
    """行业 → 候选 ETF 列表（按推荐度排序）

    使用行级匹配，同一 ETF 通过多条关系命中时只保留最高分。

    Returns:
        {
            "industry": "保险",
            "matches": [...],
            "best": { ... },
            "match_type": "exact" | "alias" | "fuzzy" | "none",
            "confidence": 0.95,
            "note": "..."
        }
    """
    rows = load_sector_etf_rows()
    if not rows:
        return {
            "industry": industry,
            "matches": [],
            "best": None,
            "match_type": "none",
            "confidence": 0.0,
            "note": "sector_etf_map 缺失",
        }

    # 所有行参与匹配，记录最高分（同一 ETF 只保留最高分）
    scored: dict[str, dict] = {}  # code -> best match info

    for r in rows:
        code = r["code"]
        sub = r["sub"]
        name = r["name"]
        sector = r["sector"]
        score = 0.0
        match_type = "none"

        # 精确匹配
        if sub == industry:
            score = 1.0
            match_type = "exact"
        # 别名匹配
        elif industry in INDUSTRY_ALIASES and sub in INDUSTRY_ALIASES[industry]:
            score = 0.9
            match_type = "alias"
        # 子串模糊匹配
        elif industry in sub or industry in name or industry in sector:
            score = 0.7
            match_type = "fuzzy"
        # sub 中的细分包含 industry（如"银行"匹配"银行/保险"）
        elif industry in sub.split("/") or sub.startswith(industry):
            score = 0.6
            match_type = "sub"

        # 只更新更高分
        if score > 0 and (code not in scored or score > scored[code]["score"]):
            scored[code] = {
                "code": code,
                "name": name,
                "sector": sector,
                "sub": sub,
                "scale": r["scale"],
                "purity": r["purity"],
                "note": r["note"],
                "match_type": match_type,
                "score": score,
            }

    if not scored:
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
        if not s:
            return 0
        s = s.replace("~", "").replace("亿", "").strip()
        try:
            return float(s)
        except ValueError:
            return 0

    all_matches = sorted(scored.values(), key=lambda m: (m["score"], scale_to_num(m.get("scale", ""))), reverse=True)
    best_score = all_matches[0]["score"]

    if best_score >= 1.0:
        confidence = 0.95
        match_type = "exact"
        note = f"行业 '{industry}' 精确匹配 {sum(1 for m in all_matches if m['score'] >= 1.0)} 个 ETF"
    elif best_score >= 0.9:
        confidence = 0.85
        match_type = "alias"
        note = f"通过别名匹配到 {len(all_matches)} 个 ETF（行业名与 ETF 板块名略有差异）"
    elif best_score >= 0.7:
        confidence = 0.70
        match_type = "fuzzy"
        note = f"模糊匹配 {len(all_matches)} 个 ETF（行业名包含于 ETF 细分名）"
    else:
        confidence = 0.60
        match_type = "sub"
        note = f"通过细分名匹配 {len(all_matches)} 个 ETF（请注意覆盖度）"

    return {
        "industry": industry,
        "matches": all_matches[:top_n],
        "best": all_matches[0] if all_matches else None,
        "match_type": match_type,
        "confidence": confidence,
        "note": note,
    }


def etf_correlation_to_industry(industry: str, etf_code: str) -> dict:
    """评估 ETF 与行业的"匹配度"

    使用行级目录，选择与 industry 最相关的 CSV 配置。
    ETF 代码在 CSV 中存在即返回 found=True（即使行业评分低）。
    """
    rows = load_sector_etf_rows()
    best_row = None
    best_score = -1

    for r in rows:
        if r["code"] != etf_code:
            continue
        # 即使行业完全不匹配，也保留该 ETF 的信息
        score = 0
        if r["sub"] == industry:
            score = 3
        elif industry in r["sub"]:
            score = 2
        elif industry in r["sector"] or industry in r["name"]:
            score = 1
        if score > best_score:
            best_score = score
            best_row = r

    if not best_row:
        # ETF 代码在 CSV 中不存在
        return {"etf_code": etf_code, "found": False}

    purity = best_row.get("purity", "")
    note_text = best_row.get("note", "")
    warnings = []
    if purity in ("偏纯", "不纯"):
        warnings.append(f"纯度 = {purity}：ETF 持仓与 {industry} 行业指数有显著偏离")
    if "含" in note_text or "替代" in note_text or "混合" in note_text:
        warnings.append(f"备注：{note_text}")

    return {
        "etf_code": etf_code,
        "found": True,
        "industry": industry,
        "etf_name": best_row["name"],
        "purity": purity,
        "scale": best_row.get("scale", ""),
        "warnings": warnings,
        "note": note_text,
    }
