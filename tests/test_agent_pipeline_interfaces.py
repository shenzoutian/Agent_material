"""Contract tests for the uniform run(request) -> result Agent API."""

import importlib

import pytest

from litdiscovery.contracts.agents import (
    ExtractorRequest, ExtractorResult, FilterRequest, FilterResult,
    ResearchGapRequest, ResearchGapResult, ResearcherRequest, ResearcherResult,
    ValidateRequest, ValidateResult,
)


@pytest.mark.parametrize("package,request_type,result_type", [
    ("researcher_agent_pipeline", ResearcherRequest, ResearcherResult),
    ("filter_agent_pipeline", FilterRequest, FilterResult),
    ("extractor_agent_pipeline", ExtractorRequest, ExtractorResult),
    ("research_gap_agent", ResearchGapRequest, ResearchGapResult),
    ("validate_agent", ValidateRequest, ValidateResult),
])
def test_every_agent_exports_uniform_interface(package, request_type, result_type):
    module = importlib.import_module(f"litdiscovery.agent.{package}")
    assert callable(module.run)
    assert request_type.__name__ in module.__all__
    assert result_type.__name__ in module.__all__


def test_researcher_run_merges_and_deduplicates(monkeypatch):
    pipeline = importlib.import_module("litdiscovery.agent.researcher_agent_pipeline.pipeline")
    monkeypatch.setattr(pipeline, "search_papers_async", lambda *a, **k: _async([
        {"doi": "10.1/a", "source": "online"},
    ]))
    monkeypatch.setattr(pipeline, "search_memory_papers", lambda *a, **k: [
        {"doi": "10.1/a", "source": "memory"}, {"doi": "10.1/b", "source": "memory"},
    ])
    result = pipeline.run(ResearcherRequest("phase change", keywords=("PCM",)))
    assert isinstance(result, ResearcherResult)
    assert len(result.papers) == 2
    assert result.keywords == ("PCM",)


async def _async(value):
    return value


def test_filter_run_maps_selection(monkeypatch):
    pipeline = importlib.import_module("litdiscovery.agent.filter_agent_pipeline.pipeline")
    monkeypatch.setattr(pipeline, "select_papers", lambda *a, **k: ([{"doi": "10.1/a"}], "score"))
    result = pipeline.run(FilterRequest("phase change", [{"doi": "10.1/a"}]))
    assert isinstance(result, FilterResult)
    assert result.reason == "score"


def test_extractor_run_maps_legacy_payload(monkeypatch):
    pipeline = importlib.import_module("litdiscovery.agent.extractor_agent_pipeline.pipeline")
    monkeypatch.setattr(pipeline, "run_extract_batch", lambda **k: {
        "base_dir": "end_mds", "completed": 2, "failed": 1,
        "limit": 3, "quality_report": "quality.json",
    })
    result = pipeline.run(ExtractorRequest(base_dir="end_mds", limit=3))
    assert result == ExtractorResult("end_mds", 2, 1, 3, "quality.json")


def test_gap_run_maps_legacy_payload(monkeypatch):
    pipeline = importlib.import_module("litdiscovery.agent.research_gap_agent.pipeline")
    monkeypatch.setattr(pipeline, "run_gap", lambda **k: {
        "batch": "batch", "gap_out": "gap", "n_detected": 3,
        "n_verdicts": 2, "accepted": 1, "gaps": [{"id": "g1"}],
    })
    result = pipeline.run(ResearchGapRequest(batch="batch", skip_llm=True))
    assert isinstance(result, ResearchGapResult)
    assert result.accepted == 1


def test_validate_run_maps_legacy_payload(monkeypatch):
    pipeline = importlib.import_module("litdiscovery.agent.validate_agent.pipeline")
    monkeypatch.setattr(pipeline, "run_validate", lambda *a, **k: {
        "n_validated": 2, "n_available": 1,
        "reports": ["a.md"], "summary_path": "validation",
    })
    result = pipeline.run(ValidateRequest(("GST", "GeTe"), delay=0))
    assert result == ValidateResult(2, 1, ("a.md",), "validation")


def test_contracts_reject_invalid_limits():
    with pytest.raises(ValueError):
        ResearcherRequest("topic", keyword_count=0)
    with pytest.raises(ValueError):
        ExtractorRequest(limit=-1)
    with pytest.raises(ValueError):
        ValidateRequest(delay=-0.1)
