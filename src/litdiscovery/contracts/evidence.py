"""Evidence-first scientific claim and hypothesis contracts."""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class VerificationStatus(str, Enum):
    UNVERIFIED = "unverified"
    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    INCOMPARABLE = "incomparable"
    NOT_FOUND = "not_found"
    NEEDS_REVIEW = "needs_review"


class EvidenceLocator(BaseModel):
    doi: str = ""
    document_uri: str = ""
    section: str = ""
    page: int | None = None
    table: str = ""
    row: str = ""
    quote: str = ""


class Claim(BaseModel):
    claim_id: str
    subject: str
    predicate: str
    value: Any
    unit: str = ""
    conditions: dict[str, Any] = Field(default_factory=dict)
    evidence: list[EvidenceLocator] = Field(default_factory=list)
    extraction_method: str = ""
    model: str = ""
    prompt_version: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED


class Hypothesis(BaseModel):
    hypothesis_id: str
    statement: str
    supporting_claim_ids: list[str] = Field(default_factory=list)
    opposing_claim_ids: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    falsification_queries: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    status: VerificationStatus = VerificationStatus.NEEDS_REVIEW
