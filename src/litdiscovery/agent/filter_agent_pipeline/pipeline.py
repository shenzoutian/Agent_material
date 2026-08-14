"""Public filter pipeline facade."""

import json

from litdiscovery.contracts.agents import FilterRequest, FilterResult
from litdiscovery.paths import handoff_path, resolve_batch
from .choose import save_choose_results, select_papers
from .fulltext import fetch_fulltext_by_doi


def run(request: FilterRequest) -> FilterResult:
    """Score/select candidates and optionally acquire their full text."""
    if not isinstance(request, FilterRequest):
        raise TypeError("request must be FilterRequest")
    selected, reason = select_papers(
        request.requirement, list(request.papers), request.min_keep,
        quality_floor=request.quality_floor,
    )
    attempts = []
    output_path = None
    batch = None
    if request.batch:
        batch_path = resolve_batch(request.batch)
        batch = str(batch_path)
        save_choose_results(selected, reason, batch_path, request.min_keep,
                            requirement=request.requirement)
        output_path = str(handoff_path(batch_path, "doi_choose_results.json"))
        if request.acquire_fulltext:
            from .acquisition import DownloadAudit, classify_access
            from litdiscovery.agent.extractor_agent_pipeline.preprocess import run_to_markdown
            audit = DownloadAudit(batch_path)
            for paper in selected:
                doi = str(paper.get("doi") or "").strip()
                if doi:
                    result = fetch_fulltext_by_doi(
                        doi, batch_path / "end_mds", format_root=str(batch_path),
                        audit=audit, paper=dict(paper))
                    result["access_class"] = classify_access(dict(paper))
                    attempts.append(result)
            attempts_path = handoff_path(batch_path, "fulltext_attempts.json")
            attempts_path.write_text(json.dumps(attempts, ensure_ascii=False, indent=2), encoding="utf-8")
            run_to_markdown(batch_path)
            summary = audit.summarize()
            summary["access_classes"] = {}
            for item in attempts:
                access_class = item.get("access_class") or "unknown"
                summary["access_classes"][access_class] = (
                    summary["access_classes"].get(access_class, 0) + 1)
            audit.summary_path.write_text(
                json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    elif request.acquire_fulltext:
        raise ValueError("batch is required when acquire_fulltext=True")
    return FilterResult(tuple(selected), reason, tuple(attempts), batch, output_path)
