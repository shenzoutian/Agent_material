"""Public repository candidate adapters used by the acquisition pipeline."""

from __future__ import annotations

from urllib.parse import quote

import requests

from litdiscovery.agent.filter_agent_pipeline.acquisition import DownloadCandidate


def discover_repository_candidates(doi: str, timeout: int = 30) -> list[DownloadCandidate]:
    """查询具备 DOI 检索 API 的开放仓储。

    OpenReview、ACL Anthology 与 NeurIPS Proceedings 没有稳定的 DOI -> PDF 公共
    查询接口；它们的 URL 应在检索阶段作为 ``oa_locations`` 保留，并由
    ``download_free`` 优先消费，避免按 DOI 猜测页面地址。
    """
    candidates = []
    candidates.extend(_pmc(doi, timeout))
    candidates.extend(_zenodo(doi, timeout))
    candidates.extend(_hal(doi, timeout))
    return candidates


def _pmc(doi: str, timeout: int) -> list[DownloadCandidate]:
    try:
        response = requests.get("https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                                params={"query": f'DOI:"{doi}"', "format": "json"}, timeout=timeout)
        if response.status_code != 200:
            return []
        rows = (response.json().get("resultList") or {}).get("result") or []
        pmcid = (rows[0] if rows else {}).get("pmcid")
        return ([DownloadCandidate("pmc", f"https://europepmc.org/articles/{pmcid}?pdf=render")]
                if pmcid else [])
    except (requests.RequestException, ValueError, TypeError):
        return []


def _zenodo(doi: str, timeout: int) -> list[DownloadCandidate]:
    try:
        response = requests.get("https://zenodo.org/api/records",
                                params={"q": f'doi:"{doi}"', "size": 5}, timeout=timeout)
        if response.status_code != 200:
            return []
        out = []
        for hit in (response.json().get("hits") or {}).get("hits") or []:
            for item in hit.get("files") or []:
                url = (item.get("links") or {}).get("content")
                if url and (item.get("key") or "").lower().endswith(".pdf"):
                    out.append(DownloadCandidate("zenodo", url))
        return out
    except (requests.RequestException, ValueError, TypeError):
        return []


def _hal(doi: str, timeout: int) -> list[DownloadCandidate]:
    try:
        response = requests.get("https://api.archives-ouvertes.fr/search/",
                                params={"q": f'doiId_s:"{doi}"', "fl": "fileMain_s", "wt": "json"},
                                timeout=timeout)
        if response.status_code != 200:
            return []
        return [DownloadCandidate("hal", row["fileMain_s"])
                for row in (response.json().get("response") or {}).get("docs") or []
                if row.get("fileMain_s")]
    except (requests.RequestException, ValueError, TypeError):
        return []
