"""
litdiscovery/agent/extractor_agent_pipeline/extraction/graph.py —— 分类门 + 选择性并行提取工作流（LangGraph）。

配置经 RuntimeCfg 显式传入（无模块级全局状态），图状态承载路由数据。
"""

import os
from pathlib import Path
from typing import TypedDict, Optional, Any
from concurrent.futures import ThreadPoolExecutor

from langgraph.graph import StateGraph, END

from litdiscovery.config import create_agent, MAX_FULLTEXT_BYTES, MIN_FULLTEXT_BYTES
from litdiscovery.llm_utils import read_fulltext
from litdiscovery.agent.extractor_agent_pipeline.extraction.domain_registry import normalize_domain
from litdiscovery.agent.extractor_agent_pipeline.extraction.property_extract import (
    extract_material_candidates,
    extract_properties,
    extract_structural_properties,
)
from litdiscovery.agent.extractor_agent_pipeline.extraction.judge import judge_verify_properties
from litdiscovery.agent.extractor_agent_pipeline.extraction.process_extract import (
    classify_paper_type,
    extract_process_flow,
)
from litdiscovery.agent.extractor_agent_pipeline.extraction.evidence_passages import select_task_passages
from litdiscovery.agent.extractor_agent_pipeline.extraction.phasechange_normalize import normalize_phasechange_output
from litdiscovery.agent.extractor_agent_pipeline.tables.evidence import build_table_evidence


class RuntimeCfg:
    """图运行配置（替代原模块级 DOMAIN/BASE_DIR/SESSION_LOG）。"""

    def __init__(self, domain: str = "thermoelectric",
                 session_log: Path | None = None,
                 data_doi_dir: Path | None = None,
                 registry: dict | None = None):
        self.domain = domain
        self.session_log = session_log
        # 提取产物目录 artifacts/extracted/<批次名>/（write_node 落盘用）
        self.data_doi_dir = data_doi_dir or Path("data_doi")
        # 动态属性域注册表：write_domain_registry 产物，可为完整域或 spec；
        # classify 门命中其 label 时，state.domain 由 normalize_domain 解析为完整域。
        self.registry = registry


# === LangGraph 状态定义 ===
class State(TypedDict):
    folder: Path
    fulltext: Optional[str]
    llm: Optional[Any]
    route: Optional[str]
    domain: Optional[Any]  # 完整域 dict（str 键经 normalize_domain 解析后写入）
    material_names: Optional[list]
    thermo: Optional[dict]
    structure: Optional[dict]
    process: Optional[dict]
    retries: int
    table_data: Optional[list]
    table_json_output: Optional[dict]
    table_evidence: Optional[dict]
    total_table_rows: Optional[int]
    skip: bool


# === 节点1: 读取论文全文（含超大/过小文档跳过） ===
def read_node(state: State, cfg: RuntimeCfg) -> State:
    folder = state["folder"]
    md = folder / "fulltext.md"
    if MAX_FULLTEXT_BYTES or MIN_FULLTEXT_BYTES:
        try:
            size = md.stat().st_size
            if MAX_FULLTEXT_BYTES and size > MAX_FULLTEXT_BYTES:
                print(f"[Skip] Skipping {folder.name} due to fulltext.md too large "
                      f"(> {MAX_FULLTEXT_BYTES / 1024 / 1024:.0f} MB)")
                return {**state, "fulltext": None, "skip": True}
            # 下限：仅有摘要/无正文（.too_small 标记或文本过小）→ 跳过，不占用 LLM 资源
            if MIN_FULLTEXT_BYTES and 0 < size < MIN_FULLTEXT_BYTES:
                print(f"[Skip] Skipping {folder.name} due to fulltext.md too small "
                      f"(< {MIN_FULLTEXT_BYTES}B，仅有摘要/无正文)")
                return {**state, "fulltext": None, "skip": True}
        except OSError:
            pass
    if (folder / ".too_small").exists():
        print(f"[Skip] Skipping {folder.name} due to .too_small marker")
        return {**state, "fulltext": None, "skip": True}
    text = read_fulltext(md)
    return {**state, "fulltext": text, "retries": 0}


# === 节点2: 根据 token_count.txt 动态设置 LLM 的 max_tokens ===
def set_tokens_node(state: State, cfg: RuntimeCfg) -> State:
    folder = state["folder"]
    token_file = folder / "token_count.txt"
    try:
        with open(token_file, "r") as f:
            token_count = int(f.read().strip())
    except Exception:
        print(f"[WARN] Could not read token_count.txt in {folder.name}, defaulting to 1000.")
        token_count = 1000

    if token_count == 0:
        print(f"[Skip] Skipping {folder.name} due to token_count = 0")
        return {**state, "skip": True}

    if token_count <= 1000:
        max_tok = 786
    else:
        extra = (token_count - 1000) // 500
        max_tok = 786 + (256 * extra)
        max_tok = min(max_tok, 8129)

    print(f"[LLM] Setting max_tokens = {max_tok} for {folder.name} (token_count = {token_count})")
    dynamic_llm = create_agent("extractor_agent", max_tokens=max_tok)
    return {**state, "llm": dynamic_llm, "skip": False}


# === 节点3: 分类门 ===
def classify_domain_node(state: State, cfg: RuntimeCfg) -> State:
    small_llm = create_agent("extractor_agent", max_tokens=256)
    result = classify_paper_type(state["fulltext"], llm=small_llm, registry=cfg.registry)
    route = result["route"]
    prop_domain = result["property_domain"]
    print(f"[Route] {state['folder'].name} -> {route}"
          + (f" (domain={prop_domain})" if prop_domain else "")
          + f" | {result['reason']}")
    if route == "none":
        return {**state, "route": route, "domain": None, "skip": True}
    if route in ("property", "both"):
        # 统一解析为完整域 dict（静态键 / 动态域 label → PROPERTY_DOMAINS / registry）
        dom = normalize_domain(prop_domain or cfg.domain, cfg.registry)
        return {**state, "route": route, "domain": dom, "skip": False}
    return {**state, "route": route, "domain": None, "skip": False}


# === 节点4: 查找候选材料 ===
def find_materials_node(state: State, cfg: RuntimeCfg) -> State:
    small_llm = create_agent("extractor_agent", max_tokens=256)
    candidates = extract_material_candidates(
        state["fulltext"], llm=small_llm, max_materials=20,
        domain=state.get("domain") or cfg.domain)
    if candidates:
        print(f"[Mat] Candidate materials: {len(candidates)} -> {candidates[:8]}{'...' if len(candidates) > 8 else ''}")
        return {**state, "material_names": candidates, "skip": False}
    if state.get("route") == "property":
        print("[Stop] No property-related materials found -> skipping data extraction.")
        return {**state, "material_names": [], "skip": True}
    print("[Route] No property materials found, downgrading both -> process")
    return {**state, "material_names": [], "route": "process", "skip": False}


# === 节点5: 提取属性性能数据 ===
def extract_property_node(state: State, cfg: RuntimeCfg) -> State:
    domain = state.get("domain") or cfg.domain
    evidence_context = select_task_passages(state["fulltext"], domain, "property")
    thermo = extract_properties(
        evidence_context,
        llm=state["llm"],
        material_names=state.get("material_names") or None,
        domain=domain
    )
    label = domain.get("label", "") if isinstance(domain, dict) else str(domain)
    if "相变" in label or "phasechange" in label.lower():
        thermo = normalize_phasechange_output(thermo)
    if not thermo.get("materials") or not isinstance(thermo["materials"], list):
        raise ValueError("[ERROR] Property extraction returned no valid materials.")
    return {**state, "thermo": thermo}


# === 节点6: 提取结构性能数据 ===
def extract_structure_node(state: State, cfg: RuntimeCfg) -> State:
    merged = state.get("material_names") or []
    struct = extract_structural_properties(
        select_task_passages(state["fulltext"], state.get("domain") or cfg.domain, "structure"),
        llm=state["llm"],
        material_names=merged or None,
        domain=state.get("domain") or cfg.domain)
    if not struct.get("materials"):
        raise ValueError("[ERROR] Structure JSON parse failed or empty.")
    return {**state, "structure": struct, "material_names": merged}


# === 节点7: 统计表格行数并规划 token 预算 ===
def count_table_and_plan_tokens_node(state: State, cfg: RuntimeCfg) -> State:
    folder = state["folder"]
    table_data = []
    total_rows = 0
    i = 1
    while True:
        csv_path = folder / f"table{i}.csv"
        caption_path = folder / f"table{i}_caption.md"
        if not csv_path.exists() or not caption_path.exists():
            break
        try:
            import pandas as pd
            df = pd.read_csv(csv_path)
            with open(caption_path, "r", encoding="utf-8") as f:
                caption = f.read().strip()
            row_count = len(df)
            rows = df.to_dict(orient="records")
            for row_number, row in enumerate(rows, start=2):
                row["__table_row"] = row_number
            table_data.append({
                "filename": f"table{i}.csv",
                "caption": caption,
                "rows": rows,
                "row_count": row_count
            })
            total_rows += row_count
        except Exception as e:
            print(f"[WARN] Failed reading {csv_path.name}: {e}")
        i += 1

    print(f"[Tables] Found {len(table_data)} tables with {total_rows} rows (deterministic extraction)")
    return {**state, "table_data": table_data, "total_table_rows": total_rows}


# === 节点8: 统一表格链（规则分类 + 确定性记录 → 领域 JSON） ===
def extract_table_json_node(state: State, cfg: RuntimeCfg) -> State:
    if not state.get("table_data"):
        return {**state, "table_json_output": {"materials": []},
                "table_evidence": {"materials": [], "records": []}}
    table_evidence = build_table_evidence(
        state["table_data"], state.get("domain") or cfg.domain)
    return {**state, "table_json_output": {"materials": table_evidence["materials"]},
            "table_evidence": table_evidence}


# === 节点9: LLM 裁判验证节点 ===
def judge_node(state: State, cfg: RuntimeCfg) -> State:
    folder = state["folder"]
    llm_judge = create_agent("extractor_agent", temperature=0.0, max_tokens=2500)
    try:
        print(f"[Judge] Running property-level LLM Judge for {folder.name}...")
        judged = judge_verify_properties(
            fulltext=select_task_passages(
                state["fulltext"], state.get("domain") or cfg.domain, "property"),
            thermo_json=state.get("thermo"),
            structure_json=state.get("structure"),
            table_json=state.get("table_json_output"),
            table_data=state.get("table_data"),
            deterministic_table_json=state.get("table_evidence"),
            llm=llm_judge,
            folder_name=folder.name,
            domain=state.get("domain") or cfg.domain,
            log_path=str(cfg.session_log / "judge_validation.log") if cfg.session_log else None,
        )
        return {**state, "thermo": judged}
    except Exception as e:
        import traceback
        log_path = "judge_error_log.txt"
        with open(log_path, "a", encoding="utf-8") as log:
            log.write("\n" + "=" * 60 + "\n")
            log.write(f"FOLDER: {folder.name}\n")
            log.write(f"ERROR TYPE: {type(e).__name__}\n")
            log.write(f"DETAILS: {repr(e)}\n")
            log.write(f"TRACEBACK:\n{traceback.format_exc()}\n")
            log.write("=" * 60 + "\n")
        print(f"[WARN] Judge node crashed in {folder.name}: {e}")
        return state


# === 节点10: 工艺 + 优势提取 ===
def process_extract_node(state: State, cfg: RuntimeCfg) -> State:
    llm = create_agent("extractor_agent")
    result = extract_process_flow(
        select_task_passages(state["fulltext"], state.get("domain") or cfg.domain, "process"),
        llm=llm,
        material_names=state.get("material_names") or None,
        domain=(state.get("domain") or cfg.domain)
        if isinstance(state.get("domain") or cfg.domain, str) else "",
    )
    n_steps = len(result.get("process", {}).get("steps", []))
    n_mats = len(result.get("materials", []))
    print(f"[Process] Extracted {n_steps} steps, {n_mats} materials for {state['folder'].name}")
    return {**state, "process": result}


def parallel_extract_node(state: State, cfg: RuntimeCfg) -> State:
    """Run independent extraction tasks concurrently after shared preparation."""
    tasks = {}
    with ThreadPoolExecutor(max_workers=4) as executor:
        if state.get("route") in ("property", "both"):
            tasks["property"] = executor.submit(extract_property_node, state, cfg)
            tasks["structure"] = executor.submit(extract_structure_node, state, cfg)
        if state.get("route") in ("property", "both", "process"):
            tasks["tables"] = executor.submit(extract_table_json_node, state, cfg)
        if state.get("route") in ("process", "both"):
            tasks["process"] = executor.submit(process_extract_node, state, cfg)

    updates = {}
    for task, future in tasks.items():
        result = future.result()
        if task == "property":
            updates["thermo"] = result.get("thermo")
        elif task == "structure":
            updates["structure"] = result.get("structure")
        elif task == "tables":
            updates["table_json_output"] = result.get("table_json_output")
            updates["table_evidence"] = result.get("table_evidence")
        elif task == "process":
            updates["process"] = result.get("process")
    return {**state, **updates}


# === 节点11: 保存输出结果到 JSON 文件 ===
def write_node(state: State, cfg: RuntimeCfg) -> State:
    folder = state["folder"]
    out_dir = cfg.data_doi_dir / folder.name
    out_dir.mkdir(parents=True, exist_ok=True)

    if state.get("thermo") is not None:
        with open(out_dir / "performance.json", "w", encoding="utf-8") as f:
            import json
            json.dump(state["thermo"], f, indent=2)
    if state.get("structure") is not None:
        with open(out_dir / "structure.json", "w", encoding="utf-8") as f:
            import json
            json.dump(state["structure"], f, indent=2)
    if state.get("table_json_output") and state["table_json_output"].get("materials"):
        with open(out_dir / "tables_output.json", "w", encoding="utf-8") as f:
            import json
            json.dump(state["table_json_output"], f, indent=2)
    if state.get("process") is not None:
        with open(out_dir / "process.json", "w", encoding="utf-8") as f:
            import json
            json.dump(state["process"], f, indent=2, ensure_ascii=False)

    print(f"[OK] Done: {folder.name}")
    return state


# === 条件路由函数 ===
def after_set_tokens(state: State) -> str:
    return END if state.get("skip") else "classify_domain"


def after_classify(state: State) -> str:
    route = state.get("route")
    if route == "none":
        return END
    if route == "process":
        return "Prepare_table_data"
    return "Find_materials"


def after_materials(state: State) -> str:
    if state.get("skip"):
        return END
    if state.get("route") == "process":
        return "Prepare_table_data"
    return "Prepare_table_data"


def start_parallel_extraction(state: State) -> str:
    return "Parallel_extract"


def after_parallel_extraction(state: State) -> str:
    return "Judge_verification" if state.get("route") in ("property", "both") else "Write_json"


# === LangGraph 工作流图构建 ===
def build_graph(cfg: RuntimeCfg):
    graph = StateGraph(State)
    graph.add_node("read_file", lambda s: read_node(s, cfg))
    graph.add_node("set_tokens", lambda s: set_tokens_node(s, cfg))
    graph.add_node("classify_domain", lambda s: classify_domain_node(s, cfg))
    graph.add_node("Find_materials", lambda s: find_materials_node(s, cfg))
    graph.add_node("Prepare_table_data", lambda s: count_table_and_plan_tokens_node(s, cfg))
    graph.add_node("Judge_verification", lambda s: judge_node(s, cfg))
    graph.add_node("Parallel_extract", lambda s: parallel_extract_node(s, cfg))
    graph.add_node("Write_json", lambda s: write_node(s, cfg))

    graph.set_entry_point("read_file")
    graph.add_edge("read_file", "set_tokens")

    graph.add_conditional_edges("set_tokens", after_set_tokens, {
        END: END,
        "classify_domain": "classify_domain",
    })
    graph.add_conditional_edges("classify_domain", after_classify, {
        END: END,
        "Find_materials": "Find_materials",
        "Prepare_table_data": "Prepare_table_data",
    })
    graph.add_conditional_edges("Find_materials", after_materials, {
        END: END,
        "Prepare_table_data": "Prepare_table_data",
    })
    graph.add_conditional_edges("Prepare_table_data", start_parallel_extraction, {
        "Parallel_extract": "Parallel_extract",
    })
    graph.add_conditional_edges("Parallel_extract", after_parallel_extraction, {
        "Judge_verification": "Judge_verification", "Write_json": "Write_json"})
    graph.add_edge("Judge_verification", "Write_json")
    graph.add_edge("Write_json", END)

    app = graph.compile()
    print("[Graph] Graph compiled.")
    return app
