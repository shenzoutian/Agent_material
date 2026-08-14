"""
litdiscovery/agent/agent_roles/registry.py —— 角色→工具菜单的纯数据 + list_roles 逻辑（无 langchain）。

tools.py 的 @tool 装饰器依赖 langchain；本模块保持零依赖，使 `litdiscovery.agent.agent_roles.list_roles`
在无 langchain 环境下也可用（CLI roles 命令、测试）。

ROLE_DESCRIPTIONS 是角色职责描述的**唯一事实源**：`orchestrator/agent_directory.py` 的
`AGENT_DIRECTORY[*].description` 从这里派生（planner 与 CLI roles 菜单共用同一文本），
`config.AGENT_ROLES` 不再保存描述。
"""

# 角色 → 工具映射
ROLE_TOOL_MAP = {
    "researcher_agent": [
        "generate_keywords", "search_papers", "deep_research_papers",
        "search_memory_papers", "snowball_expand", "write_doi_list",
    ],
    "filter_agent": [
        "choose_papers", "finalize_batch", "fetch_fulltext", "preprocess",
    ],
    "extractor_agent": [
        "write_domain_registry", "classify_paper", "extract_process",
        "extract_materials", "extract_property", "extract_structure",
        "extract_tables", "judge_properties", "extract_batch",
    ],
    "gap_concept_extractor": ["materialize_gap", "materialize_evidence"],
    "gap_adjudicator": ["adjudicate_gaps"],
    "report_writer": ["write_report"],
    "knowledge_indexer": ["index_knowledge", "search_knowledge"],
    "_stage": [
        "memory", "write_extraction", "detect_gaps",
        "write_gap_report", "validate_formulas",
    ],
}

# 角色描述（单一事实源；agent_directory.AGENT_DIRECTORY 从此派生其 description）
ROLE_DESCRIPTIONS = {
    "researcher_agent": "科研文献检索：HyDE 拆分需求 → 生成关键词 → 检索论文 → 雪球扩展（随机抽每篇参考文献）→ 收敛 doi_list",
    "filter_agent": "全文获取：取舍（doi_list 全集，从宽）→ 定稿下载列表 → 下载 → to_markdown → end_mds",
    "extractor_agent": "属性/结构/表格/工艺提取：动态属性域注册表（显式/LLM 生成/回退静态四域）→ 分类门 → 全批次提取（performance/structure/process.json）",
    "gap_concept_extractor": "摘要概念词提取（research-gap 语料物化）",
    "gap_adjudicator": "research-gap 候选裁决（排除假阳性）",
    "report_writer": "聚合检索/提取/gap 各阶段产物，生成中文结构化调研报告（md + json）",
    "knowledge_indexer": "知识库沉淀（index / search）",
    "_stage": "流水线预置能力（非绑定角色）",
}


def format_role_menu(query: str = "") -> str:
    """格式化角色菜单文本（list_roles 的核心逻辑，纯函数）。"""
    q = (query or "").strip().lower()
    lines = ["角色菜单："]
    for role, tools_ in ROLE_TOOL_MAP.items():
        label = role if role != "_stage" else "流水线预置"
        desc = ROLE_DESCRIPTIONS.get(role, "")
        if q and q not in role and q not in desc.lower():
            continue
        lines.append(f"- {label} {desc}\n    工具: {', '.join(tools_)}")
    return "\n".join(lines)
