"""Deterministic corpus quality, deduplication, and diversity controls."""

import math
import re
import unicodedata
from collections import Counter
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

from litdiscovery.common.fs import write_json_atomic
from litdiscovery.config import QUALITY_FLOOR_DEFAULT


def normalize_doi(value: str) -> str:
    value = (value or "").strip().lower()
    return re.sub(r"^https?://(?:dx\.)?doi\.org/", "", value).rstrip(". ")


def normalize_title(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").lower()
    return re.sub(r"[^a-z0-9一-鿿]+", "", value)


DEFAULT_QUALITY_WEIGHTS = {
    "relevance": 0.30,
    "completeness": 0.20,
    "abstract_quality": 0.15,
    "citation_signal": 0.10,
    "recency": 0.10,
    "fulltext_likelihood": 0.15,
}


def quality_assessment(paper: dict, requirement: str = "",
                       weights: dict | None = None) -> dict:
    """Score metadata fitness without pretending citation count is relevance."""
    title = (paper.get("title") or "").strip()
    abstract = (paper.get("abstract") or "").strip()
    doi = normalize_doi(paper.get("doi") or "")
    venue = (paper.get("venue") or "").strip()
    year = _as_int(paper.get("year"))
    citations = max(0, _as_int(paper.get("citation_count")) or 0)
    current_year = datetime.now().year

    completeness = sum((bool(title), bool(doi), bool(venue), bool(year), len(abstract) >= 120)) / 5
    abstract_quality = min(len(abstract) / 800, 1.0)
    citation_signal = min(math.log1p(citations) / math.log1p(500), 1.0)
    recency = 0.0 if not year else max(0.0, 1.0 - max(0, current_year - year) / 20)
    relevance = _token_overlap(requirement, f"{title} {abstract}")
    fulltext_likelihood = _fulltext_likelihood(paper, doi)
    applied = dict(DEFAULT_QUALITY_WEIGHTS)
    if weights:
        applied.update({k: float(v) for k, v in weights.items() if k in applied})
    total = sum(applied.values()) or 1.0
    components = {
        "relevance": relevance, "completeness": completeness,
        "abstract_quality": abstract_quality, "citation_signal": citation_signal,
        "recency": recency, "fulltext_likelihood": fulltext_likelihood,
    }
    score = 100 * sum(applied[k] * components[k] for k in applied) / total
    issues = []
    if not doi:
        issues.append("missing_doi")
    if len(abstract) < 120:
        issues.append("short_or_missing_abstract")
    if not venue:
        issues.append("missing_venue")
    if not year:
        issues.append("missing_year")
    return {
        "score": round(score, 2),
        "relevance": round(relevance, 3),
        "completeness": round(completeness, 3),
        "abstract_quality": round(abstract_quality, 3),
        "citation_signal": round(citation_signal, 3),
        "recency": round(recency, 3),
        "fulltext_likelihood": round(fulltext_likelihood, 3),
        "weights": {k: round(v / total, 3) for k, v in applied.items()},
        "issues": issues,
    }


def deduplicate_papers(papers: list[dict]) -> tuple[list[dict], list[dict]]:
    """Deduplicate by DOI then normalized title, retaining the richest record."""
    groups = {}
    duplicates = []
    for index, paper in enumerate(papers):
        doi = normalize_doi(paper.get("doi") or "")
        title = normalize_title(paper.get("title") or "")
        key = f"doi:{doi}" if doi else f"title:{title}"
        if not doi and not title:
            duplicates.append({"index": index, "reason": "missing_identity"})
            continue
        existing = groups.get(key)
        if existing is None and not doi and title:
            fuzzy_key = next((candidate for candidate in groups
                              if candidate.startswith("title:") and
                              _near_duplicate_title(title, candidate[6:])), None)
            if fuzzy_key:
                key = fuzzy_key
                existing = groups[key]
        if existing is None:
            groups[key] = dict(paper)
            continue
        winner, loser = _richer(existing, paper)
        groups[key] = dict(winner)
        duplicates.append({"index": index, "reason": "duplicate", "key": key,
                           "discarded_title": loser.get("title", "")})
    return list(groups.values()), duplicates


def _near_duplicate_title(a: str, b: str, threshold: float = 0.94) -> bool:
    """Conservative near-duplicate check for records that have no DOI."""
    if min(len(a), len(b)) < 24:
        return False
    return SequenceMatcher(None, a, b, autojunk=False).ratio() >= threshold


def balanced_quality_fill(selected: list[dict], candidates: list[dict], requirement: str,
                          target: int, quality_floor: float = QUALITY_FLOOR_DEFAULT,
                          quotas: dict | None = None) -> list[dict]:
    """Fill a recall floor using quality plus year/source diversity."""
    if len(selected) >= target:
        return selected
    selected_keys = {_identity(p) for p in selected}
    source_counts = Counter((p.get("source") or "unknown") for p in selected)
    decade_counts = Counter(_decade(p.get("year")) for p in selected)
    pool = []
    for paper in candidates:
        if _identity(paper) in selected_keys:
            continue
        assessment = quality_assessment(paper, requirement)
        if assessment["score"] < quality_floor:
            continue
        source = paper.get("source") or "unknown"
        decade = _decade(paper.get("year"))
        diversity_bonus = 5 / (1 + source_counts[source]) + 4 / (1 + decade_counts[decade])
        pool.append((assessment["score"] + diversity_bonus, paper, assessment))
    pool.sort(key=lambda item: item[0], reverse=True)
    out = list(selected)
    quotas = quotas or {"review": 0.10, "classic": 0.20, "recent": 0.20}
    bucket_counts = Counter(_publication_bucket(p) for p in out)
    for bucket, fraction in quotas.items():
        required = math.ceil(target * fraction)
        for _, paper, assessment in list(pool):
            if len(out) >= target or bucket_counts[bucket] >= required:
                break
            if _publication_bucket(paper) != bucket or _identity(paper) in selected_keys:
                continue
            enriched = dict(paper)
            enriched["quality"] = assessment
            enriched["selection_reason"] = f"quota_fill:{bucket}"
            enriched["selection_bucket"] = bucket
            out.append(enriched)
            selected_keys.add(_identity(paper))
            bucket_counts[bucket] += 1
    for _, paper, assessment in pool:
        if _identity(paper) in selected_keys:
            continue
        enriched = dict(paper)
        enriched["quality"] = assessment
        enriched["selection_reason"] = "quality_diversity_fill"
        enriched["selection_bucket"] = _publication_bucket(paper)
        out.append(enriched)
        selected_keys.add(_identity(paper))
        source_counts[paper.get("source") or "unknown"] += 1
        decade_counts[_decade(paper.get("year"))] += 1
        if len(out) >= target:
            break
    return out


def _publication_bucket(paper: dict) -> str:
    title = (paper.get("title") or "").lower()
    if re.search(r"\b(review|survey|perspective|roadmap)\b", title):
        return "review"
    year = _as_int(paper.get("year"))
    current_year = datetime.now().year
    if year and year >= current_year - 5:
        return "recent"
    if year and year <= current_year - 10:
        return "classic"
    return "primary_other"


def build_corpus_audit(papers: list[dict], requirement: str,
                       duplicates: list[dict] | None = None) -> dict:
    rows = []
    for paper in papers:
        rows.append({
            "doi": normalize_doi(paper.get("doi") or ""),
            "title": paper.get("title") or "",
            "year": paper.get("year"),
            "source": paper.get("source") or "unknown",
            "selection_reason": paper.get("selection_reason") or "llm_selected",
            "selection_bucket": paper.get("selection_bucket") or _publication_bucket(paper),
            "quality": paper.get("quality") or quality_assessment(paper, requirement),
        })
    scores = [row["quality"]["score"] for row in rows]
    duplicate_count = len(duplicates or [])
    abstract_count = sum("short_or_missing_abstract" not in row["quality"]["issues"] for row in rows)
    current_year = datetime.now().year
    source_count = len({row["source"] for row in rows})
    has_classic = any((_as_int(row["year"]) or current_year) <= current_year - 10 for row in rows)
    has_recent = any((_as_int(row["year"]) or 0) >= current_year - 5 for row in rows)
    duplicate_rate = duplicate_count / max(len(rows) + duplicate_count, 1)
    abstract_rate = abstract_count / len(rows) if rows else 0.0
    checks = {
        "duplicate_rate_below_2pct": duplicate_rate < 0.02,
        "abstract_usable_rate_above_90pct": abstract_rate > 0.90,
        "at_least_two_sources": source_count >= 2,
        "classic_coverage": has_classic,
        "recent_five_year_coverage": has_recent,
    }
    return {
        "schema_version": 1,
        "requirement": requirement,
        "papers": len(rows),
        "mean_quality_score": round(sum(scores) / len(scores), 2) if scores else 0,
        "with_doi": sum(bool(row["doi"]) for row in rows),
        "with_abstract": abstract_count,
        "abstract_usable_rate": abstract_rate,
        "duplicate_rate": duplicate_rate,
        "source_count": source_count,
        "coverage": {"classic": has_classic, "recent_five_years": has_recent},
        "acceptance_checks": checks,
        "unmet_checks": [name for name, passed in checks.items() if not passed],
        "sources": dict(Counter(row["source"] for row in rows)),
        "decades": dict(Counter(str(_decade(row["year"])) for row in rows)),
        "selection_buckets": dict(Counter(row["selection_bucket"] for row in rows)),
        "duplicates_removed": duplicates or [],
        "records": rows,
    }


def update_cumulative_catalog(papers: list[dict], catalog_path: str | Path,
                              batch: str = "") -> dict:
    """Merge selected metadata into a cross-run catalog without losing provenance."""
    path = Path(catalog_path)
    existing = []
    if path.exists():
        try:
            import json
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            existing = []
    merged = {_identity(p): dict(p) for p in existing if _identity(p)}
    for paper in papers:
        key = _identity(paper)
        old = merged.get(key)
        record = dict(_richer(old, paper)[0]) if old else dict(paper)
        batches = list(dict.fromkeys([*((old or {}).get("batches") or []), batch]))
        record["batches"] = [item for item in batches if item]
        record["quality"] = paper.get("quality") or quality_assessment(paper)
        merged[key] = record
    records = sorted(merged.values(), key=lambda p: (-(p.get("quality") or {}).get("score", 0),
                                                     p.get("title") or ""))
    write_json_atomic(path, records)
    return {"records": len(records), "path": str(path)}


def audit_fulltext_corpus(end_mds: str | Path) -> dict:
    """Measure whether downloaded documents are usable for evidence extraction."""
    root = Path(end_mds)
    records = []
    for folder in sorted(root.iterdir()) if root.is_dir() else []:
        if not folder.is_dir():
            continue
        path = folder / "fulltext.md"
        if not path.exists():
            records.append({"paper": folder.name, "usable": False, "issues": ["missing_fulltext"]})
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        lowered = text.lower()
        issues = []
        if len(text) < 2000:
            issues.append("too_short")
        if "abstract" not in lowered and "摘要" not in text:
            issues.append("missing_abstract_marker")
        if "doi:" not in lowered and "10." not in text[:3000]:
            issues.append("missing_doi_metadata")
        replacement_ratio = text.count("�") / max(len(text), 1)
        if replacement_ratio > 0.001:
            issues.append("encoding_damage")
        records.append({
            "paper": folder.name,
            "path": str(path),
            "chars": len(text),
            "tables": lowered.count("=== table") + lowered.count("|---"),
            "replacement_ratio": round(replacement_ratio, 6),
            "usable": not any(i in issues for i in ("missing_fulltext", "too_short", "encoding_damage")),
            "issues": issues,
        })
    usable = sum(r["usable"] for r in records)
    return {"schema_version": 1, "papers": len(records), "usable": usable,
            "usable_rate": usable / len(records) if records else 0.0, "records": records}


def _identity(paper: dict) -> str:
    doi = normalize_doi(paper.get("doi") or "")
    return f"doi:{doi}" if doi else f"title:{normalize_title(paper.get('title') or '')}"


def _richer(a: dict, b: dict) -> tuple[dict, dict]:
    def richness(p):
        return (len(p.get("abstract") or "") + 80 * bool(p.get("doi"))
                + 20 * bool(p.get("venue")) + 10 * bool(p.get("year")))
    return (a, b) if richness(a) >= richness(b) else (b, a)


def _token_overlap(query: str, text: str) -> float:
    query_tokens = set(re.findall(r"[a-z0-9]{2,}", (query or "").lower()))
    for segment in re.findall(r"[一-鿿]{2,}", query or ""):
        query_tokens.add(segment)
        for width in (2, 3):
            query_tokens.update(segment[i:i + width] for i in range(len(segment) - width + 1))
    if not query_tokens:
        return 0.0
    haystack = (text or "").lower()
    return sum(token in haystack for token in query_tokens) / len(query_tokens)


def _fulltext_likelihood(paper: dict, doi: str) -> float:
    """Estimate acquisition likelihood without treating it as scientific relevance."""
    locations = paper.get("locations") or paper.get("oa_locations") or []
    best_oa = paper.get("best_oa_location") or {}
    if (paper.get("fulltext_url") or paper.get("oa_url") or paper.get("pdf_url")
            or paper.get("is_oa") or best_oa.get("pdf_url")
            or any((item or {}).get("pdf_url") for item in locations)):
        return 1.0
    source = (paper.get("source") or "").lower()
    if "memory" in source and paper.get("has_structured"):
        return 1.0
    if doi.startswith("10.48550/"):
        return 1.0
    if doi.endswith(".s001") or re.match(r"10\.1007/978-", doi):
        return 0.15
    if doi.startswith("10.1016/"):
        return 0.75
    return 0.45 if doi else 0.1


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _decade(year) -> int:
    value = _as_int(year)
    return value // 10 * 10 if value else 0
