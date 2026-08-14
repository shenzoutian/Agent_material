"""
stages/extraction/domain_registry.py —— 动态属性域注册表。

把静态四域（agent_roles/prompts/extractor_prompts/<domain>.py）的能力泛化为运行时注册表，新增属性域无需写新文件：

- validate_domain_registry: 校验 planner/LLM 给定的注册表结构（Pydantic），返回错误列表
- build_prompts_from_registry: 由注册表生成五条提取 prompt（material_candidates /
  properties / structure / tables / judge），产物与静态四域同构
- normalize_domain: 统一 str（静态四域键 / 动态域 label）与 dict（注册表/完整域）→ 完整域 dict
- generate_domain_registry: registry_generator 子能力（需求 → LLM 生成注册表 → 校验 →
  回退静态四域），保证提取环节永远可用

域注册表来源优先级：
  ① planner 显式给定 domain_registry → 校验 + 生成 prompts
  ② extractor_agent 依据需求 LLM 生成（registry_generator 子能力）→ Pydantic 校验
  ③ 缺省回退 → 现有静态四域之一

注册表 schema（domain_registry.json）：
    {
      "label": "压电薄膜",
      "material_keywords": ["ScAlN", "AlScN", "piezoelectric thin film"],
      "properties": {
        "<属性id>": {
          "symbol": "d_33", "label": "压电常数d33", "field": "piezoelectric_coefficient_d33",
          "numeric_key": "d_33_value", "unit_key": "d_33_unit",
          "temperature_key": "d_33_Temperature", "temperature_unit_key": "d_33_Temp_unit",
          "aliases": ["..."],
        }
      }
    }
    每个属性必填 field + numeric_key；unit_key / temperature_key / temperature_unit_key
    可选（无量纲属性可省略，与静态四域中 k_p 等一致）。
"""

from typing import Optional

from pydantic import BaseModel, Field, ValidationError

from litdiscovery.agent.agent_roles.prompts import PROPERTY_DOMAINS

ENTRY_CONTEXT_FIELDS = (
    "sample_form", "phase_state", "measurement_method", "heating_rate",
    "film_thickness", "pulse_type", "pulse_width", "crystallization_definition",
    "value_origin", "endurance_basis", "evidence_quote", "evidence_section",
    "evidence_page", "evidence_table", "evidence_table_row", "evidence_table_column",
)


# ============================================================
# Pydantic 校验模型
# ============================================================

class PropertySpec(BaseModel):
    field: str
    numeric_key: str = ""
    symbol: str = ""
    label: str = ""
    unit_key: Optional[str] = None
    temperature_key: Optional[str] = None
    temperature_unit_key: Optional[str] = None
    aliases: list = Field(default_factory=list)


class DomainRegistrySpec(BaseModel):
    label: str = "自定义属性域"
    material_keywords: list = Field(default_factory=list)
    properties: dict[str, PropertySpec]


def validate_domain_registry(reg) -> list:
    """校验注册表结构，返回错误列表（空列表 = 通过）。

    规则：properties 必填非空，每个属性必含 field + numeric_key；
    单位/温度键可省略。返回错误人类可读，供工具/日志展示。
    """
    errors = []
    if not isinstance(reg, dict):
        return ["注册表必须是 dict"]
    if not isinstance(reg.get("properties"), dict) or not reg["properties"]:
        return ["properties 必须是非空 dict（属性注册表）"]
    for name, check in (("label", str), ("material_keywords", list)):
        if name in reg and not isinstance(reg[name], check):
            errors.append(f"{name} 必须是 {check.__name__}")
    try:
        DomainRegistrySpec(**reg)
    except ValidationError as e:
        for err in e.errors():
            loc = ".".join(str(x) for x in err["loc"])
            errors.append(f"{loc}: {err['msg']}")
    return errors


# ============================================================
# prompt 模板生成（产物与静态四域同构，供 render_prompt_pair 消费）
# ============================================================

# .format() 模板占位符（render() 渲染时替换）；正文用 @@NAME@ sentinel 避免与字面花括号混淆
_PLACEHOLDERS = {
    "@@MAX_MATERIALS@": "{max_materials}",
    "@@FULLTEXT@": "{fulltext}",
    "@@MATERIAL_HINT@": "{material_hint}",
    "@@COMBINED_BLOCK@": "{combined_block}",
    "@@TABLE_CONTEXT@": "{table_context}",
    "@@MERGED_JSON@": "{merged_json}",
}


def _escape_template(body: str) -> str:
    """把含真实花括号的正文转成 .format() 模板：字面花括号转义，占位符 sentinel 还原。"""
    t = body.replace("{", "{{").replace("}", "}}")
    for sentinel, ph in _PLACEHOLDERS.items():
        t = t.replace(sentinel, ph)
    return t


def _prop_lines(reg: dict) -> str:
    """提取 prompt 罗列用的属性行（中文名 + 符号），供 material_candidates/properties 等使用。"""
    lines = []
    for pid, p in reg["properties"].items():
        label = p.get("label") or p.get("symbol") or pid
        sym = p.get("symbol")
        lines.append(f"- {label}" + (f"（{sym}）" if sym else ""))
    return "\n".join(lines)


def _prop_json_section(reg: dict) -> str:
    """properties 提取 JSON 模板的每个属性一行（含数值/单位/温度键，省略 None 键）。"""
    lines = []
    for pid, p in reg["properties"].items():
        inner = []
        if p.get("numeric_key"):
            inner.append(f'"{p["numeric_key"]}": ...')
        if p.get("unit_key"):
            inner.append(f'"{p["unit_key"]}": "..."')
        if p.get("temperature_key"):
            inner.append(f'"{p["temperature_key"]}": "..."')
        if p.get("temperature_unit_key"):
            inner.append(f'"{p["temperature_unit_key"]}": "..."')
        inner.extend(f'"{name}": null' for name in ENTRY_CONTEXT_FIELDS)
        lines.append(f'      "{pid}": [{{{", ".join(inner)}}}],')
    return "\n".join(lines)


def _build_prompts(label: str, kw: str, prop_lines: str, prop_json: str) -> dict:
    """由域元数据生成五条 prompt 模板（material_candidates/properties/structure/tables/judge）。"""
    structure_fields = ("compound_type、crystal_structure、lattice_structure、space_group、"
                        "doping_type、dopants、processing_method")
    return {
        "material_candidates": {
            "system": _escape_template(
                f"""你是一个科学文献阅读助手。从以下文本中，列出那些在附近任何位置提及了{label}性能
（如{kw}）的材料名称。

材料名称示例：化合物、合金、掺杂变体，如"PZT"、"BaTiO3"、"LiNbO3"、"AlN"、"ZnO"、"PVDF"等。

规则：
- 仅包含至少讨论了一种{label}性能的材料。
- 保留原文中的名称写法（包括相关的掺杂剂/相标签）。
- 返回一个包含单个数组"materials"的JSON对象。
- 去重处理。

返回JSON格式：
{{
  "materials": ["...", "..."]
}}"""),
            "user": _escape_template(
                """最多返回前@@MAX_MATERIALS@项。

文本：
```@@FULLTEXT@@```"""),
        },
        "properties": {
            "system": _escape_template(
                f"""你是一个{label}材料研究提取助手。

请从文本中提取以下每种材料的{label}性能数据：
- name：仅材料名称，不含额外字符串标签。
{prop_lines}

对于每个性能参数，请提取**数值**及其对应的**温度**和**单位**（如有提及）。

注意事项：
- 缺失值必须严格设为 null。
- 仅包含材料名称，不含额外的字符串标签。
- 不要自行进行任何计算或单位换算。
- 如果存在多个值，将它们作为独立的字典条目全部返回。
- 如找到超过10种材料，只保留前10种。
- 所有字段名和字符串值必须使用**合法的JSON语法**（双引号）。
- 数值保持为数字类型，不加引号（即非字符串）。
- 输出中严格不包含其他任何内容。

返回结构化JSON格式：
{{
  "materials": [
    {{
      "name": "...",
{prop_json}
    }}
  ]
}}"""),
            "user": _escape_template(
                """@@MATERIAL_HINT@文本：
```@@FULLTEXT@@```"""),
        },
        "structure": {
            "system": _escape_template(
                f"""你是一个{label}材料结构信息提取助手。

对于每种材料，请提取以下结构信息：
- name：仅材料名称, 不含额外字符串标签。
- {structure_fields}

注意事项：
- 缺失值必须严格设为 null。
- 仅包含材料名称，不含额外的字符串标签。
- 如找到超过10种材料，只保留前10种。
- 所有字段名和字符串值必须使用**合法的JSON语法**（双引号）。
- 所有字段值必须遵循**合法的JSON语法**，使用双引号。
- 输出中严格不包含其他任何内容。

返回JSON格式：
{{
  "materials": [
    {{
      "name": "...",
      "compound_type": "<类型|null>",
      "crystal_structure": "<结构|null>",
      "lattice_structure": "<结构|null>",
      "space_group": "<群组|null>",
      "doping": {{
        "doping_type": "<类型|null>",
        "dopants": [<字符串列表>]
      }},
      "processing_method": "<字符串|null>"
    }}
  ]
}}"""),
            "user": _escape_template(
                """@@MATERIAL_HINT@文本：
```@@FULLTEXT@@```"""),
        },
        "tables": {
            "system": _escape_template(
                f"""你是一个{label}材料科学表格提取助手。

以下是一篇科学论文中所有表格及其标题的集合。

请提取表格中涉及的所有材料，并返回每种材料的以下性能数据：

{label}性能：
{prop_lines}

结构信息：
- compound_type（化合物类型）
- crystal_structure（晶体结构）
- lattice_structure（晶格结构）
- space_group（空间群）
- doping_type（掺杂类型）及dopants（掺杂剂列表）
- processing_method（加工方法）

注意事项：
- 缺失值必须严格设为 null。
- 仅包含材料名称，不含额外的字符串标签。
- 如找到超过10种材料，只保留前10种。
- 所有字段名和字符串值必须使用**合法的JSON语法**（双引号）。
- 所有缺失值必须严格明确设为 null。
- 所有字段值必须遵循**合法的JSON语法**，使用双引号。
- 输出中严格不包含其他任何内容。

返回结构化JSON格式：
{{
  "materials": [
    {{
      "name": "...",
{prop_json}
      "compound_type": "<类型|null>",
      "crystal_structure": "<结构|null>",
      "lattice_structure": "<结构|null>",
      "space_group": "<群组|null>",
      "doping": {{
        "doping_type": "<类型|null>",
        "dopants": [<字符串列表>]
      }},
      "processing_method": "<字符串|null>"
    }}
  ]
}}

所有缺失值必须严格明确设为 null。"""),
            "user": _escape_template(
                """@@MATERIAL_HINT@
### 表格及标题：
@@COMBINED_BLOCK@@"""),
        },
        "judge": {
            "system": _escape_template(
                f"""你是一个科学验证助手。

请对照给定的论文全文、带标题的表格数据以及已提取的材料JSON，
验证每一项**{label}数值性能**及其**温度上下文**。

仅需验证的{label}性能参数：
{prop_lines}

数值验证规则：
- 对于每个数值，检查文本或表格中是否出现了相同或相近的值（及其温度，如有提及）。
- 若数值和温度均一致 → 标记为 correct（正确）。
- 若数值正确但温度不一致 → 标记为 temp_mismatch（温度不匹配）。
- 若数值未找到或不合理 → 标记为 incorrect（不正确）。
- 若温度缺失但数值匹配 → 标记为 correct_no_temp（正确但无温度）。
- 绝不凭空编造、求平均或修改数字。

结构验证规则：
- 对于每种材料，检查其结构字段（compound_type、crystal_structure、
  lattice_structure、space_group、doping_type、processing_method）
  是否存在且语法有效。
- 若以上字段全部存在或为 null（但格式正确）→ 标记 structure_ok（结构正确）。
- 不得删除结构信息。

返回严格合法的JSON格式：
{{
  "correct": {{
     "材料名": {{"<symbol>": [数值]}}
  }},
  "incorrect": {{
     "材料名": {{"<symbol>": [数值]}}
  }},
  "temp_mismatch": {{
     "材料名": {{"<symbol>": [{{"value":数值,"reported_T":温度,"found_T":温度}}]}}
  }},
  "structure_ok": ["材料A","材料B"],
  "notes": "简要说明"
}}

务必包含全部四个顶级键，即使为空也要包含。
所有键和字符串值使用双引号。
整体输出用 {{ }} 包裹。"""),
            "user": _escape_template(
                """### 论文全文
```@@FULLTEXT@@```

### 表格标题与数据
```@@TABLE_CONTEXT@@```

### 已提取值的合并JSON
```@@MERGED_JSON@@```"""),
        },
    }


def build_prompts_from_registry(reg: dict) -> dict:
    """由注册表 spec 生成完整域 dict（label/material_keywords/properties/prompts）。"""
    label = reg.get("label") or "自定义属性域"
    kw = "、".join(str(x) for x in reg.get("material_keywords", [])[:8]) or "（未定义关键词）"
    return {
        "label": label,
        "material_keywords": reg.get("material_keywords", []),
        "properties": reg["properties"],
        "prompts": _build_prompts(label, kw, _prop_lines(reg), _prop_json_section(reg)),
    }


# ============================================================
# 域解析 / 生成
# ============================================================

def normalize_domain(domain, registry: Optional[dict] = None) -> dict:
    """统一 str（静态四域键 / 动态域 label）与 dict（注册表/完整域）输入 → 完整域 dict。

    - dict：含 prompts 视为完整域直接复用，否则按 spec 生成 prompts；
    - str：静态四域键直接查；否则若与 registry 的 label 匹配则用动态域；
    - 兜底：回退静态 thermoelectric（保证提取永远可用）。
    """
    if isinstance(domain, dict):
        if "prompts" in domain and "properties" in domain:
            return domain
        return build_prompts_from_registry(domain)
    key = domain or "thermoelectric"
    if key in PROPERTY_DOMAINS:
        return PROPERTY_DOMAINS[key]
    if isinstance(registry, dict) and key in (
            registry.get("label"),
            *[str(x) for x in registry.get("material_keywords", [])]):
        return registry if "prompts" in registry else build_prompts_from_registry(registry)
    return PROPERTY_DOMAINS.get(key, PROPERTY_DOMAINS["thermoelectric"])


def generate_domain_registry(requirement: str, llm,
                            fallback_domain: str = "thermoelectric") -> dict:
    """registry_generator 子能力：依据需求 LLM 生成属性域注册表 → 校验 → 生成 prompts。

    无需求 / LLM 输出非法 / 校验失败 → 回退静态四域（fallback_domain）。
    返回完整域 dict，回退时带 "_fallback": True 标记（调用方据此说明来源）。
    """
    fallback = PROPERTY_DOMAINS.get(fallback_domain, PROPERTY_DOMAINS["thermoelectric"])
    if not requirement or not requirement.strip():
        return {**fallback, "_fallback": True}

    from langchain_core.messages import HumanMessage, SystemMessage
    from litdiscovery.llm_utils import robust_json_parse

    system = (
        "你是材料科学属性域定义专家。给定科研需求，定义一个动态属性域注册表（JSON）。\n"
        '结构：{"label": "属性域中文名", "material_keywords": ["材料关键词", ...], '
        '"properties": {"<属性id>": {"symbol": "符号", "label": "中文名", '
        '"field": "性能字段名", "numeric_key": "数值键", "unit_key": "单位键", '
        '"temperature_key": "温度键", "temperature_unit_key": "温度单位键", '
        '"aliases": ["别名", ...]}}}\n'
        "每个属性必须含 field 和 numeric_key；单位/温度键可选（无量纲属性可省略）。\n"
        "只输出 JSON，不要其他内容。"
    )
    reg = None
    try:
        out = llm.invoke([SystemMessage(content=system),
                          HumanMessage(content=f"科研需求：{requirement}")])
        text = getattr(out, "content", str(out))
        parsed = robust_json_parse(text)
        if isinstance(parsed, dict):
            reg = parsed
    except Exception:
        reg = None
    if reg is not None and not validate_domain_registry(reg):
        return build_prompts_from_registry(reg)
    return {**fallback, "_fallback": True}
