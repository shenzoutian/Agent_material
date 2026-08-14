"""Public extractor pipeline facade."""

from litdiscovery.contracts.agents import ExtractorRequest, ExtractorResult
from .extraction.api import run_extract_batch


def run(request: ExtractorRequest) -> ExtractorResult:
    if not isinstance(request, ExtractorRequest):
        raise TypeError("request must be ExtractorRequest")
    base_dir = request.base_dir
    if base_dir is None and request.batch:
        base_dir = str(request.batch) + "/end_mds"
    payload = run_extract_batch(
        base_dir=base_dir, domain=request.domain, limit=request.limit,
        domain_registry=dict(request.domain_registry) if request.domain_registry else None,
        min_fulltext_usable_rate=request.min_fulltext_usable_rate,
        allow_low_quality=request.allow_low_quality,
    )
    return ExtractorResult(
        payload["base_dir"], payload["completed"], payload["failed"],
        payload["limit"], payload["quality_report"],
    )

