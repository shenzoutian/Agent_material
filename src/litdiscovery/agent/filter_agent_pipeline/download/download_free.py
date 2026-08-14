"""免费开放获取来源：不依赖出版社订阅或付费 API。"""

from __future__ import annotations

from litdiscovery.agent.filter_agent_pipeline.acquisition import DownloadCandidate, unique_candidates
from litdiscovery.agent.filter_agent_pipeline import pdf_fetch, repositories


def discover(doi: str, paper: dict | None = None) -> list[DownloadCandidate]:
    """按稳定性返回免费 OA 候选，优先使用检索阶段已知的直接地址。"""
    candidates = pdf_fetch._paper_candidates(paper)
    for provider, finder in (
        ("arxiv", lambda: pdf_fetch._arxiv_pdf_url(doi)),
        ("unpaywall", lambda: pdf_fetch._try_unpaywall(doi)),
        ("semantic_scholar", lambda: pdf_fetch._try_semantic_scholar(doi)),
        ("core", lambda: pdf_fetch._try_core(doi)),
    ):
        print(f"      [OA] 尝试 {provider} ...")
        try:
            url = finder()
        except Exception:
            url = None
        if url:
            candidates.append(DownloadCandidate(provider, url))
    # PMC、Zenodo、HAL；OpenReview/ACL/NeurIPS 由仓储适配器统一扩展。
    candidates.extend(repositories.discover_repository_candidates(doi))
    return unique_candidates(candidates)
