"""
litdiscovery/agent/orchestrator/pipeline.py —— executor：确定性流水线驱动。

planner 只做 LLM 路由（生成 plan.v3.json），本模块把执行统一为"确定性步骤链"：
    - run_pipeline(runbook.json)：按 runbook steps 逐步骤调用 build_tools() 里同名 @tool；
    - run_plan(plan.v3.json)：把 plan 的 agents 链按 AGENT_DIRECTORY 展开为 runbook steps
      （plan_to_runbook），再交 run_pipeline 执行——两条入口共用同一执行器。
自动建批次、按批次解析相对路径，并以 run_state.json 维护断点续跑。

runbook JSON 结构：
    {
      "name": "...",
      "requirement": "科研需求",
      "domain": "piezoelectric",
      "batch": "",                        # 留空 → 驱动 create_log_dir 建新批次
      "steps": [
        {"stage": "retrieve", "kind": "hyde", "args": {"requirement": "{requirement}"}},
        {"stage": "retrieve", "tool": "generate_keywords",
         "args": {"requirement": "{requirement}", "count": 7}},
        {"stage": "retrieve", "tool": "search_papers",
         "args": {"requirement": "{requirement}", "keywords": "{hyde:terms}"}},
        {"stage": "retrieve", "tool": "write_doi_list",
         "args": {"batch": "{batch}", "source": "search_results.json"}},
        # ---- filter_agent 固定链（researcher 收敛后接入）----
        # 相对路径参数（seeds_file/papers_file/candidates_file）自动解析到 <批次>/orders/<文件>
        {"stage": "fulltext", "tool": "snowball_expand",
         "args": {"seeds_file": "doi_list.json", "rounds": 1}},
        {"stage": "fulltext", "tool": "choose_papers",
         "args": {"requirement": "{requirement}", "papers_file": "snowball_candidates.json", "min_keep": 21}},
        {"stage": "fulltext", "tool": "finalize_batch", "args": {"download_n": 150}},
        ...
      ]
    }

args 模板：
    {requirement} / {domain} / {batch}        顶层配置 + 当前批次
    {prev:<tool>}                             上一步同名工具的输出字符串
    {hyde:terms} / {hyde:overall}             HyDE 子问题检索词 / 全局检索词（逗号连接）
    {p:<param>}                               plan.v3 参数（plan_to_runbook 阶段回填默认值）

自动行为：
    - 接受 batch 参数的工具且 runbook 未显式给 batch → 注入当前批次
    - 相对路径参数（papers_file/seeds_file/candidates_file）→ 解析为 <批次>/orders/<值>
      （Agent 交接文件统一收进 orders/，与 end_mds/ 同级）
    - 每步状态只写 run_state.json
    - 每步工具调用落一条结构化记录 <会话>/execution.jsonl
      {agent, tool, args, output, duration_ms, ts}（每批一会话，会话名 = 批次名）
    - 步骤已完成且未 --force → 跳过

运行：
    litdiscovery pipeline <runbook.json> [--dry-run] [--force]   # runbook 驱动
    python run_all.py --json <runbook.json>                      # run_all 双模式入口
"""

import argparse
import json
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

from litdiscovery.config import create_agent
from litdiscovery.common.logging import (
    create_log_dir,
    session_dir_for_batch,
    redirect_to_session,
    append_execution_record,
)
from litdiscovery.paths import resolve_batch, handoff_path
from litdiscovery.agent.agent_roles.registry import ROLE_TOOL_MAP
from litdiscovery.agent.orchestrator.plan import load_plan, plan_to_runbook
from litdiscovery.contracts.plans import stable_step_id
from litdiscovery.errors import StepExecutionError
from litdiscovery.runtime import RunStateStore
from litdiscovery.agent.robust_agent import Decision, handle_exception, mark_success

def _tool_schema_fields(tool) -> set:
    """从 StructuredTool 派生参数字段名（兼容 pydantic v1 __fields__ / v2 model_fields）。"""
    schema = getattr(tool, "args_schema", None)
    if schema is None:
        return set()
    fields = getattr(schema, "model_fields", None) or getattr(schema, "__fields__", None)
    return set(fields or {})


def _derive_tool_meta():
    """从 build_tools() 派生 (接受 batch 参数的工具集, 工具名 → 底层实现路径)。

    消除与 agent_roles/tools.py 的手工同步：新工具增删、改参数名时此处自动跟随，
    不再需要维护两份常量清单。底层实现路径取 StructuredTool.func 的
    module.name（实现函数本身），比手写字符串更可靠。
    """
    from litdiscovery.agent.agent_roles import build_tools
    tools = build_tools()
    batch = {t.name for t in tools if "batch" in _tool_schema_fields(t)}
    entry = {}
    for t in tools:
        fn = getattr(t, "func", None)
        if fn is None:
            continue
        entry[t.name] = (fn.__module__, f"{fn.__module__}.{fn.__name__}")
    return batch, entry


# ---- 从 build_tools() 自动派生的工具元数据（驱动自动注入 batch 参数的集合 + dry-run 底层入口）----
_BATCH_TOOLS, TOOL_ENTRY = _derive_tool_meta()

# ---- 相对路径参数：按当前批次目录解析（交接文件收进 orders/）----
_PATH_ARGS = {"papers_file", "seeds_file", "candidates_file"}

# ---- 工具 → 角色 Agent 反向映射（execution.jsonl 的 agent 字段）----
_TOOL_AGENT = {t: role for role, tools in ROLE_TOOL_MAP.items() for t in tools}


def _snip(value, n=200):
    """把入参/出参里的长字符串截断，避免 execution.jsonl 被全文撑爆。"""
    if isinstance(value, str):
        return value if len(value) <= n else value[:n] + "..."
    if isinstance(value, dict):
        return {k: _snip(v, n) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_snip(v, n) for v in value]
    return value


def _load_registry():
    """加载 build_tools() 的 {工具名: StructuredTool} 注册表。"""
    from litdiscovery.agent.agent_roles import build_tools
    return {t.name: t for t in build_tools()}


def _resolve_templates(value, ctx):
    """替换 args 模板：{requirement}/{domain}/{batch}/{prev:<tool>}/{hyde:terms}/{hyde:overall}。"""
    if isinstance(value, str):
        value = value.replace("{requirement}", ctx["requirement"])
        value = value.replace("{domain}", ctx.get("domain", ""))
        value = value.replace("{batch}", str(ctx["batch"]))
        value = re.sub(r"\{prev:([A-Za-z_][\w]*)\}",
                       lambda m: str(ctx["outputs"].get(m.group(1), "")), value)
        value = re.sub(r"\{hyde:terms\}",
                       lambda m: ", ".join(ctx.get("hyde_terms", [])), value)
        value = re.sub(r"\{hyde:overall\}",
                       lambda m: ", ".join(ctx.get("hyde_overall", [])), value)
    return value


def _run_hyde(ctx):
    """确定性 HyDE 拆分：把检索词物化成可流入 search_papers 的数据。

    HyDE 归检索能力，位于 retrieval.hyde。
    """
    from litdiscovery.agent.researcher_agent_pipeline.hyde import hyde_expand
    expanded = hyde_expand(ctx["requirement"], llm=create_agent("researcher_agent"))
    terms = [t for sp in expanded.get("sub_problems", [])
             for t in (sp.get("search_terms") or [])]
    terms += expanded.get("overall_terms") or []
    ctx["hyde_terms"] = [t for t in terms if t]
    ctx["hyde_overall"] = expanded.get("overall_terms") or []
    return expanded


def run_pipeline(runbook, batch: str = "", dry_run: bool = False,
                 force: bool = False, step_filter=None) -> dict:
    """执行 runbook 字典或 JSON 文件。"""
    cfg = (json.loads(Path(runbook).read_text(encoding="utf-8"))
           if isinstance(runbook, (str, Path)) else dict(runbook))
    requirement = cfg.get("requirement", "") or ""
    domain = cfg.get("domain", "") or ""
    steps = cfg.get("steps", []) or []
    if not steps:
        raise ValueError("[pipeline] runbook 无 steps")

    registry = _load_registry()

    # ---- 批次解析：--batch > runbook.batch > 新建 ----
    # dry-run 不落盘：无显式批次时用占位路径，仅用于展示相对路径解析
    if batch:
        b = Path(batch) if Path(batch).is_absolute() else resolve_batch(batch)
    elif cfg.get("batch"):
        b = Path(cfg["batch"]) if Path(cfg["batch"]).is_absolute() else resolve_batch(cfg["batch"])
    elif dry_run:
        b = Path("<auto-新建批次>")
    else:
        b = create_log_dir(requirement)
    if not dry_run:
        b.mkdir(parents=True, exist_ok=True)
        # 每批一会话：stdout 双写 + execution.jsonl 收敛到批次会话（续跑复用）
        redirect_to_session(session_dir_for_batch(b))

    ctx = {"requirement": requirement, "domain": domain, "batch": b,
           "outputs": {}, "hyde_terms": [], "hyde_overall": []}

    state_store = RunStateStore(b)
    run_state = state_store.load(requirement=requirement, domain=domain)

    trace = []
    executed = 0
    total = len(steps)

    for i, step in enumerate(steps, 1):
        tool = step.get("tool")
        kind = step.get("kind", "tool")
        stage = step.get("stage", "")
        if step.get("enabled") is False:
            print(f"[{i}/{total}] [跳过] {tool or kind} 在 runbook 中已禁用")
            continue
        step_id = step.get("step_id") or stable_step_id(stage, tool or kind, step.get("args", {}) or {})
        already_done = state_store.completed(run_state, step_id)
        if not force and already_done:
            print(f"[{i}/{total}] [续跑] {step_id} 已完成，跳过（--force 可重跑）")
            continue
        if step_filter and i not in step_filter:
            continue

        # ---- HyDE 步骤：确定性拆分（不调工具，但物化检索词） ----
        if kind == "hyde":
            if dry_run:
                print(f"[{i}/{total}] [HyDE] 拆分需求 → 检索词（dry-run 不调用 LLM）")
            else:
                step_state = state_store.begin(run_state, step_id, stage, "hyde")
                t0 = time.perf_counter()
                try:
                    expanded = _run_hyde(ctx)
                except Exception as exc:
                    state_store.fail(run_state, step_id, exc)
                    append_execution_record(b, {"event": "step_failed", "status": "failed",
                        "step_id": step_id, "attempt": step_state.attempts, "agent": "researcher_agent",
                        "tool": "hyde_expand", "stage": stage, "error": str(exc)})
                    raise StepExecutionError(step_id, exc) from exc
                dur_ms = round((time.perf_counter() - t0) * 1000, 1)
                n_sub = len(expanded.get("sub_problems", []))
                print(f"[{i}/{total}] [HyDE] 拆分 {n_sub} 个子问题 → 检索词 {len(ctx['hyde_terms'])} 个")
                state_store.succeed(run_state, step_id, f"terms={len(ctx['hyde_terms'])}")
                append_execution_record(b, {
                    "step_id": step_id, "attempt": step_state.attempts,
                    "agent": "researcher_agent", "tool": "hyde_expand", "stage": stage,
                    "args": {"requirement": _snip(ctx["requirement"])},
                    "output": f"子问题 {n_sub} 个，检索词 {len(ctx['hyde_terms'])} 个",
                    "duration_ms": dur_ms, "ts": datetime.now().isoformat(),
                })
            trace.append({"step": step_id, "kind": "hyde",
                          "terms": ctx["hyde_terms"], "overall": ctx["hyde_overall"]})
            continue

        # ---- copy 桥接步骤：把 A 产物复制为 B（补 retrieve 链 seed_papers 缺口） ----
        if kind == "copy":
            src = step["args"].get("src")
            dst = step["args"].get("dst")
            src_p = Path(src) if Path(src).is_absolute() else handoff_path(b, src)
            dst_p = Path(dst) if Path(dst).is_absolute() else handoff_path(b, dst)
            if dry_run:
                print(f"[{i}/{total}] [copy] {src_p} → {dst_p}")
            else:
                step_state = state_store.begin(run_state, step_id, stage, "copy")
                t0 = time.perf_counter()
                if not src_p.exists():
                    exc = FileNotFoundError(f"[copy] 源不存在: {src_p}")
                    state_store.fail(run_state, step_id, exc)
                    append_execution_record(b, {"event": "step_failed", "status": "failed",
                        "step_id": step_id, "attempt": step_state.attempts, "tool": "copy",
                        "stage": stage, "error": str(exc)})
                    raise StepExecutionError(step_id, exc) from exc
                dst_p.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_p, dst_p)
                dur_ms = round((time.perf_counter() - t0) * 1000, 1)
                print(f"[{i}/{total}] [copy] {src} → {dst}")
                state_store.succeed(run_state, step_id, str(dst_p))
                append_execution_record(b, {
                    "step_id": step_id, "attempt": step_state.attempts,
                    "agent": "", "tool": "copy", "stage": stage,
                    "args": {"src": str(src_p), "dst": str(dst_p)},
                    "output": f"{src} → {dst}",
                    "duration_ms": dur_ms, "ts": datetime.now().isoformat(),
                })
            trace.append({"step": step_id, "kind": "copy", "src": str(src_p), "dst": str(dst_p)})
            continue

        # ---- 工具步骤 ----
        fn = registry.get(tool)
        if fn is None:
            raise KeyError(f"[pipeline] 未知工具 {tool!r}（可用: {', '.join(sorted(registry))}）")
        resolved = {}
        for k, v in (step.get("args", {}) or {}).items():
            v = _resolve_templates(v, ctx)
            if k in _PATH_ARGS and isinstance(v, str) and v and not Path(v).is_absolute():
                v = str(handoff_path(b, v))
            resolved[k] = v
        if tool in _BATCH_TOOLS and "batch" not in resolved:
            resolved["batch"] = str(b)

        entry = TOOL_ENTRY.get(tool, (tool, ""))[1]
        if dry_run:
            print(f"[{i}/{total}] {tool}({json.dumps(resolved, ensure_ascii=False)})")
            print(f"          ← 入口: {entry}")
            trace.append({"step": step_id, "tool": tool, "args": resolved, "entry": entry})
            continue

        print(f"[{i}/{total}] 调用 {tool} ...")
        t0 = time.perf_counter()
        step_state = state_store.begin(run_state, step_id, stage, tool)
        out = None
        last_error = None
        decision = None
        succeeded = False
        while True:
            try:
                out = fn.invoke(resolved)
                mark_success(stage=stage, operation=tool)
                succeeded = True
                break
            except Exception as e:
                last_error = e
                decision = handle_exception(e, stage=stage, operation=tool,
                                            context=_snip(resolved), batch_root=str(b))
                if decision is Decision.RETRY:
                    time.sleep(2)
                    continue
                break

        if not succeeded:
            if decision is Decision.ABORT:
                state_store.fail(run_state, step_id, last_error)
                append_execution_record(b, {"event": "step_failed", "status": "failed",
                    "step_id": step_id, "attempt": step_state.attempts,
                    "agent": _TOOL_AGENT.get(tool, ""), "tool": tool, "stage": stage,
                    "args": _snip(resolved), "error": str(last_error)})
                print(f"[pipeline] 步骤 {step_id} 失败: {type(last_error).__name__}: {last_error}")
                raise StepExecutionError(step_id, last_error) from last_error
            # SKIP / DEGRADE：记录为跳过，继续下一步（断点续跑不再重试该步）
            reason = decision.value if decision else "skip"
            state_store.skip(run_state, step_id, reason)
            append_execution_record(b, {"event": "step_skipped", "status": "skipped",
                "step_id": step_id, "attempt": step_state.attempts,
                "agent": _TOOL_AGENT.get(tool, ""), "tool": tool, "stage": stage,
                "args": _snip(resolved), "error": str(last_error), "output": reason})
            print(f"[pipeline] 步骤 {step_id} 已跳过（{reason}）")
            trace.append({"step": step_id, "tool": tool, "decision": reason})
            continue
        dur_ms = round((time.perf_counter() - t0) * 1000, 1)
        out = str(out)
        ctx["outputs"][tool] = out
        executed += 1
        state_store.succeed(run_state, step_id, out)
        append_execution_record(b, {
            "step_id": step_id, "attempt": step_state.attempts,
            "agent": _TOOL_AGENT.get(tool, ""),
            "tool": tool,
            "stage": stage,
            "args": _snip(resolved),
            "output": out[:200],
            "duration_ms": dur_ms,
            "ts": datetime.now().isoformat(),
        })
        trace.append({"step": step_id, "tool": tool, "args": resolved, "output": out[:200]})
        print(f"     → {out[:160].replace(chr(10), ' ')}")

    return {"batch": str(b), "trace": trace, "steps": total, "executed": executed,
            "skipped": total - executed}


def run_plan(plan_path, batch: str = "", dry_run: bool = False,
             force: bool = False, step_filter=None) -> dict:
    """执行 plan v3：编译为内存 runbook 后交给确定性执行器。

    planner 纯路由化后，planner 只产出 plan.v3.json（agent 链 + 软设置参数），
    本函数把 agents 链按 AGENT_DIRECTORY 展开为 runbook steps（参数回填默认值、
    路径模板交由 _resolve_templates 解析），再复用 run_pipeline 的断点续跑/批次机制。
    """
    plan = load_plan(Path(plan_path).parent if Path(plan_path).is_file() else batch)
    if not plan.get("agents"):
        raise ValueError(f"[pipeline] plan 无 agents 链: {plan_path}")
    runbook = plan_to_runbook(plan, ctx={"requirement": plan.get("requirement", "")})

    b = Path(batch) if batch else Path(plan_path).parent
    result = run_pipeline(runbook, batch=str(b) if b else "",
                          dry_run=dry_run, force=force, step_filter=step_filter)
    result["plan"] = plan
    result["runbook"] = runbook
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="pipeline",
        description="确定性 JSON 流水线驱动：按 runbook 逐步骤调用各阶段组件（绕过 planner LLM）。")
    parser.add_argument("runbook", type=str, help="runbook JSON 路径（自建，参考本模块 docstring 结构）")
    parser.add_argument("--batch", type=str, default="", help="指定批次目录（覆盖 runbook.batch）")
    parser.add_argument("--dry-run", action="store_true", help="只解析并打印调用链，不执行任何组件")
    parser.add_argument("--force", action="store_true", help="忽略 run_state.json 断点，重跑全部步骤")
    parser.add_argument("--step", type=str, default="", help="只跑指定步骤序号（逗号分隔，1 基）")
    args = parser.parse_args(argv)

    if not Path(args.runbook).exists():
        print(f"[pipeline] runbook 不存在: {args.runbook}")
        return 2
    step_filter = {int(x) for x in args.step.split(",") if x.strip()} if args.step else None
    result = run_pipeline(args.runbook, batch=args.batch, dry_run=args.dry_run,
                          force=args.force, step_filter=step_filter)

    print("\n" + "=" * 66)
    print(f"[pipeline] runbook: {args.runbook}")
    print(f"[pipeline] 批次: {result['batch']}")
    print(f"[pipeline] 步骤: {result['steps']} 总 / 执行 {result['executed']} / 跳过 {result['skipped']}")
    if args.dry_run:
        print("[pipeline] dry-run：以上仅为调用链，未执行任何组件")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
