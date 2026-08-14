"""
litdiscovery.memory —— 长期记忆（规则匹配历史索引）。

检索为纯规则匹配（无向量依赖），供 planner 的 memory 工具使用。
索引落盘到 artifacts/memory/。
"""

from .store import (
    build_index,
    save_index,
    load_index,
    known_dois,
    search,
    summary,
    _normalize_title,
    _norm_doi,
)
from .ingest import ingest, refresh

__all__ = [
    "build_index", "save_index", "load_index", "known_dois", "search", "summary",
    "ingest", "refresh",
]
