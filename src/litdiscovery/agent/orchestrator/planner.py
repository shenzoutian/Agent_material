"""
orchestrator/planner.py —— LLM 路由 planner。

不掌握任何具体工具：收到用户指令 → 结合 AGENT_DIRECTORY（子 Agent 能力 +
参数 schema + 默认参考）生成 plan.v3.json（agent 链 + 全参数软设置）→
用户确认（--auto 跳过）→ 落盘批次目录；执行统一交 executor
（pipeline.run_plan）按 plan.v3.json 逐 agent 确定性调用。

软设置规约：plan.v3.json 内所有参数（含路径）均为模板/用户给定值；
默认值（config 常量）经 params.py 注入本模块 prompt 作为参考，具体值由
planner 结合用户输入确定。

工作流：
    run_planner(requirement, ...)
      ├─ ① 组装 ROUTER_SYSTEM（AGENT_DIRECTORY + 参数参考）注入 LLM
      ├─ ② LLM 输出 plan.v3.json（agent 链 + params 软设置）
      ├─ ③ 校验（validate_plan）+ 用户确认/修改（--auto 跳过）
      └─ ④ save_plan 落盘 <batch>/plan.v3.json → 交 executor
"""

import json
import sys
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from litdiscovery.config import create_agent
from litdiscovery.llm_utils import parse_json_text
from litdiscovery.agent.orchestrator.agent_directory import AGENT_DIRECTORY, render_directory
from litdiscovery.agent.orchestrator.plan import (
    new_plan,
    save_plan,
    validate_plan,
)

ROUTER_SYSTEM = """你是科研调研工作流编译器 planner。你只选择能力并配置参数，不执行工具、不生成科研结论、不猜测文件是否存在。

可用子 Agent 目录：
{directory}

编排规则：
1. 先识别用户要求的最终产物，再选择产生该产物所需的最短完整 Agent 链。
2. 严格满足目录中 requires/provides 的前置产物关系；除非用户明确提供本地文档或已有批次，不得跳过生产前置数据的 Agent。
3. 每个选中的子 Agent必须给出完整 params：
   - 参数值须结合用户输入确定；用户未提及时，可参考上表默认值；
   - 路径类参数用模板（如 {batch}/doi_list.json），禁止写死绝对路径。
4. 完整链路通常为 researcher_agent → filter_agent → extractor_agent → gap_chain → validate → report_writer；validate 仅在材料公式和待验证声明存在时加入。
5. “只做某阶段”仍需判断输入是否由用户/批次提供；没有输入时补齐前置阶段。
6. 不得根据想象跳过“已完成阶段”；只有用户明确说明或提供批次状态时才可续跑。
7. 当用户说“继续/run/恢复/重试”且给出已有批次时，优先复用该批次 plan，交给 executor 依据 run_state 续跑；需要诊断时加入 review_agent。
8. 参数采用保守预算：小规模验证优先较小 limit/download_n，大规模任务遵从用户显式上限。

输出要求：
- 只输出一个符合 plan v3 的 JSON 对象，不得增加未知 Agent、未知参数、解释或代码块。
- batch_name 必须概括科研任务主题，用作目录名称；尽量简短，不含时间戳、路径或文件名非法字符。
- 结构示例：
{
  "plan_version": 3,
  "requirement": "科研需求原文",
  "batch_name": "新型滤波器",
  "domain": "piezoelectric",
  "batch": "",
  "confirmed": true,
  "agents": [
    {"agent": "researcher_agent", "stage": "retrieve",
      "params": {"keyword_count": 7, "min_keep": 12, "download_n": 5},
      "outputs": {"doi_list": "{batch}/doi_list.json"}},
    {"agent": "extractor_agent", "stage": "extract",
      "params": {"domain": "piezoelectric", "limit": 20},
      "outputs": {"extracted": "{batch}/extracted"}}
  ]
}
"""


def _extract_plan_json(text) -> dict:
    """从 LLM 输出提取 plan.v3.json 对象。"""
    data = parse_json_text(text)
    if not isinstance(data, dict):
        raise ValueError(f"plan 需要 dict 结构，得到 {type(data).__name__}")
    return data


def generate_plan(requirement: str, llm=None, constraints: dict = None) -> dict:
    """LLM 路由：用户指令 → plan.v3.json（agent 链 + 参数软设置）。

    llm 缺省用 planner 角色实例化。constraints（seed_dois/download_n/limit 等）
    拼入用户消息，供 planner 结合输入确定参数。
    """
    llm = llm or create_agent("planner")
    system = ROUTER_SYSTEM.replace("{directory}", render_directory())

    user = f"科研需求：{requirement}"
    if constraints:
        parts = []
        if constraints.get("seed_dois"):
            parts.append(f"使用以下手动种子 DOI 检索（不要跑关键词+Apify）：{constraints['seed_dois']}")
        if constraints.get("download_n"):
            parts.append(f"下载论文数量上限设为 {constraints['download_n']} 篇")
        if constraints.get("limit"):
            parts.append(f"提取阶段最多处理 {constraints['limit']} 篇（验证用小值）")
        if constraints.get("docs"):
            parts.append("用户手动提供了本地文档，跳过检索，直接用这些文档走提取")
        if parts:
            user += "\n\n用户明确约束（必须结合到参数设置中）：\n" + "\n".join(f"  - {p}" for p in parts)

    out = llm.invoke([SystemMessage(content=system),
                      HumanMessage(content=user)])
    plan = _extract_plan_json(getattr(out, "content", str(out)))

    # 归一化：补 requirement / 校验
    plan["requirement"] = plan.get("requirement") or requirement
    errors = validate_plan(plan)
    if errors:
        raise ValueError("planner 输出未通过校验: " + "; ".join(errors))
    return plan


_CONFIRM_KEYWORDS = ("y", "yes", "ok", "确认", "确认执行")
_RECOVERY_COMMANDS = {"run", "继续", "续跑", "恢复", "重试", "继续运行", "resume", "continue"}
_RECOVERY_PHRASES = ("继续", "续跑", "恢复", "重试", "上一步", "上一轮", "resume", "continue")


def _is_recovery_request(requirement: str) -> bool:
    """Recognize commands and natural-language requests to resume prior work."""
    text = " ".join((requirement or "").strip().lower().split())
    return text in _RECOVERY_COMMANDS or any(phrase in text for phrase in _RECOVERY_PHRASES)


def _latest_incomplete_batch() -> Path | None:
    """Return the newest resumable batch, excluding accidental recovery-only plans."""
    from litdiscovery.paths import BATCHES_ROOT, batch_sort_key

    if not BATCHES_ROOT.is_dir():
        return None
    candidates = []
    for candidate in BATCHES_ROOT.iterdir():
        plan_path = candidate / "plan.v3.json"
        state_path = candidate / "run_state.json"
        if not candidate.is_dir() or not plan_path.is_file() or not state_path.is_file():
            continue
        try:
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        # A recovery phrase must never define a new research topic. Such a plan is
        # residue from the old LLM-routing bug and must not shadow the real batch.
        if _is_recovery_request(str(plan.get("requirement", ""))):
            continue
        statuses = [str(step.get("status", "")) for step in (state.get("steps") or {}).values()]
        if not statuses or any(status != "succeeded" for status in statuses):
            candidates.append(candidate)
            continue
        # A full workflow only becomes terminal after its requested report exists.
        agents = {str(agent.get("agent", "")) for agent in (plan.get("agents") or [])}
        if "report_writer" in agents and not (candidate / "report.md").is_file():
            candidates.append(candidate)
    return max(candidates, key=batch_sort_key) if candidates else None


def _print_plan(plan: dict) -> None:
    """打印 plan v3 草案（agent 链 + 参数），供用户确认。"""
    print("\n" + "=" * 66)
    print("[Planner] plan.v3.json 草案：")
    for i, ag in enumerate(plan.get("agents", []), 1):
        print(f"  {i}. {ag['agent']} [{ag.get('stage', '')}]")
        print(f"       params: {json.dumps(ag.get('params', {}), ensure_ascii=False)}")
    print("-" * 66)


def _load_replacement_plan(path: str, plan: dict) -> dict:
    """尝试从文件加载用户修改后的 plan v3；任何失败都沿用原草案。"""
    p = Path(path)
    if not p.exists():
        print(f"[Planner] 替换文件不存在: {p}，沿用草案")
        return plan
    try:
        alt = json.loads(p.read_text(encoding="utf-8"))
        errors = validate_plan(alt)
        if errors:
            raise ValueError("; ".join(errors))
        print(f"[Planner] 已加载替换 plan: {p}")
        return alt
    except Exception as e:
        print(f"[Planner] 替换文件无效({e})，沿用草案")
        return plan


def _confirm_plan(plan: dict) -> dict:
    """交互确认：接受草案 / 退出 / 用用户提供的 plan 文件替换草案。

    用户输入：回车或确认词 = 接受；q/quit/exit = 退出；
    其他输入视为修改后的 plan.v3.json 路径（加载成功则替换）。
    返回的 plan 一律标记 confirmed=True。
    """
    _print_plan(plan)
    inp = input("确认执行? (回车=确认 / q=退出 / 输入修改后的 plan 文件路径): ").strip().lower()
    if inp in ("q", "quit", "exit"):
        sys.exit(0)
    if inp and inp not in _CONFIRM_KEYWORDS:
        plan = _load_replacement_plan(inp, plan)
    plan["confirmed"] = True
    return plan


def run_planner(requirement: str, batch: str = "",
                constraints: dict = None, confirm: bool = False) -> dict:
    """运行纯路由 planner：生成 plan.v3.json → 确认 → 落盘批次目录。

    参数:
        requirement: 科研需求
        batch:       已有批次目录（续跑复用；留空新建）
        constraints: seed_dois/download_n/limit 等用户约束
        confirm:     交互确认（默认 False；--auto 时跳过）
    返回:
        {"plan": plan_v3, "plan_path": str, "batch": str}
        （不执行任何工具；执行交 executor）
    """
    from litdiscovery.common.logging import (
        create_log_dir, session_dir_for_batch, redirect_to_session,
    )

    # 每批一会话：会话目录按批次目录名命名（续跑复用，不散建多会话）
    recovery = _is_recovery_request(requirement)
    b = Path(batch) if batch else (_latest_incomplete_batch() if recovery else None)
    if recovery and not batch and b is not None:
        print(f"[Planner] 自动定位最近未完成批次: {b}")
    if b is not None:
        redirect_to_session(session_dir_for_batch(b))
        print(f"[Planner] 会话日志目录: {session_dir_for_batch(b)}")

    # 恢复调用的短指令不再交给 LLM 猜测。已有批次的 plan 是唯一事实来源，
    # 直接复用它，executor 会依据 run_state.json 跳过已成功步骤。
    if b is not None and recovery:
        existing = b / "plan.v3.json"
        if existing.exists():
            plan = json.loads(existing.read_text(encoding="utf-8"))
            errors = validate_plan(plan)
            if errors:
                raise ValueError("已有 plan.v3.json 无效: " + "; ".join(errors))
            plan["batch"] = str(b)
            plan["confirmed"] = True
            print(f"[Planner] 恢复已有计划: {existing}")
            return {"plan": plan, "plan_path": str(existing), "batch": str(b), "resumed": True}

    plan = generate_plan(requirement, constraints=constraints)
    if confirm:
        plan = _confirm_plan(plan)

    # ---- 批次解析：--batch > plan.batch > 已建 ----
    if plan.get("batch") and str(plan["batch"]).strip():
        b = Path(plan["batch"])
        b.mkdir(parents=True, exist_ok=True)
        redirect_to_session(session_dir_for_batch(b))   # plan 显式指定批次时切换会话
    elif b is None:
        batch_name = str(plan.get("batch_name") or "").strip()
        b = create_log_dir(requirement, label=batch_name or None)
        redirect_to_session(session_dir_for_batch(b))
        print(f"[Planner] 批次名称: {b.name}")
        print(f"[Planner] 会话日志目录: {session_dir_for_batch(b)}")
    b.mkdir(parents=True, exist_ok=True)

    # 走到此处要么已交互确认（_confirm_plan 置 True），要么 --auto 直通 → 视为已确认
    plan["batch"] = str(b)
    plan["confirmed"] = True
    path = save_plan(b, plan)
    print(f"[Planner] plan.v3.json 已落盘: {path}")

    return {"plan": plan, "plan_path": str(path), "batch": str(b)}
