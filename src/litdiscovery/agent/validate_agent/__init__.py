"""External materials-database validation Agent public interface."""

from .pipeline import run
from litdiscovery.contracts.agents import ValidateRequest, ValidateResult

__all__ = ["run", "ValidateRequest", "ValidateResult"]
