"""
orchestrator/agent_directory.py —— 子 Agent 目录（planner 唯一掌握的"世界模型"）。

planner 作为路由agent，只持有本目录：
每个子 Agent 的能力描述 + 参数 schema（引用 params.RESOLVED_REF）+ 默认步骤模板。

AGENT_DIRECTORY[agent] 结构：
    {
      "stage":      阶段名（用于运行状态和报告分组）
      "description": 能力描述（注入 planner prompt）
      "steps":      默认确定性步骤模板（executor 展开执行）：
                    - tool: build_tools() 里的工具名
                    - kind: "copy" / "hyde"（非工具步骤）
                    - args: 可含 {requirement}/{batch}/{hyde:terms} 等模板占位，
                            由 run_pipeline._resolve_templates 运行时解析；
                            可含 {p:param} 由 plan_to_runbook 用 plan 参数回填。
    }

executor 执行 plan.v3.json 时，把 agents[].agent 展开为 steps 模板、
用 params + 默认值回填 {p:} 占位符，再交给 run_pipeline 确定性执行。
"""

from litdiscovery.agent.orchestrator.params import render_params_reference
from litdiscovery.agent.agent_roles.registry import ROLE_DESCRIPTIONS

# 子 Agent 目录（阶段 → 能力 → 默认步骤模板）
AGENT_DIRECTORY = {
    "researcher_agent": {
        "stage": "retrieve",
        "requires": [],
        "provides": ["paper_set"],
        "description": ROLE_DESCRIPTIONS["researcher_agent"],
        "steps": [
            {"stage": "retrieve", "kind": "hyde", "args": {"requirement": "{requirement}"}},
            {"stage": "retrieve", "tool": "generate_keywords",
             "args": {"requirement": "{requirement}", "count": "{p:keyword_count}",
                      "context": "{hyde:terms}"}},
            {"stage": "retrieve", "tool": "search_papers",
             "args": {"requirement": "{requirement}", "keywords": "{prev:generate_keywords}",
                      "results_per_keyword": "{p:results_per_keyword}"}},
            {"stage": "retrieve", "tool": "deep_research_papers",
             "args": {"requirement": "{requirement}", "batch": "{batch}",
                      "model": "{p:deep_research_model}",
                      "max_tool_calls": "{p:deep_research_max_tool_calls}"}},
            {"stage": "retrieve", "tool": "search_memory_papers",
             "args": {"requirement": "{requirement}", "batch": "{batch}",
                      "limit": "{p:memory_limit}"}},
            {"stage": "retrieve", "tool": "snowball_expand",
             "args": {"seeds_file": "search_results.json",
                      "sample_per_paper": "{p:sample_per_paper}",
                      "ref_limit": "{p:ref_limit}",
                      "max_candidates": "{p:max_candidates}"}},
            {"stage": "retrieve", "tool": "write_doi_list",
             "args": {"batch": "{batch}", "source": "search_results.json",
                      "merge_source": "snowball_candidates.json",
                      "merge_sources": "deep_research_results.json,memory_papers.json"}},
        ],
    },
    "filter_agent": {
        "stage": "fulltext",
        "requires": ["paper_set"],
        "provides": ["fulltext_corpus"],
        "description": ROLE_DESCRIPTIONS["filter_agent"],
        "steps": [
            {"stage": "fulltext", "tool": "choose_papers",
             "args": {"requirement": "{requirement}", "papers_file": "doi_list.json",
                      "min_keep": "{p:keep_min}", "quality_floor": "{p:quality_floor}"}},
            {"stage": "fulltext", "tool": "finalize_batch",
             "args": {"download_n": "{p:download_n}"}},
            {"stage": "fulltext", "tool": "fetch_fulltext",
             "args": {"batch": "{batch}", "pdf": "{p:pdf}"}},
            {"stage": "fulltext", "tool": "preprocess",
             "args": {"batch": "{batch}", "pdf_only": "{p:pdf_only}"}},
        ],
    },
    "extractor_agent": {
        "stage": "extract",
        "requires": ["fulltext_corpus"],
        "provides": ["claim_set"],
        "description": ROLE_DESCRIPTIONS["extractor_agent"],
        "steps": [
            {"stage": "extract", "tool": "write_domain_registry",
             "args": {"batch": "{batch}", "requirement": "{requirement}",
                      "domain_registry": "{p:domain_registry}",
                      "fallback_domain": "{p:domain}"}},
            {"stage": "extract", "tool": "extract_batch",
             "args": {"batch": "{batch}", "domain": "{p:domain}", "limit": "{p:limit}",
                      "domain_registry_file": "domain_registry.json",
                      "min_fulltext_usable_rate": "{p:min_fulltext_usable_rate}",
                      "allow_low_quality": "{p:allow_low_quality}"}},
        ],
    },
    "gap_chain": {
        "stage": "gap",
        "requires": ["claim_set"],
        "provides": ["gap_set"],
        "description": "research-gap 链：物化三表 → pandas 检测 → LLM 裁决 → 报告",
        "steps": [
            {"stage": "gap", "tool": "materialize_gap", "args": {"batch": "{batch}"}},
            {"stage": "gap", "tool": "materialize_evidence", "args": {"batch": "{batch}"}},
            {"stage": "gap", "tool": "detect_gaps", "args": {"batch": "{batch}"}},
            {"stage": "gap", "tool": "adjudicate_gaps", "args": {"batch": "{batch}"}},
            {"stage": "gap", "tool": "write_gap_report", "args": {"batch": "{batch}"}},
        ],
    },
    "validate": {
        "stage": "validate",
        "requires": ["claim_set"],
        "provides": ["validation_set"],
        "description": "材料数据库验证：MP/OQMD/AFLOW 交叉核对化学式，产出 validation/<formula>/comparison",
        "steps": [
            {"stage": "validate", "tool": "validate_formulas",
             "args": {"formulas": "{p:formulas}", "batch": "{batch}"}},
        ],
    },
    "report_writer": {
        "stage": "report",
        "requires": [],
        "provides": ["report"],
        "description": ROLE_DESCRIPTIONS["report_writer"],
        "steps": [
            {"stage": "report", "tool": "write_report",
             "args": {"batch": "{batch}", "sections": "{p:sections}"}},
        ],
    },
    "review_agent": {
        "stage": "review",
        "requires": [],
        "provides": ["run_review"],
        "description": "运行审查：读取 run_state.json 与执行日志，归因失败步骤并给出重试/修复建议",
        "steps": [
            {"stage": "review", "tool": "review_run", "args": {"batch": "{batch}"}},
        ],
    },
}


def render_directory() -> str:
    """渲染 AGENT_DIRECTORY 为 planner prompt 文本（能力 + 参数参考）。"""
    lines = ["可用子 Agent 目录（按阶段选择，不必全部使用）："]
    for agent, cfg in AGENT_DIRECTORY.items():
        lines.append(f"- {agent} [{cfg['stage']}] {cfg['description']}")
        ref = render_params_reference(agent)
        if ref:
            lines.append(f"  参数参考（软设置，默认值仅作参考，须结合用户输入确定）：\n{ref}")
    return "\n".join(lines)
