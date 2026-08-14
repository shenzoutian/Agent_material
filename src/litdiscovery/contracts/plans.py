"""Strict planner and executor contracts."""

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, Field


def stable_step_id(stage: str, operation: str, args: dict[str, Any]) -> str:
    canonical = json.dumps(args, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(f"{stage}\0{operation}\0{canonical}".encode("utf-8")).hexdigest()[:12]
    return f"{stage}.{operation}.{digest}"


class AgentSpec(BaseModel):
    agent: str
    stage: str = ""
    params: dict[str, Any]
    outputs: dict[str, str] = Field(default_factory=dict)


class PlanSchema(BaseModel):
    plan_version: Literal[3] = 3
    requirement: str = Field(min_length=1)
    batch_name: str = ""
    created_by: str = "planner"
    confirmed: bool = False
    domain: str = ""
    batch: str = ""
    agents: list[AgentSpec] = Field(min_length=1)


class StepSpec(BaseModel):
    step_id: str = ""
    stage: str
    kind: str = "tool"
    tool: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    requires: list[str] = Field(default_factory=list)
    provides: list[str] = Field(default_factory=list)

    def with_stable_id(self) -> "StepSpec":
        if self.step_id:
            return self
        operation = self.tool or self.kind
        data = self.model_dump() if hasattr(self, "model_dump") else self.dict()
        data["step_id"] = stable_step_id(self.stage, operation, self.args)
        return type(self)(**data)


class RunbookSchema(BaseModel):
    name: str = ""
    requirement: str
    domain: str = ""
    batch: str = ""
    steps: list[StepSpec] = Field(min_length=1)
