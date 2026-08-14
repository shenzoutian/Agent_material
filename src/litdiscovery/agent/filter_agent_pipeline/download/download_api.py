"""出版社官方 API 来源。没有配置 Key 时对应适配器自然返回空结果。"""

from __future__ import annotations

from litdiscovery.agent.filter_agent_pipeline.acquisition import DownloadCandidate, unique_candidates
from litdiscovery.agent.filter_agent_pipeline import pdf_fetch


def discover(doi: str) -> list[DownloadCandidate]:
    prefix = doi.lower()
    providers = (
        ("elsevier", lambda: pdf_fetch._try_elsevier(doi) if prefix.startswith("10.1016/") else None),
        ("springer", lambda: pdf_fetch._try_springer(doi) if prefix.startswith("10.1007/") else None),
        ("ieee", lambda: pdf_fetch._try_ieee(doi) if prefix.startswith("10.1109/") else None),
        ("wiley", lambda: pdf_fetch._try_wiley(doi) if prefix.startswith("10.1002/") else None),
    )
    candidates = []
    for provider, finder in providers:
        print(f"      [API] 尝试 {provider} ...")
        try:
            url = finder()
        except Exception:
            url = None
        if url:
            candidates.append(DownloadCandidate(provider, url))
    return unique_candidates(candidates)
