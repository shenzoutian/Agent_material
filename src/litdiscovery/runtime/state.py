"""Single-source workflow state with legacy ledger migration."""

import json
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from litdiscovery.common.fs import write_json_atomic


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class StepState(BaseModel):
    step_id: str
    stage: str = ""
    operation: str = ""
    status: StepStatus = StepStatus.PENDING
    args_hash: str = ""
    attempts: int = 0
    started_at: str = ""
    finished_at: str = ""
    output: str = ""
    error: str = ""
    artifacts: list[str] = Field(default_factory=list)


class RunState(BaseModel):
    schema_version: int = 1
    requirement: str = ""
    batch: str = ""
    domain: str = ""
    steps: dict[str, StepState] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class RunStateStore:
    FILE_NAME = "run_state.json"

    def __init__(self, batch: str | Path):
        self.batch = Path(batch)
        self.path = self.batch / self.FILE_NAME

    def load(self, *, requirement: str = "", domain: str = "") -> RunState:
        if self.path.exists():
            return RunState(**json.loads(self.path.read_text(encoding="utf-8")))
        return RunState(requirement=requirement, batch=str(self.batch), domain=domain)

    def save(self, state: RunState) -> None:
        state.updated_at = datetime.now().isoformat()
        data = state.model_dump(mode="json") if hasattr(state, "model_dump") else state.dict()
        write_json_atomic(self.path, data)

    def begin(self, state: RunState, step_id: str, stage: str, operation: str) -> StepState:
        step = state.steps.get(step_id) or StepState(step_id=step_id, stage=stage, operation=operation)
        step.status = StepStatus.RUNNING
        step.attempts += 1
        step.started_at = datetime.now().isoformat()
        step.error = ""
        state.steps[step_id] = step
        self.save(state)
        return step

    def succeed(self, state: RunState, step_id: str, output: str = "") -> None:
        step = state.steps[step_id]
        step.status = StepStatus.SUCCEEDED
        step.output = output[:2000]
        step.finished_at = datetime.now().isoformat()
        self.save(state)

    def fail(self, state: RunState, step_id: str, error: Exception) -> None:
        step = state.steps[step_id]
        step.status = StepStatus.FAILED
        step.error = f"{type(error).__name__}: {error}"
        step.finished_at = datetime.now().isoformat()
        self.save(state)

    def skip(self, state: RunState, step_id: str, reason: str = "") -> None:
        """把步骤标记为 SKIPPED（robust_agent 决策 skip 后，断点续跑不再重试）。"""
        step = state.steps[step_id]
        step.status = StepStatus.SKIPPED
        step.error = reason
        step.finished_at = datetime.now().isoformat()
        self.save(state)

    @staticmethod
    def completed(state: RunState, step_id: str) -> bool:
        step = state.steps.get(step_id)
        return bool(step and step.status == StepStatus.SUCCEEDED)
