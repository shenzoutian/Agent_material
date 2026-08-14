from litdiscovery.agent.filter_agent_pipeline import repositories
from litdiscovery.agent.researcher_agent_pipeline import search


class _JsonResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def test_public_repository_adapters(monkeypatch):
    def fake_get(url, **kwargs):
        if "europepmc" in url:
            return _JsonResponse({"resultList": {"result": [{"pmcid": "PMC123"}]}})
        if "zenodo" in url:
            return _JsonResponse({"hits": {"hits": [{"files": [
                {"key": "paper.pdf", "links": {"content": "https://zenodo/paper.pdf"}}
            ]}]}})
        return _JsonResponse({"response": {"docs": [
            {"fileMain_s": "https://hal/paper.pdf"}
        ]}})

    monkeypatch.setattr(repositories.requests, "get", fake_get)
    candidates = repositories.discover_repository_candidates("10.1/test")
    assert [item.provider for item in candidates] == ["pmc", "zenodo", "hal"]


def test_openalex_search_preserves_oa_locations(monkeypatch):
    payload = {"results": [{
        "title": "Open paper", "publication_year": 2025,
        "doi": "https://doi.org/10.1/open", "cited_by_count": 2,
        "primary_location": {"pdf_url": "https://repo/primary.pdf",
                             "source": {"display_name": "Repository"}},
        "locations": [{"pdf_url": "https://repo/primary.pdf"},
                      {"pdf_url": "https://repo/mirror.pdf"}],
        "open_access": {"is_oa": True}, "abstract_inverted_index": {},
    }]}
    monkeypatch.setattr(search, "_get", lambda *args, **kwargs: _JsonResponse(payload))
    paper = search._search_openalex_query("phase change", 10)[0]
    assert paper["best_oa_location"]["pdf_url"] == "https://repo/primary.pdf"
    assert len(paper["oa_locations"]) == 2
    assert paper["is_oa"] is True
