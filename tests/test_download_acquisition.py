from litdiscovery.agent.filter_agent_pipeline import pdf_fetch
from litdiscovery.agent.filter_agent_pipeline.acquisition import (
    DownloadCandidate, ProviderCircuitBreaker, classify_access, classify_response,
    is_pdf_prefix, validate_pdf_bytes,
)
from litdiscovery.agent.filter_agent_pipeline.quality import quality_assessment


class _Response:
    def __init__(self, status, content_type, payload=b""):
        self.status_code = status
        self.headers = {"Content-Type": content_type}
        self._payload = payload

    def iter_content(self, chunk_size=65536):
        yield self._payload


def test_response_classification_and_pdf_signature():
    assert classify_response(403, "text/html") == ("access_denied", False)
    assert classify_response(503, "text/plain") == ("provider_unavailable", True)
    assert is_pdf_prefix(b"%PDF-1.7\n")
    assert not is_pdf_prefix(b"<html>login</html>")


def test_download_falls_through_non_pdf_candidate(tmp_path, monkeypatch):
    candidates = [DownloadCandidate("first", "https://one"),
                  DownloadCandidate("second", "https://two")]
    monkeypatch.setattr(pdf_fetch, "get_pdf_candidates", lambda doi, paper=None: candidates)
    valid_pdf = (b"%PDF-1.7\n1 0 obj<</Type /Page>>endobj\n" +
                 b"x" * 12000 + b"\n%%EOF")
    responses = iter([
        _Response(200, "text/html", b"<html>login</html>"),
        _Response(200, "application/pdf", valid_pdf),
    ])
    monkeypatch.setattr(pdf_fetch.requests, "get", lambda *args, **kwargs: next(responses))

    path = pdf_fetch.download_pdf_by_doi("10.1/test", tmp_path)

    assert path is not None
    assert path.read_bytes().startswith(b"%PDF-")


def test_circuit_breaker_recovers_after_cooldown(monkeypatch):
    breaker = ProviderCircuitBreaker(threshold=2, cooldown_seconds=10)
    now = [0.0]
    monkeypatch.setattr("litdiscovery.agent.filter_agent_pipeline.acquisition.time.monotonic",
                        lambda: now[0])
    breaker.record("core", True)
    breaker.record("core", True)
    now[0] = 5.0
    assert not breaker.available("core")
    now[0] = 11.0
    assert breaker.available("core")


def test_access_score_penalizes_supplement_and_rewards_oa_location():
    supplement = quality_assessment({"doi": "10.1021/example.s001", "title": "x"})
    oa = quality_assessment({"doi": "10.1021/example", "title": "x",
                             "best_oa_location": {"pdf_url": "https://repo/p.pdf"}})
    assert supplement["fulltext_likelihood"] == 0.15
    assert oa["fulltext_likelihood"] == 1.0


def test_access_class_and_pdf_structure_validation():
    assert classify_access({"pdf_url": "https://repo/p.pdf"}) == "download_ready"
    assert classify_access({"is_oa": True}) == "metadata_only"
    assert classify_access({"doi": "10.1/x"}) == "restricted"
    valid = b"%PDF-1.7\n1 0 obj<</Type /Page>>endobj\n%%EOF"
    assert validate_pdf_bytes(valid) == (True, "ok", 1)
    assert validate_pdf_bytes(b"%PDF-1.7\n")[:2] == (False, "missing_pdf_eof")


def test_openalex_locations_precede_discovery(monkeypatch):
    for name in ("_arxiv_pdf_url", "_try_unpaywall", "_try_semantic_scholar",
                 "_try_core", "_try_direct"):
        monkeypatch.setattr(pdf_fetch, name, lambda doi: None)
    monkeypatch.setattr(
        "litdiscovery.agent.filter_agent_pipeline.repositories.discover_repository_candidates",
        lambda *args, **kwargs: [],
    )
    candidates = pdf_fetch.get_pdf_candidates("10.1/x", {
        "best_oa_location": {"pdf_url": "https://repo/direct.pdf",
                             "landing_page_url": "https://repo/article"}
    })
    assert [(c.provider, c.kind) for c in candidates] == [
        ("openalex", "pdf"), ("openalex_landing", "landing")]


def test_landing_page_meta_pdf_is_followed(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf_fetch, "get_pdf_candidates", lambda doi, paper=None: [
        DownloadCandidate("landing", "https://repo/article", "landing")])
    valid_pdf = (b"%PDF-1.7\n1 0 obj<</Type /Page>>endobj\n" +
                 b"x" * 12000 + b"\n%%EOF")
    first = _Response(200, "text/html",
                      b'<meta name="citation_pdf_url" content="/paper.pdf">')
    first.url = "https://repo/article"
    responses = iter([first, _Response(200, "application/pdf", valid_pdf)])
    monkeypatch.setattr(pdf_fetch.requests, "get", lambda *args, **kwargs: next(responses))
    assert pdf_fetch.download_pdf_by_doi("10.1/x", tmp_path) is not None
