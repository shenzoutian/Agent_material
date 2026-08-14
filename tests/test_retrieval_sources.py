from litdiscovery.agent.researcher_agent_pipeline.deep_research import extract_dois, run_deep_research
from litdiscovery.agent.filter_agent_pipeline.quality import quality_assessment


def test_deep_research_is_optional_without_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = run_deep_research("phase change memory")
    assert result == {"status": "disabled", "reason": "missing_openai_api_key", "papers": []}


def test_deep_research_extracts_and_deduplicates_dois():
    response = {"output": [{"content": [{
        "text": "Evidence: https://doi.org/10.1038/NCOMMS5086 and 10.1038/ncomms5086.",
        "annotations": [{"url": "https://doi.org/10.1016/j.actamat.2020.01.001"}],
    }]}]}
    assert extract_dois(response) == [
        "10.1038/ncomms5086", "10.1016/j.actamat.2020.01.001"
    ]


def test_quality_score_exposes_weighted_download_signal():
    paper = {"doi": "10.48550/arxiv.1234.5678", "title": "ScAlN piezoelectric film",
             "abstract": "ScAlN piezoelectric film " * 20, "venue": "arXiv", "year": 2026}
    quality = quality_assessment(paper, "ScAlN piezoelectric film")
    assert quality["fulltext_likelihood"] == 1.0
    assert abs(sum(quality["weights"].values()) - 1.0) < 0.01
