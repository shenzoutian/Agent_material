"""Task-aware, deterministic evidence localization for structured extraction."""

import re

from litdiscovery.agent.extractor_agent_pipeline.extraction.domain_registry import normalize_domain


_TASK_SECTION_TERMS = {
    "process": ("method", "material", "experiment", "experimental", "synthesis",
                "fabrication", "preparation", "procedure", "processing"),
    "property": ("result", "discussion", "analysis", "performance", "property",
                 "characterization", "measurement"),
    "structure": ("result", "discussion", "analysis", "characterization", "structure",
                  "crystal", "microstructure"),
}


def _terms_for_task(domain, task: str) -> set[str]:
    dom = normalize_domain(domain)
    terms = {"table", "fig", "measured", "calculated", "experiment"}
    for pid, spec in dom.get("properties", {}).items():
        terms.update((pid, spec.get("field"), spec.get("symbol"), spec.get("label")))
        terms.update(spec.get("aliases") or [])
    if task == "structure":
        terms.update(("crystal", "lattice", "space group", "xrd", "raman", "sem", "tem"))
    if task == "process":
        terms.update(("anneal", "sinter", "deposit", "sputter", "grown", "synthes",
                      "fabricat", "substrate", "precursor", "temperature", "pressure"))
    if ("crystallization_temperature" in dom.get("properties", {})
            or "phasechange" in str(domain).lower()):
        terms.update(("amorphous", "crystalline", "set", "reset", "dsc", "resistivity",
                      "crystallization", "endurance", "retention", "threshold", "pulse"))
    return {str(term).lower() for term in terms if term and len(str(term)) > 1}


def _sections(fulltext: str) -> list[tuple[str, str]]:
    parts = re.split(r"(?=^===\s*.+?\s*===$)", fulltext, flags=re.M)
    sections = []
    for part in parts:
        match = re.match(r"^===\s*(.+?)\s*===\s*\n?", part)
        if match:
            sections.append((match.group(1), part[match.end():]))
        elif part.strip():
            sections.append(("Preamble", part))
    return sections or [("Preamble", fulltext)]


def select_task_passages(fulltext: str, domain, task: str,
                         max_chars: int = 30000) -> str:
    """Return compact, task-specific evidence with section and page provenance.

    Process extraction prioritizes Methods/Experimental sections; property and
    structure extraction prioritize Results/Analysis sections.  Keyword-bearing
    paragraphs are selected first, then the preferred sections are used as a
    recall-preserving fallback instead of re-sending the entire paper.
    """
    if task not in _TASK_SECTION_TERMS:
        raise ValueError(f"unknown evidence task: {task}")
    terms = _terms_for_task(domain, task)
    preferred = _TASK_SECTION_TERMS[task]
    prioritized = []
    fallback = []
    for title, body in _sections(fulltext):
        is_preferred = any(term in title.lower() for term in preferred)
        for paragraph in re.split(r"\n\s*\n", body):
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            located = f"[SECTION: {title}]\n{paragraph}"
            if any(term in paragraph.lower() for term in terms):
                (prioritized if is_preferred else fallback).append(located)
            elif is_preferred:
                fallback.append(located)
    selected = prioritized + fallback
    if not selected:
        selected = [f"[SECTION: {title}]\n{body.strip()}" for title, body in _sections(fulltext)]
    return "\n\n".join(selected)[:max_chars]


def select_evidence_passages(fulltext: str, domain, max_chars: int = 60000) -> str:
    """Select property-bearing paragraphs while preserving section labels.

    This is a recall-oriented locator, not an extractor. If it cannot identify enough
    evidence it returns the original text, avoiding a silent recall regression.
    """
    return select_task_passages(fulltext, domain, "property", max_chars)
