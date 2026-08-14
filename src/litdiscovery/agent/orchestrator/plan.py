"""
orchestrator/plan.py —— plan.v3.json 契约（agent 链 + 全参数软设置）+ 翻译为 runbook。

planner 输出 `<batch>/plan.v3.json`；运行状态由 runtime/state.py 的
`<batch>/run_state.json` 独立维护。

plan.v3.json 结构：
    {
      "plan_version": 3,
      "requirement": "...",
      "created_by": "planner",
      "confirmed": true,
      "agents": [
        {"agent": "researcher_agent", "stage": "retrieve",
         "params": {"keyword_count": 7, "min_keep": 12, "download_n": 5},
         "outputs": {"doi_list": "${batch}/orders/doi_list.json"}},
        ...
      ]
    }

plan_to_runbook：把 v3 的 agents 链展开为 runbook steps（executor 实际执行形态）：
    - 每个 agent 按 AGENT_DIRECTORY 取默认步骤模板；
    - 模板内 {p:param} 占位符用该 agent 的 params（软设置）回填，缺省用默认值；
    - 模板内 {requirement}/{batch}/{hyde:terms} 等模板占位保留，
      由 run_pipeline._resolve_templates 运行时解析。
    生成的 runbook 交给 run_pipeline 执行（确定性，断点续跑/批次自动创建全保留）。
"""

import json
import re
from pathlib import Path

from litdiscovery.agent.orchestrator.agent_directory import AGENT_DIRECTORY
from litdiscovery.agent.orchestrator.params import resolve_params
from litdiscovery.contracts.plans import PlanSchema, StepSpec

PLAN_VERSION = 3
PLAN_V3_FILE = "plan.v3.json"

_PARAM_RE = re.compile(r"\{p:([A-Za-z_][\w]*)\}")


def _fill_params(args: dict, params: dict) -> dict:
    """把 args 里的 {p:param} 占位符替换为 params 值（软设置回填）。

    - 值整体恰为单个占位符（如 "{p:keyword_count}"）→ 替换为原始类型
      （int/bool 保持，避免工具参数被字符串化）；
    - 值内嵌占位符（如 "batch-{p:id}"）→ 字符串替换。
    """
    out = {}
    for k, v in args.items():
        if isinstance(v, str):
            exact = _PARAM_RE.fullmatch(v.strip())
            if exact:
                out[k] = params.get(exact.group(1), "")
                continue
            v = _PARAM_RE.sub(lambda m: str(params.get(m.group(1), "")), v)
        out[k] = v
    return out


def new_plan(requirement: str, agents: list, confirmed: bool = False,
             batch_name: str = "") -> dict:
    """构造 plan v3 契约（planner 输出形态）。"""
    return {
        "plan_version": PLAN_VERSION,
        "requirement": requirement,
        "batch_name": batch_name,
        "created_by": "planner",
        "confirmed": confirmed,
        "agents": agents,
    }


def save_plan(batch, plan: dict) -> Path:
    """写 plan.v3.json 到批次目录，返回路径。"""
    b = Path(batch)
    b.mkdir(parents=True, exist_ok=True)
    path = b / PLAN_V3_FILE
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_plan(batch) -> dict:
    """读批次目录的 plan.v3.json；不存在返回空契约。"""
    path = Path(batch) / PLAN_V3_FILE
    if not path.exists():
        return new_plan("", [])
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return new_plan("", [])


def validate_plan(plan: dict) -> list:
    """校验 plan v3 契约，返回错误列表（空列表 = 合法）。"""
    errors = []
    if not isinstance(plan, dict):
        return ["plan 不是 dict"]
    try:
        PlanSchema(**plan)
    except Exception as exc:
        # Keep the established Chinese diagnostics while exposing strict schema details.
        if plan.get("plan_version") != PLAN_VERSION:
            return [f"plan_version 应为 {PLAN_VERSION}"]
        agents = plan.get("agents")
        if not isinstance(agents, list) or not agents:
            return ["agents 链为空"]
        for i, ag in enumerate(agents, 1):
            if not isinstance(ag, dict) or not isinstance(ag.get("params"), dict):
                return [f"agents[{i}] {ag.get('agent') if isinstance(ag, dict) else ''} 缺 params"]
        errors.append(f"plan schema 无效: {exc}")
    if plan.get("plan_version") != PLAN_VERSION:
        errors.append(f"plan_version 应为 {PLAN_VERSION}")
    agents = plan.get("agents")
    if not isinstance(agents, list) or not agents:
        errors.append("agents 链为空")
        return errors
    for i, ag in enumerate(agents, 1):
        name = ag.get("agent")
        if name not in AGENT_DIRECTORY:
            errors.append(f"agents[{i}] 未知 agent: {name!r}（可用: {', '.join(AGENT_DIRECTORY)}）")
        if not isinstance(ag.get("params"), dict):
            errors.append(f"agents[{i}] {name} 缺 params")
        cfg = AGENT_DIRECTORY.get(name)
        if cfg and ag.get("stage") and ag.get("stage") != cfg["stage"]:
            errors.append(
                f"agents[{i}] {name} stage 应为 {cfg['stage']!r}，得到 {ag.get('stage')!r}")

    # Compile-time handoff validation. Report writer may summarize partial workflows.
    available = set()
    for i, ag in enumerate(agents, 1):
        cfg = AGENT_DIRECTORY.get(ag.get("agent"))
        if not cfg:
            continue
        missing = set(cfg.get("requires", [])) - available
        if missing:
            errors.append(f"agents[{i}] {ag.get('agent')} 缺前置产物: {', '.join(sorted(missing))}")
        available.update(cfg.get("provides", []))
    return errors


def plan_to_runbook(plan: dict, ctx: dict = None) -> dict:
    """plan v3 → runbook v2 steps（agent 链展开 + 参数软设置回填）。

    返回 runbook dict（含 requirement/domain/batch/steps），可直接交给
    run_pipeline 执行。steps 里保留 {requirement}/{batch}/{hyde:terms} 模板，
    由 run_pipeline 的 _resolve_templates 解析。
    """
    steps = []
    for ag in plan.get("agents", []):
        name = ag.get("agent")
        cfg = AGENT_DIRECTORY.get(name)
        if cfg is None:
            raise KeyError(f"[plan] 未知 agent {name!r}（可用: {', '.join(AGENT_DIRECTORY)}）")
        params = resolve_params(name, ag.get("params") or {})
        for tpl in cfg["steps"]:
            step = {
                "stage": tpl["stage"],
                "args": _fill_params(tpl.get("args", {}), params),
            }
            if tpl.get("kind"):
                step["kind"] = tpl["kind"]
            if tpl.get("tool"):
                step["tool"] = tpl["tool"]
            spec = StepSpec(**step).with_stable_id()
            steps.append(spec.model_dump(exclude_none=True) if hasattr(spec, "model_dump")
                         else spec.dict(exclude_none=True))

    domain = plan.get("domain", "")
    return {
        "name": f"plan-v3-{plan.get('requirement', '')[:20]}",
        "requirement": plan.get("requirement", ""),
        "domain": domain,
        "batch": plan.get("batch", ""),
        "steps": steps,
    }
