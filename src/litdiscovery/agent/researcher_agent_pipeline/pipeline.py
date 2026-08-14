"""Public researcher pipeline facade.

这是精简的程序化检索入口（不编排 HyDE/雪球，那属于 executor 的 planner 链）；
与 executor 的 ``write_doi_list`` 收敛共用同一去重实现（``quality.deduplicate_papers``），
保证两条入口的合并语义一致。
"""

import asyncio
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from litdiscovery.contracts.agents import ResearcherRequest, ResearcherResult
from litdiscovery.paths import handoff_path, resolve_batch
from litdiscovery.agent.filter_agent_pipeline.quality import deduplicate_papers
from .deep_research import run_deep_research
from .keywords import generate_keywords
from .memory_search import search_memory_papers
from .search import search_papers_async


def run(request: ResearcherRequest) -> ResearcherResult:
    """Retrieve and merge online, Deep Research, and memory candidates."""
    if not isinstance(request, ResearcherRequest):
        raise TypeError("request must be ResearcherRequest")
    keywords = list(request.keywords) or generate_keywords(
        request.requirement, request.keyword_count,
        use_search=request.use_frontier_search,
    )
    online = asyncio.run(search_papers_async(keywords, request.results_per_keyword))
    # Deep Research 与 memory 检索互不依赖，并发执行（Deep Research 是最慢的来源）
    deep, memory = [], []
    with ThreadPoolExecutor(max_workers=2) as ex:
        deep_fut = (
            ex.submit(run_deep_research, request.requirement,
                      model=request.deep_research_model or None,
                      max_tool_calls=request.deep_research_max_tool_calls or None)
            if request.use_deep_research else None
        )
        memory_fut = (
            ex.submit(search_memory_papers, request.requirement, request.memory_limit)
            if request.use_memory else None
        )
        if deep_fut is not None:
            deep = deep_fut.result().get("papers", [])
        if memory_fut is not None:
            memory = memory_fut.result()
    papers, _duplicates = deduplicate_papers(online + deep + memory)
    output_path = None
    batch = None
    if request.batch:
        batch_path = resolve_batch(request.batch)
        batch = str(batch_path)
        path = handoff_path(batch_path, "doi_list.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(papers, ensure_ascii=False, indent=2), encoding="utf-8")
        output_path = str(path)
    source_counts = Counter(str(p.get("source") or "unknown") for p in papers)
    return ResearcherResult(tuple(papers), tuple(keywords), dict(source_counts), batch, output_path)
