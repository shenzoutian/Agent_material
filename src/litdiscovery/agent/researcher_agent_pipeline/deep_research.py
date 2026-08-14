"""Optional OpenAI Deep Research retrieval adapter.

The adapter owns API transport and normalization only. It never stores API keys and
returns a deterministic status object so the retrieval pipeline can continue when the
service is disabled, times out, or returns no DOI-bearing citations.
"""

from __future__ import annotations

import re
import time
from typing import Callable

import requests

from litdiscovery.config import (
    OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_DEEP_RESEARCH_MAX_TOOL_CALLS,
    OPENAI_DEEP_RESEARCH_MODEL, OPENAI_DEEP_RESEARCH_POLL_INTERVAL,
    OPENAI_DEEP_RESEARCH_TIMEOUT,
)

RESPONSES_URL = f"{OPENAI_BASE_URL}/responses"
DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:a-z0-9]+", re.I)


def _extract_text(response: dict) -> str:
    chunks = []
    for item in response.get("output", []) or []:
        for content in item.get("content", []) or []:
            text = content.get("text") or content.get("value")
            if text:
                chunks.append(str(text))
    return "\n".join(chunks)


def extract_dois(response: dict) -> list[str]:
    """Extract normalized DOI values from output text and citation URLs."""
    values = [_extract_text(response)]
    for item in response.get("output", []) or []:
        values.append(str(item.get("url") or ""))
        for content in item.get("content", []) or []:
            for annotation in content.get("annotations", []) or []:
                values.append(str(annotation.get("url") or annotation.get("title") or ""))
    seen, out = set(), []
    for value in values:
        for match in DOI_RE.findall(value):
            doi = match.rstrip(".,;)]}").lower()
            if doi not in seen:
                seen.add(doi)
                out.append(doi)
    return out


def run_deep_research(
    requirement: str,
    *,
    api_key: str | None = None,
    model: str | None = None,
    max_tool_calls: int | None = None,
    poll_interval: float | None = None,
    timeout: float | None = None,
    request: Callable = requests.request,
    sleep: Callable = time.sleep,
) -> dict:
    """Run a background Deep Research response and return normalized paper stubs."""
    key = (api_key or OPENAI_API_KEY or "").strip()
    if not key:
        return {"status": "disabled", "reason": "missing_openai_api_key", "papers": []}
    model = model or OPENAI_DEEP_RESEARCH_MODEL
    max_tool_calls = (OPENAI_DEEP_RESEARCH_MAX_TOOL_CALLS
                      if max_tool_calls is None else max_tool_calls)
    poll_interval = (OPENAI_DEEP_RESEARCH_POLL_INTERVAL
                     if poll_interval is None else poll_interval)
    timeout = OPENAI_DEEP_RESEARCH_TIMEOUT if timeout is None else timeout
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    prompt = (
        "Search scholarly and publisher sources for the following materials-science "
        "question. Prioritize primary research, return DOI identifiers, and distinguish "
        "direct evidence from reviews. Do not invent DOI values.\n\n" + requirement
    )
    payload = {
        "model": model,
        "input": prompt,
        "tools": [{"type": "web_search_preview"}],
        "background": True,
        "max_tool_calls": max_tool_calls,
    }
    try:
        created = request("POST", RESPONSES_URL, headers=headers, json=payload, timeout=60)
        created.raise_for_status()
        response = created.json()
        response_id = response.get("id")
        deadline = time.monotonic() + timeout
        while response.get("status") in {"queued", "in_progress"} and time.monotonic() < deadline:
            sleep(poll_interval)
            polled = request("GET", f"{RESPONSES_URL}/{response_id}", headers=headers, timeout=60)
            polled.raise_for_status()
            response = polled.json()
        status = response.get("status") or "unknown"
        if status != "completed":
            return {"status": "timeout" if time.monotonic() >= deadline else status,
                    "response_id": response_id, "papers": []}
        dois = extract_dois(response)
        papers = [{"doi": doi, "title": "", "year": None, "abstract": "",
                   "venue": "", "citation_count": 0, "source": "openai_deep_research"}
                  for doi in dois]
        return {"status": "completed", "response_id": response_id,
                "papers": papers, "report": _extract_text(response)}
    except (requests.RequestException, ValueError, TypeError) as exc:
        return {"status": "failed", "reason": f"{type(exc).__name__}: {exc}", "papers": []}
