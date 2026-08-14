"""Normalize relevant historical-corpus records for the retrieval handoff."""

from litdiscovery.memory import ingest, search


def search_memory_papers(requirement: str, limit: int = 100) -> list[dict]:
    records = ingest()
    hits = search(requirement, k=limit, records=records)
    out = []
    for item in hits:
        doi = (item.get("doi") or "").strip().lower()
        if not doi:
            continue
        out.append({
            "doi": doi,
            "title": item.get("title") or "",
            "year": item.get("year"),
            "abstract": item.get("abstract") or "",
            "venue": item.get("venue") or "",
            "citation_count": item.get("citation_count", 0),
            "source": "memory",
            "source_batch": item.get("batch") or "",
            "has_structured": bool(item.get("has_structured")),
        })
    return out
