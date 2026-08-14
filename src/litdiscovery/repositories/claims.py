"""Append-friendly JSONL claim and hypothesis storage."""

import json
from pathlib import Path

from litdiscovery.contracts import Claim, Hypothesis


class ClaimRepository:
    def __init__(self, batch: str | Path):
        root = Path(batch) / "evidence"
        root.mkdir(parents=True, exist_ok=True)
        self.claims_path = root / "claims.jsonl"
        self.hypotheses_path = root / "hypotheses.jsonl"

    def save_claims(self, claims: list[Claim]) -> None:
        self._write(self.claims_path, claims)

    def load_claims(self) -> list[Claim]:
        return [Claim(**row) for row in self._read(self.claims_path)]

    def save_hypotheses(self, hypotheses: list[Hypothesis]) -> None:
        self._write(self.hypotheses_path, hypotheses)

    def load_hypotheses(self) -> list[Hypothesis]:
        return [Hypothesis(**row) for row in self._read(self.hypotheses_path)]

    @staticmethod
    def _write(path: Path, values: list) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            for value in values:
                data = value.model_dump() if hasattr(value, "model_dump") else value.dict()
                handle.write(json.dumps(data, ensure_ascii=False, default=str) + "\n")
        tmp.replace(path)

    @staticmethod
    def _read(path: Path) -> list[dict]:
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
