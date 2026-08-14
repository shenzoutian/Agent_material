"""
orchestrator/params.py —— 参数参考库（软设置默认值单一事实源）。

工具参数（含路径）一律为"软设置"：本模块保存每个子 Agent 参数的
默认值 / 描述 / 类型，作为 planner prompt 的参考，并在 executor 执行时对
    plan.v3.json 缺省字段回填默认值（fallback）。

软设置三件套：
    params.py        ← 本模块：RESOLVED_REF 参考库（单一事实源）
    plan.v3.json     ← planner 输出，含 agent 链 + 全部参数（软设置）
    ${} 路径模板     ← executor 运行时解析（run_pipeline._resolve_templates）

原则：现有 config 常量（SNOWBALL_* / SEED_KEEP_DEFAULT / DOWNLOAD_N_AUTO_* 等）
是默认值的权威来源——这里引用它们，不重复硬编码。
"""

from litdiscovery.config import (
    DEFAULT_RESULTS_PER_KEYWORD,
    DOWNLOAD_N_AUTO_DEFAULT,
    MIN_FULLTEXT_USABLE_RATE,
    QUALITY_FLOOR_DEFAULT,
    SEED_KEEP_DEFAULT,
    SNOWBALL_MAX_CANDIDATES,
    SNOWBALL_REF_LIMIT,
    SNOWBALL_SAMPLE_PER_PAPER,
)

# agent → 参数字段 → {default, desc, type}
RESOLVED_REF = {
    "researcher_agent": {
        "keyword_count": {"default": 7, "desc": "检索关键词数量", "type": "int"},
        "results_per_keyword": {"default": DEFAULT_RESULTS_PER_KEYWORD,
                                "desc": "每关键词检索结果上限", "type": "int"},
        "sample_per_paper": {"default": SNOWBALL_SAMPLE_PER_PAPER,
                             "desc": "雪球扩容：每篇种子随机抽取的参考文献条数", "type": "int"},
        "ref_limit": {"default": SNOWBALL_REF_LIMIT,
                      "desc": "每篇种子参考文献扩展上限", "type": "int"},
        "max_candidates": {"default": SNOWBALL_MAX_CANDIDATES,
                           "desc": "雪球候选总上限", "type": "int"},
        "deep_research_model": {"default": "",
                                  "desc": "OpenAI Deep Research 模型", "type": "str"},
        "deep_research_max_tool_calls": {"default": 0,
                                           "desc": "Deep Research 最大联网工具调用数", "type": "int"},
        "memory_limit": {"default": 100, "desc": "历史文献目录最大命中数", "type": "int"},
        # 注：取舍/定稿归 filter_agent 固定链；HyDE 为固定子步骤（无条件执行）。
    },
    "filter_agent": {
        "keep_min": {"default": SEED_KEEP_DEFAULT,
                     "desc": "取舍保留下限（实际 = max(12, 关键词数×3)）", "type": "int"},
        "quality_floor": {"default": QUALITY_FLOOR_DEFAULT,
                          "desc": "确定性语料质量补齐门槛（0-100）", "type": "float"},
        "download_n": {"default": DOWNLOAD_N_AUTO_DEFAULT,
                       "desc": "定稿保留篇数上限（0=全部）", "type": "int"},
        "pdf": {"default": False, "desc": "True 走 PDF 下载路径", "type": "bool"},
        "pdf_only": {"default": False, "desc": "预处理只转 PDF（默认全部格式）", "type": "bool"},
    },
    "extractor_agent": {
        "domain": {"default": "thermoelectric",
                   "desc": "回退属性域（thermoelectric/ferroelectric/piezoelectric/phasechange）",
                   "type": "str"},
        "limit": {"default": 2000, "desc": "最多处理新篇数", "type": "int"},
        "min_fulltext_usable_rate": {"default": MIN_FULLTEXT_USABLE_RATE,
                                      "desc": "进入抽取前的全文可用率硬门", "type": "float"},
        "allow_low_quality": {"default": False,
                               "desc": "显式绕过全文质量门（仅调试）", "type": "bool"},
        "domain_registry": {"default": "",
                            "desc": "显式属性域注册表 JSON（可空；空则 LLM 生成/回退静态域）",
                            "type": "str"},
    },
    "gap_chain": {
        "skip_llm": {"default": False,
                     "desc": "跳过 gap 链的 LLM 调用（概念提取 + 裁决，省调用）", "type": "bool"},
    },
    "validate": {
        "formulas": {"default": "", "desc": "逗号分隔化学式（留空从 gap_output 提取）",
                     "type": "str"},
    },
    "report_writer": {
        "sections": {"default": [], "desc": "报告章节（逗号分隔字符串）", "type": "str"},
    },
}


def resolve_params(agent: str, given: dict = None) -> dict:
    """按 RESOLVED_REF 回填默认值，返回合并后的软设置参数表。

    - given 缺失字段 → 用参考库默认值；
    - given 额外字段（如 seed_dois，未进 schema）→ 原样保留；
    - 返回值为最终执行参数（类型保持，供 runbook 步骤直接使用）。
    """
    given = given or {}
    ref = RESOLVED_REF.get(agent, {})
    merged = {name: spec["default"] for name, spec in ref.items()}
    for name, value in given.items():
        if value is not None:
            merged[name] = value
    return merged


def render_params_reference(agent: str) -> str:
    """渲染单个 agent 的参数参考文本（默认值 + 描述），供 planner prompt 注入。"""
    lines = []
    for name, spec in RESOLVED_REF.get(agent, {}).items():
        lines.append(f"    {name}（{spec['type']}，默认 {spec['default']}）：{spec['desc']}")
    return "\n".join(lines)
