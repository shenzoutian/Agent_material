"""Build an auditable claim store from normalized extraction outputs."""

import csv
import hashlib
import json
from pathlib import Path

from litdiscovery.common.fs import write_json_atomic
from litdiscovery.contracts import Claim, EvidenceLocator, VerificationStatus
from litdiscovery.paths import BatchPaths
from litdiscovery.repositories import ClaimRepository


class EvidenceService:
    def materialize(self, batch: str | Path) -> dict:
        paths = BatchPaths.from_value(batch)
        source = paths.gap / "material_props.csv"
        if not source.exists():
            raise FileNotFoundError(f"normalized property table not found: {source}")

        claims = []
        with source.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                claim = self._row_to_claim(row, source)
                if claim is not None:
                    claims.append(claim)

        repository = ClaimRepository(paths.root)
        repository.save_claims(claims)
        traced = sum(1 for claim in claims if any(e.quote or e.page or e.table for e in claim.evidence))
        report = {
            "claims": len(claims),
            "traceable_claims": traced,
            "traceability_rate": traced / len(claims) if claims else 0.0,
            "needs_review": sum(c.verification_status == VerificationStatus.NEEDS_REVIEW for c in claims),
            "source": str(source),
            "claim_store": str(repository.claims_path),
        }
        condition_keys = ("sample_form", "phase_state", "measurement_method", "value_origin")
        report["condition_coverage"] = {
            key: (sum(bool(c.conditions.get(key)) for c in claims) / len(claims) if claims else 0.0)
            for key in condition_keys
        }
        write_json_atomic(paths.evidence / "quality.json", report)
        write_json_atomic(paths.evidence / "needs_review.json", [
            c.model_dump(mode="json") for c in claims
            if c.verification_status == VerificationStatus.NEEDS_REVIEW
        ])
        return report

    @staticmethod
    def _row_to_claim(row: dict, source: Path) -> Claim | None:
        subject = (row.get("material_raw") or row.get("material_family") or "").strip()
        predicate = (row.get("property_key") or row.get("property_symbol") or "").strip()
        value = row.get("value")
        if not subject or not predicate or value in (None, ""):
            return None
        doi = (row.get("doi") or "").strip()
        identity = json.dumps([doi, subject, predicate, value, row.get("unit"), row.get("temp_K")],
                              ensure_ascii=False, separators=(",", ":"))
        claim_id = "claim-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        locator = EvidenceLocator(
            doi=doi,
            document_uri=str(source),
            section=(row.get("evidence_section") or "").strip(),
            page=EvidenceService._page(row.get("evidence_page")),
            table=(row.get("evidence_table") or "").strip(),
            row=(str(row.get("evidence_table_row") or "")).strip(),
            quote=(row.get("evidence_quote") or "").strip(),
        )
        conditions = {}
        if row.get("temp_K") not in (None, ""):
            conditions["temperature_K"] = row["temp_K"]
        for key in ("sample_form", "phase_state", "measurement_method", "heating_rate",
                    "film_thickness", "pulse_type", "pulse_width",
                    "crystallization_definition", "value_origin", "endurance_basis",
                    "evidence_table_row", "evidence_table_column"):
            if row.get(key) not in (None, ""):
                conditions[key] = row[key]
        traceable = bool(locator.quote or locator.page or locator.table or locator.row)
        return Claim(
            claim_id=claim_id,
            subject=subject,
            predicate=predicate,
            value=value,
            unit=(row.get("unit") or "").strip(),
            conditions=conditions,
            evidence=[locator],
            extraction_method=(row.get("source") or "structured"),
            confidence=0.75 if traceable else 0.4,
            verification_status=(VerificationStatus.UNVERIFIED if traceable
                                 else VerificationStatus.NEEDS_REVIEW),
        )

    @staticmethod
    def _page(value) -> int | None:
        try:
            return int(float(value)) if value not in (None, "") else None
        except (TypeError, ValueError):
            return None
