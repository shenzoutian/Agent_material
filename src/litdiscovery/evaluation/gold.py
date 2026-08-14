"""Deterministic metrics for expert-annotated retrieval and extraction gold sets."""

from __future__ import annotations

from litdiscovery.agent.filter_agent_pipeline.quality import normalize_doi


def evaluate_retrieval(gold: dict, predictions: list[dict], k: int = 50) -> dict:
    relevant = {normalize_doi(x) for x in gold.get("relevant_dois", []) if normalize_doi(x)}
    ranked = [normalize_doi(p.get("doi") or "") for p in predictions[:k]]
    ranked = [doi for doi in ranked if doi]
    hits = relevant.intersection(ranked)
    return {"k": k, "gold_relevant": len(relevant), "retrieved": len(ranked),
            "hits": len(hits), "recall_at_k": len(hits) / len(relevant) if relevant else 0.0,
            "precision_at_k": len(hits) / len(ranked) if ranked else 0.0,
            "missing_dois": sorted(relevant - hits)}


def evaluate_extraction(gold: list[dict], predictions: list[dict]) -> dict:
    """Report exact-match P/R/F1 for identity, value, unit, conditions and locator."""
    dimensions = {
        "value": ("doi", "material", "property", "value"),
        "unit": ("doi", "material", "property", "value", "unit"),
        "conditions": ("doi", "material", "property", "value", "unit", "conditions"),
        "locator": ("doi", "material", "property", "value", "unit", "locator"),
    }
    return {name: _set_metrics({_key(row, fields) for row in gold},
                               {_key(row, fields) for row in predictions})
            for name, fields in dimensions.items()}


def _key(row: dict, fields: tuple[str, ...]) -> tuple:
    def freeze(value):
        if isinstance(value, dict):
            return tuple(sorted((k, freeze(v)) for k, v in value.items()))
        if isinstance(value, list):
            return tuple(freeze(v) for v in value)
        return str(value or "").strip().lower()
    return tuple(freeze(row.get(field)) for field in fields)


def _set_metrics(gold: set, predicted: set) -> dict:
    tp = len(gold & predicted)
    precision = tp / len(predicted) if predicted else 0.0
    recall = tp / len(gold) if gold else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"gold": len(gold), "predicted": len(predicted), "true_positive": tp,
            "precision": precision, "recall": recall, "f1": f1}
