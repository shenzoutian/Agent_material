"""Clear public facade for planning and executing research workflows."""

from pathlib import Path


class WorkflowService:
    def plan(self, requirement: str, *, batch: str = "", constraints: dict | None = None,
             confirm: bool = False) -> dict:
        from litdiscovery.agent.orchestrator.planner import run_planner
        return run_planner(requirement, batch=batch, constraints=constraints, confirm=confirm)

    def execute(self, plan_path: str | Path, *, batch: str = "", dry_run: bool = False,
                force: bool = False, step_filter=None) -> dict:
        from litdiscovery.agent.orchestrator.pipeline import run_plan
        return run_plan(str(plan_path), batch=batch, dry_run=dry_run,
                        force=force, step_filter=step_filter)

    def run(self, requirement: str, *, batch: str = "", constraints: dict | None = None,
            confirm: bool = False, dry_run: bool = False) -> dict:
        planned = self.plan(requirement, batch=batch, constraints=constraints, confirm=confirm)
        if dry_run:
            return self.execute(planned["plan_path"], batch=planned["batch"], dry_run=True)
        return self.execute(planned["plan_path"], batch=planned["batch"])
