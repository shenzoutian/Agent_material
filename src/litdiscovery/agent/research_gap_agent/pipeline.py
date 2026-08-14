"""Public research-gap pipeline facade."""

from litdiscovery.contracts.agents import ResearchGapRequest, ResearchGapResult
from .api import run_gap


def run(request: ResearchGapRequest) -> ResearchGapResult:
    if not isinstance(request, ResearchGapRequest):
        raise TypeError("request must be ResearchGapRequest")
    payload = run_gap(
        batch=request.batch, skip_llm=request.skip_llm, limit=request.limit,
        concept_llm=request.concept_llm, judge_llm=request.judge_llm,
    )
    return ResearchGapResult(
        payload["batch"], payload["gap_out"], payload["n_detected"],
        payload["n_verdicts"], payload["accepted"], tuple(payload["gaps"]),
    )

