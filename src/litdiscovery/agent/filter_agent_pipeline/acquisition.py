"""Download validation, failure classification, circuit breaking, and audit."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Iterable

from litdiscovery.common.fs import write_json_atomic

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


@dataclass(frozen=True)
class DownloadCandidate:
    provider: str
    url: str
    kind: str = "pdf"


def classify_response(status_code: int, content_type: str = "") -> tuple[str, bool]:
    if status_code in (401, 403):
        return "access_denied", False
    if status_code in (404, 410):
        return "not_found", False
    if status_code == 429:
        return "rate_limited", True
    if status_code in RETRYABLE_STATUS:
        return "provider_unavailable", True
    if not 200 <= status_code < 300:
        return "http_error", False
    content_type = (content_type or "").lower()
    if "html" in content_type:
        return "html_landing_page", False
    if "xml" in content_type or "json" in content_type:
        return "structured_response_not_pdf", False
    return "ok", False


def is_pdf_prefix(prefix: bytes) -> bool:
    return prefix.lstrip().startswith(b"%PDF-")


def validate_pdf_bytes(content: bytes) -> tuple[bool, str, int]:
    """Perform dependency-free structural checks before accepting a PDF."""
    if not is_pdf_prefix(content[:1024]):
        return False, "invalid_pdf_signature", 0
    if b"%%EOF" not in content[-4096:]:
        return False, "missing_pdf_eof", 0
    pages = len(re.findall(rb"/Type\s*/Page(?!s)\b", content))
    if pages < 1:
        return False, "no_pdf_pages", 0
    return True, "ok", pages


def classify_access(paper: dict) -> str:
    """Classify a record before network acquisition without claiming access rights."""
    locations = paper.get("oa_locations") or paper.get("locations") or []
    best = paper.get("best_oa_location") or {}
    if (paper.get("pdf_url") or paper.get("fulltext_url") or paper.get("oa_url")
            or best.get("pdf_url") or any((loc or {}).get("pdf_url") for loc in locations)):
        return "download_ready"
    if paper.get("is_oa") or paper.get("is_open_access"):
        return "metadata_only"
    return "restricted"


class ProviderCircuitBreaker:
    def __init__(self, threshold: int = 5, cooldown_seconds: int = 600):
        self.threshold = threshold
        self.cooldown_seconds = cooldown_seconds
        self._failures: dict[str, int] = {}
        self._opened_at: dict[str, float] = {}

    def available(self, provider: str) -> bool:
        opened = self._opened_at.get(provider)
        if opened is None:
            return True
        if time.monotonic() - opened >= self.cooldown_seconds:
            self._failures.pop(provider, None)
            self._opened_at.pop(provider, None)
            return True
        return False

    def record(self, provider: str, retryable_failure: bool) -> None:
        if not retryable_failure:
            return
        count = self._failures.get(provider, 0) + 1
        self._failures[provider] = count
        if count >= self.threshold:
            self._opened_at[provider] = time.monotonic()

    def success(self, provider: str) -> None:
        self._failures.pop(provider, None)
        self._opened_at.pop(provider, None)


class DownloadAudit:
    def __init__(self, batch_root: str | Path):
        self.orders = Path(batch_root) / "orders"
        self.orders.mkdir(parents=True, exist_ok=True)
        self.events_path = self.orders / "fulltext_attempts.jsonl"
        self.summary_path = self.orders / "download_summary.json"
        self._lock = Lock()

    def record(self, **event) -> None:
        event.setdefault("timestamp", time.strftime("%Y-%m-%dT%H:%M:%S%z"))
        with self._lock, self.events_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")

    def summarize(self) -> dict:
        events = []
        if self.events_path.exists():
            for line in self.events_path.read_text(encoding="utf-8").splitlines():
                try:
                    events.append(json.loads(line))
                except ValueError:
                    continue
        providers: dict[str, dict[str, int]] = {}
        successful_dois = set()
        failures: dict[str, int] = {}
        for event in events:
            provider = event.get("provider") or "unknown"
            outcome = event.get("outcome") or "unknown"
            counts = providers.setdefault(provider, {})
            counts[outcome] = counts.get(outcome, 0) + 1
            if outcome == "success" and event.get("doi"):
                successful_dois.add(event["doi"])
            failure = event.get("failure_class")
            if failure:
                failures[failure] = failures.get(failure, 0) + 1
        summary = {"schema_version": 1, "attempts": len(events),
                   "successful_dois": len(successful_dois), "providers": providers,
                   "failure_classes": failures}
        write_json_atomic(self.summary_path, summary)
        return summary


def unique_candidates(candidates: Iterable[DownloadCandidate]) -> list[DownloadCandidate]:
    seen = set()
    result = []
    for candidate in candidates:
        url = candidate.url.strip()
        if url and url not in seen:
            seen.add(url)
            result.append(candidate)
    return result


@dataclass
class DownloadStats:
    """文献下载过程的实时统计：总数 / 已下载 / 失败 / 跳过 / 格式分布。

    供 download_batch、_run_fulltext、tools.fetch_fulltext 三处下载循环共用，
    每个 DOI 处理完后 print(render()) 即输出一行实时进度。
    """

    total: int = 0
    success: int = 0
    failed: int = 0
    skipped: int = 0
    too_small: int = 0
    formats: dict = field(default_factory=dict)

    @property
    def done(self) -> int:
        """已处理数量（成功 + 失败 + 跳过 + 仅摘要/过小）。"""
        return self.success + self.failed + self.skipped + self.too_small

    def record(self, ok: bool = True, fmt: str = "", skipped: bool = False,
               too_small: bool = False) -> None:
        if too_small:
            self.too_small += 1
        elif skipped:
            self.skipped += 1
        elif ok:
            self.success += 1
            if fmt:
                self.formats[fmt] = self.formats.get(fmt, 0) + 1
        else:
            self.failed += 1

    def render(self) -> str:
        parts = [f"进度 {self.done}/{self.total}", f"已下载 {self.success}",
                 f"失败 {self.failed}"]
        if self.skipped:
            parts.append(f"跳过 {self.skipped}")
        if self.too_small:
            parts.append(f"仅摘要/过小 {self.too_small}")
        if self.formats:
            fmt = " ".join(f"{k}={v}" for k, v in sorted(self.formats.items()))
            parts.append(f"格式: {fmt}")
        return "[Download] " + " | ".join(parts)


_FORMAT_BY_SUFFIX = {".pdf": "pdf", ".xml": "xml", ".txt": "txt", ".tex": "tex"}


def format_from_path(path) -> str:
    """按已保存文件的扩展名归一到格式标签（默认 markdown）。"""
    if not path:
        return ""
    return _FORMAT_BY_SUFFIX.get(Path(path).suffix.lower(), "md")


def format_from_result(result: dict) -> str:
    """fetch_fulltext_by_doi 返回的 format → 输出目录格式标签。"""
    fmt = (result.get("format") or "").lower()
    if fmt in ("markdown", "md"):
        return "md"
    if fmt == "text":
        return "txt"
    return fmt if fmt in ("pdf", "xml", "tex") else ""
