"""静态行业大类映射与联动判断

只负责：
- 加载 industry_groups.json
- 给定行业名查询所属大类
- 同一大类多细分雷达/正式信号的简单计数
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_GROUPS: dict[str, list[str]] | None = None
_REVERSE: dict[str, str] | None = None  # 行业名 → 大类名


def _data_dir() -> Path:
    from .config import DATA_DIR
    return DATA_DIR


def _load() -> tuple[dict[str, list[str]], dict[str, str]]:
    """加载行业大类配置（惰性、缓存）"""
    global _GROUPS, _REVERSE
    if _GROUPS is not None:
        return _GROUPS, _REVERSE  # type: ignore[return-value]

    path = Path(__file__).parent.parent / "data" / "industry_groups.json"
    if not path.exists():
        _GROUPS = {}
        _REVERSE = {}
        return _GROUPS, _REVERSE

    with open(str(path), encoding="utf-8") as f:
        raw: dict[str, Any] = json.load(f)

    groups: dict[str, list[str]] = {}
    reverse: dict[str, str] = {}
    for group_name, members in raw.items():
        if isinstance(members, list) and not group_name.startswith("_"):
            groups[group_name] = [str(m) for m in members]
            for m in groups[group_name]:
                if m not in reverse:
                    reverse[m] = group_name

    _GROUPS = groups
    _REVERSE = reverse
    return _GROUPS, _REVERSE


def reload() -> None:
    """清除缓存，下次调用重新加载"""
    global _GROUPS, _REVERSE
    _GROUPS = None
    _REVERSE = None


def get_group(industry: str) -> str | None:
    """查询行业所属大类，未映射返回 None"""
    _, rev = _load()
    return rev.get(industry)


def get_members(group_name: str) -> list[str]:
    """查询大类的成员行业列表"""
    grp, _ = _load()
    return list(grp.get(group_name, []))


def all_groups() -> dict[str, list[str]]:
    """返回全部大类映射"""
    grp, _ = _load()
    return dict(grp)


def check_group_linkage(
    industries: list[str],
    min_count: int = 2,
) -> list[dict]:
    """检查同一大类是否有 ≥min_count 个行业出现

    返回格式:
        [{"group": "医药", "members": [...], "count": 3}, ...]
    """
    grp, _ = _load()
    counter: dict[str, list[str]] = {}
    for ind in industries:
        g = get_group(ind)
        if g:
            counter.setdefault(g, []).append(ind)

    return [
        {
            "group": g,
            "members": sorted(counter[g]),
            "count": len(counter[g]),
        }
        for g in sorted(counter)
        if len(counter[g]) >= min_count
    ]
