"""Public validation pipeline facade."""

from litdiscovery.contracts.agents import ValidateRequest, ValidateResult
from .api import run_validate


def run(request: ValidateRequest) -> ValidateResult:
    if not isinstance(request, ValidateRequest):
        raise TypeError("request must be ValidateRequest")
    payload = run_validate(
        list(request.formulas), batch=request.batch,
        out_root=request.output_dir, delay=request.delay,
    )
    return ValidateResult(
        payload["n_validated"], payload["n_available"],
        tuple(payload.get("reports", [])), payload.get("summary_path", ""),
    )

