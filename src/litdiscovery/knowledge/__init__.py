"""Local knowledge indexing and deterministic lexical retrieval."""

import json
import re
import unicodedata
from pathlib import Path

from litdiscovery.common.fs import write_text_atomic
from litdiscovery.paths import KNOWLEDGE_ROOT, read_handoff

KNOWLEDGE_STORE = KNOWLEDGE_ROOT


def index_batch(batch, store=None) -> dict:
    """把批次产物清洗为知识条目并落盘（预留：当前做清洗与 JSONL 落盘，不做向量化）。

    返回 {"n_docs": n, "store": store_path, "skipped": [...]}。
    """
    store = Path(store) if store else KNOWLEDGE_STORE
    store.mkdir(parents=True, exist_ok=True)
    batch = Path(batch)

    docs = []
    skipped = []
    doi_json = read_handoff(batch, "doi_reach_results.json")
    if doi_json.exists():
        data = json.loads(doi_json.read_text(encoding="utf-8"))
        papers = data if isinstance(data, list) else next(
            (v for v in data.values() if isinstance(v, list)), [])
        for p in papers:
            docs.append({
                "type": "paper", "doi": p.get("doi"), "title": p.get("title"),
                "year": p.get("year"), "venue": p.get("venue"),
                "abstract": p.get("abstract"), "cited_by": p.get("citation_count"),
                "source_batch": batch.name,
            })

    gaps_json = batch / "gap_output" / "research_gaps.json"
    if gaps_json.exists():
        data = json.loads(gaps_json.read_text(encoding="utf-8"))
        for g in data.get("gaps", []):
            docs.append({
                "type": "gap", "statement": g.get("statement"),
                "gap_type": g.get("type"), "confidence": g.get("confidence"),
                "evidence_doi": g.get("evidence_doi", []),
                "source_batch": batch.name,
            })

    report_json = batch / "report.json"
    if report_json.exists():
        docs.append({
            "type": "report", "content": report_json.read_text(encoding="utf-8"),
            "source_batch": batch.name,
        })

    if docs:
        out_file = store / f"{batch.name}.jsonl"
        write_text_atomic(out_file, "".join(
            json.dumps(d, ensure_ascii=False) + "\n" for d in docs))
        print(f"[Knowledge] 沉淀 {len(docs)} 条知识条目 → {out_file}")
    else:
        print(f"[Knowledge] 批次 {batch.name} 无可沉淀产物（缺 doi_reach_results.json 等）")

    return {"n_docs": len(docs), "store": str(store), "skipped": skipped}


def search(query: str, k: int = 5) -> list:
    """Search indexed papers, gaps, and reports without an external vector DB."""
    query_terms = _terms(query)
    if not query_terms or not KNOWLEDGE_STORE.is_dir():
        return []
    hits = []
    for path in KNOWLEDGE_STORE.glob("*.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except (TypeError, json.JSONDecodeError):
                continue
            haystack = _normalize(" ".join(str(v) for v in record.values()))
            matched = [term for term in query_terms if term in haystack]
            if not matched:
                continue
            exact_doi = bool(record.get("doi") and _normalize(str(record["doi"])) in _normalize(query))
            record = dict(record)
            record["score"] = len(matched) + (10 if exact_doi else 0)
            record["matched_terms"] = matched
            record["index"] = path.name
            hits.append(record)
    hits.sort(key=lambda item: (-item["score"], str(item.get("title") or item.get("statement") or "")))
    return hits[:max(0, k)]


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").lower()
    return re.sub(r"\s+", " ", value)


def _terms(value: str) -> list[str]:
    normalized = _normalize(value)
    latin = re.findall(r"[a-z0-9][a-z0-9_.:/-]{1,}", normalized)
    chinese = re.findall(r"[一-鿿]{2,}", normalized)
    return list(dict.fromkeys(latin + chinese))
