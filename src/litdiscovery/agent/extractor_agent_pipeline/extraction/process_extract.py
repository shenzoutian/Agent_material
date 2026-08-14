"""
stages/extraction/process_extract.py —— extractor_agent 子能力：分类门 + 工艺提取。

classify_paper_type（process/property/both/none 路由 + 属性域识别）与
extract_process_flow（工艺步骤 + 材料优势）；PROCESS_EXTRACTION 位于
prompts/extractor_prompts/process.py。
"""

from litdiscovery.agent.agent_roles.prompts import PROPERTY_DOMAINS
from litdiscovery.agent.agent_roles.prompts.extractor_prompts import PROCESS_EXTRACTION
from litdiscovery.llm_utils import (
    robust_json_parse,
    invoke_messages,
    render,
    _PROCESS_HINT_TEMPLATE,
)


_ROUTE_VALUES = ("process", "property", "both", "none")


def build_domain_descriptions(registry=None) -> str:
    """拼接所有属性域 + 工艺类的描述行，供分类门使用。

    registry: 动态属性域注册表（dict）；若有则追加一行（label + 关键词），
    使分类门能命中 LLM 生成的动态域。
    """
    lines = []
    for domain_key, domain in PROPERTY_DOMAINS.items():
        label = domain.get("label", domain_key)
        keywords = domain.get("material_keywords", [])
        kw = "、".join(str(k) for k in keywords[:6]) if keywords else "（未定义关键词）"
        lines.append(f'- {domain_key}（{label}）: 关键词 {kw}')
    if registry and registry.get("properties"):
        label = registry.get("label") or "自定义属性域"
        kw = "、".join(str(k) for k in registry.get("material_keywords", [])[:6]) or "（未定义关键词）"
        lines.append(f'- <dynamic>（{label}）: 关键词 {kw}')
    lines.append("- process（工艺）: 关键词 " + "、".join(PROCESS_EXTRACTION.get("keywords", [])))
    return "\n".join(lines)


def classify_paper_type(fulltext: str, llm, registry=None) -> dict:
    """判断论文类型并识别属性域。

    registry: 动态属性域注册表（dict）；property_domain 命中其 label 也算合法。

    返回 dict:
        route: "process" | "property" | "both" | "none"
        property_domain: PROPERTY_DOMAINS 键或动态域 label；route 非 property/both 时为 None
        reason: 一句话理由
    """
    prompts = PROCESS_EXTRACTION["prompts"]["classify"]
    system, user = render(
        prompts["system"], prompts["user"],
        system_kwargs={"domain_descriptions": build_domain_descriptions(registry)},
        fulltext=fulltext,
    )
    out = invoke_messages(llm, system, user)
    data = robust_json_parse(out.content)

    route = str(data.get("route", "")).strip().lower()
    if route not in _ROUTE_VALUES:
        route = "none"  # 解析失败视为 none，走最省的跳过路径
    if route in ("property", "both"):
        prop_domain = str(data.get("property_domain", "")).strip().lower()
        known = set(PROPERTY_DOMAINS)
        if registry and registry.get("label"):
            known.add(str(registry["label"]).strip().lower())
        prop_domain = prop_domain if prop_domain in known else None
    else:
        prop_domain = None
    return {
        "route": route,
        "property_domain": prop_domain,
        "reason": str(data.get("reason", "")),
    }


def extract_process_flow(fulltext: str, llm, material_names: list = None,
                         domain: str = "") -> dict:
    """提取制备工艺流程步骤列表与材料优势。

    返回 {"materials": [...], "process": {"process_name", "steps"}, "notes"}
    解析失败时返回空骨架，不抛错。
    """
    prompts = PROCESS_EXTRACTION["prompts"]["process"]
    if material_names:
        hint = _PROCESS_HINT_TEMPLATE.format(
            names=", ".join(f'"{n}"' for n in material_names))
    else:
        hint = ""

    domain_rules = ""
    if domain == "phasechange":
        domain_rules = """

相变材料专项要求：区分靶材/前驱体组成与最终薄膜组成；提取沉积方式、基底、膜厚、
沉积温度、功率/压力/气氛、退火温度/时间/升温速率和冷却方式。器件工艺需区分
材料制备、图形化、电极和电脉冲操作。每个参数保留原始单位，并在 notes 或 source
中给出最短充分原句；未绑定到具体步骤的参数不得强行填入。
"""
    system, user = render(
        prompts["system"] + domain_rules, prompts["user"],
        material_hint=hint,
        fulltext=fulltext,
    )
    try:
        out = invoke_messages(llm, system, user)
        data = robust_json_parse(out.content)
    except Exception as e:
        print("❌ Process extraction failed:", e)
        data = {}

    if not isinstance(data, dict):
        data = {}
    # 补全骨架，保证下游 write_node 可直接落盘
    data.setdefault("materials", [])
    process = data.get("process")
    if not isinstance(process, dict):
        process = {}
    process.setdefault("process_name", "")
    process.setdefault("steps", [])
    data["process"] = process
    data.setdefault("notes", "")
    return data
