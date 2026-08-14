"""Versioned data contracts shared across services and workflow runtime."""

from .evidence import Claim, EvidenceLocator, Hypothesis, VerificationStatus
from .plans import AgentSpec, PlanSchema, RunbookSchema, StepSpec
from .agents import (
    ExtractorRequest, ExtractorResult, FilterRequest, FilterResult,
    ResearcherRequest, ResearcherResult, ResearchGapRequest, ResearchGapResult,
    ValidateRequest, ValidateResult,
)

__all__ = [
    "AgentSpec", "Claim", "EvidenceLocator",
    "Hypothesis", "PlanSchema", "RunbookSchema", "StepSpec", "VerificationStatus",
    "ResearcherRequest", "ResearcherResult", "FilterRequest", "FilterResult",
    "ExtractorRequest", "ExtractorResult", "ResearchGapRequest", "ResearchGapResult",
    "ValidateRequest", "ValidateResult",
]
