"""Post-extraction normalization for phase-change material evidence records."""

import re

PULSE_TYPES = {"set": "SET", "reset": "RESET", "unknown": "unknown"}
PHASE_STATES = {
    "amorphous": "amorphous", "non-crystalline": "amorphous",
    "crystalline": "crystalline", "cubic": "cubic", "hexagonal": "hexagonal",
    "mixed": "mixed", "unknown": "unknown",
}
VALUE_ORIGINS = {"experimental", "calculated", "cited", "unknown"}


def parse_composition(name: str) -> dict:
    """Retain the raw composition and expose common PCM aliases/components."""
    raw = (name or "").strip()
    upper = raw.upper()
    alias = "GST" if re.search(r"\bGST\b|GE\s*2?SB\s*2?TE\s*5?", upper) else ""
    elements = re.findall(r"[A-Z][a-z]?", re.sub(r"\([^)]*\)", "", raw))
    dopants = []
    match = re.search(r"(?:doped with|doped|:|\+|-)\s*([A-Z][a-z]?)\b", raw, re.I)
    if match:
        dopants.append(match.group(1))
    return {"raw": raw, "alias": alias, "elements": list(dict.fromkeys(elements)),
            "dopants": dopants}


def normalize_phasechange_output(payload: dict) -> dict:
    for material in payload.get("materials", []) if isinstance(payload, dict) else []:
        material["composition"] = parse_composition(material.get("name") or "")
        for field, entries in list(material.items()):
            if not isinstance(entries, list) or field in {"phase_states"}:
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                pulse = str(entry.get("pulse_type") or "unknown").lower()
                entry["pulse_type"] = PULSE_TYPES.get(pulse, "unknown")
                phase = str(entry.get("phase_state") or "unknown").lower()
                entry["phase_state"] = PHASE_STATES.get(phase, "unknown")
                origin = str(entry.get("value_origin") or "unknown").lower()
                entry["value_origin"] = origin if origin in VALUE_ORIGINS else "unknown"
                if field == "crystallization_temperature":
                    definition = str(entry.get("crystallization_definition") or "unknown").lower()
                    entry["crystallization_definition"] = (
                        definition if definition in {"onset", "peak", "unknown"} else "unknown")
                if field == "endurance_cycles":
                    basis = str(entry.get("endurance_basis") or "unknown").lower()
                    entry["endurance_basis"] = basis if basis in {"measured", "extrapolated", "unknown"} else "unknown"
    return payload
