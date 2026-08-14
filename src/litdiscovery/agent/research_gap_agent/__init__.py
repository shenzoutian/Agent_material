"""Research-gap Agent public interface."""

from .pipeline import run
from litdiscovery.contracts.agents import ResearchGapRequest, ResearchGapResult

__all__ = ["run", "ResearchGapRequest", "ResearchGapResult"]
