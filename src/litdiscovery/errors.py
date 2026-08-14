"""Domain-specific failures exposed by the public service layer."""


class LitDiscoveryError(Exception):
    """Base class for expected application failures."""


class StepExecutionError(LitDiscoveryError):
    """A workflow step failed after its inputs were validated."""

    def __init__(self, step_id: str, cause: Exception):
        self.step_id = step_id
        self.cause = cause
        super().__init__(f"step {step_id!r} failed: {type(cause).__name__}: {cause}")
