from pathlib import Path

from litdiscovery.agent.filter_agent_pipeline import fulltext, pdf_fetch
from litdiscovery.agent.extractor_agent_pipeline import preprocess


def _disable_other_sources(monkeypatch):
    monkeypatch.setattr(fulltext, "_fetch_elsevier", lambda doi: None)
    monkeypatch.setattr(fulltext, "_fetch_core", lambda doi: None)
    monkeypatch.setattr(fulltext, "_fetch_europepmc", lambda doi: None)


def test_markdown_source_is_staged_before_preprocessing(tmp_path, monkeypatch):
    doi = "10.1000/source-md"
    text = "# Title\n\n" + ("full text paragraph\n" * 500)
    monkeypatch.setattr(
        fulltext,
        "_fetch_arxiv",
        lambda value: {
            "source": "oa_markdown",
            "format": "markdown",
            "text": text,
            "status": "ok",
        },
    )
    _disable_other_sources(monkeypatch)

    result = fulltext.fetch_fulltext_by_doi(
        doi, tmp_path / "end_mds", format_root=tmp_path
    )

    staged = tmp_path / "markdowns" / "10.1000_source-md.md"
    assert result["path"] == str(staged)
    assert staged.read_text(encoding="utf-8") == text
    assert not (tmp_path / "end_mds" / "10.1000_source-md" / "fulltext.md").exists()


def test_too_small_marker_moves_to_processed_document(tmp_path, monkeypatch):
    doi = "10.1000/abstract"
    monkeypatch.setattr(
        fulltext,
        "_fetch_arxiv",
        lambda value: {
            "source": "oa_markdown",
            "format": "markdown",
            "text": "# Title\n\nShort abstract.",
            "status": "abstract-only",
        },
    )
    _disable_other_sources(monkeypatch)
    monkeypatch.setattr(fulltext, "_fallback_pdf", lambda doi, root: None)

    result = fulltext.fetch_fulltext_by_doi(
        doi, tmp_path / "end_mds", format_root=tmp_path
    )
    source_marker = tmp_path / "markdowns" / "10.1000_abstract.too_small"
    assert result["status"] == "too_small"
    assert source_marker.exists()
    assert not (tmp_path / "end_mds").exists()

    def fake_preprocess(input_dir, output_dir, stages):
        processed = Path(output_dir) / "10.1000_abstract"
        processed.mkdir(parents=True)
        (processed / "fulltext.md").write_text("cleaned", encoding="utf-8")

    monkeypatch.setattr(preprocess, "run_preprocess", fake_preprocess)
    preprocess.run_to_markdown(tmp_path)
    assert (tmp_path / "end_mds" / "10.1000_abstract" / ".too_small").exists()


def test_existing_markdown_is_not_overwritten_by_raw_format(tmp_path, monkeypatch):
    markdowns = tmp_path / "markdowns"
    xmls = tmp_path / "xmls"
    markdowns.mkdir()
    xmls.mkdir()
    staged = markdowns / "paper.md"
    staged.write_text("preferred markdown", encoding="utf-8")
    (xmls / "paper.xml").write_text("<article>raw xml</article>", encoding="utf-8")
    monkeypatch.setattr(preprocess, "run_preprocess", lambda *args, **kwargs: None)

    preprocess.run_to_markdown(tmp_path)

    assert staged.read_text(encoding="utf-8") == "preferred markdown"


def test_batch_cache_matches_the_same_doi_only(tmp_path, monkeypatch):
    pdfs = tmp_path / "pdfs"
    markdowns = tmp_path / "markdowns"
    xmls = tmp_path / "xmls"
    pdfs.mkdir()
    markdowns.mkdir()
    xmls.mkdir()
    (markdowns / "10.1000_cached.md").write_text("cached", encoding="utf-8")
    (xmls / "unrelated.xml").write_text("raw", encoding="utf-8")
    fetched = []

    def fake_download(doi, output_dir):
        fetched.append(doi)
        return output_dir / "downloaded.pdf"

    monkeypatch.setattr(pdf_fetch, "download_any_format_by_doi", fake_download)
    success, failed = pdf_fetch.download_batch(
        ["10.1000/cached", "10.1000/missing"], pdfs, skip_existing=True
    )

    assert (success, failed) == (2, 0)
    assert fetched == ["10.1000/missing"]
