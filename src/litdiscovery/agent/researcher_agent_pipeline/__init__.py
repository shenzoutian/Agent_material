"""
litdiscovery.agent.researcher_agent_pipeline —— 文献发现与候选扩展。

    doi_reach     检索编排库（关键词/种子 + 雪球 + 取舍 + 定稿，供 CLI retrieve 调用）
    keywords      关键词生成 + 联网前沿检索
    search        Apify MCP 检索 + 手动种子元数据补全
    snowball      引用雪球扩展（OpenAlex 主源 / Semantic Scholar 兜底）
    hyde          HyDE 需求拆分（researcher_agent 内部子能力）

re-export 采用惰性 __getattr__：依赖 langchain 的子模块不在包导入时加载，
避免无 langchain 环境下导入检索包时加载全部可选依赖。
"""

import importlib

_LAZY = {
    "run": "litdiscovery.agent.researcher_agent_pipeline.pipeline",
    "ResearcherRequest": "litdiscovery.contracts.agents",
    "ResearcherResult": "litdiscovery.contracts.agents",
    "Tee": "litdiscovery.common.logging",
    "sanitize_dir_name": "litdiscovery.common.logging",
    "create_log_dir": "litdiscovery.common.logging",
    "create_session_log": "litdiscovery.common.logging",
    "save_results": "litdiscovery.common.logging",
    "append_log_summary": "litdiscovery.common.logging",
    "_get": "litdiscovery.common.net",
    "parse_keyword_list": "litdiscovery.agent.researcher_agent_pipeline.keywords",
    "generate_keywords": "litdiscovery.agent.researcher_agent_pipeline.keywords",
    "frontier_search": "litdiscovery.agent.researcher_agent_pipeline.keywords",
    "build_frontier_context": "litdiscovery.agent.researcher_agent_pipeline.keywords",
    "confirm_keywords": "litdiscovery.agent.researcher_agent_pipeline.keywords",
    "search_papers_async": "litdiscovery.agent.researcher_agent_pipeline.search",
    "confirm_papers": "litdiscovery.agent.researcher_agent_pipeline.search",
    "_enrich_doi": "litdiscovery.agent.researcher_agent_pipeline.search",
    "fetch_neighbors": "litdiscovery.agent.researcher_agent_pipeline.snowball",
    "dedup_papers": "litdiscovery.agent.researcher_agent_pipeline.snowball",
    "rank_candidates": "litdiscovery.agent.researcher_agent_pipeline.snowball",
    "rank_by_llm": "litdiscovery.agent.researcher_agent_pipeline.snowball",
    "_norm_doi": "litdiscovery.agent.researcher_agent_pipeline.snowball",
}

__all__ = sorted(_LAZY.keys())


def __getattr__(name):
    """惰性加载：首次访问某名字时从对应子模块导入。"""
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module 'litdiscovery.agent.researcher_agent_pipeline' has no attribute {name!r}")
    mod = importlib.import_module(module)
    value = getattr(mod, name)
    globals()[name] = value
    return value
