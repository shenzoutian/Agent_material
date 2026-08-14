"""
stages/extraction/property_extract.py —— extractor_agent 的属性/结构/表格提取。

extract_material_candidates / extract_properties / extract_structural_properties /
extract_from_tables（LLM 无关，llm 实例由调用方传入）。
"""

from typing import Dict, List, Optional

from litdiscovery.llm_utils import (
    robust_json_parse,
    invoke_messages,
    build_material_hint,
    build_combined_block,
)
from litdiscovery.agent.extractor_agent_pipeline.extraction.prompting import render_prompt_pair


def extract_material_candidates(fulltext: str, llm, max_materials: int = 20,
                                *, domain: str = "thermoelectric") -> List[str]:
    """返回在文本中与属性性能关联出现的材料名称列表（去重）。"""
    system, user = render_prompt_pair(
        domain, "material_candidates", fulltext=fulltext, max_materials=max_materials)
    out = invoke_messages(llm, system, user)
    data = robust_json_parse(out.content)
    mats = data.get("materials", [])
    seen = set()
    result = []
    for m in mats:
        if not m or not isinstance(m, str):
            continue
        key = m.strip()
        if key and key not in seen:
            seen.add(key)
            result.append(key)
    return result


def extract_properties(fulltext: str, llm, material_names: Optional[List[str]] = None,
                       *, domain: str = "thermoelectric") -> Dict:
    """提取属性性能的结构化数据。"""
    material_hint = build_material_hint(material_names, "properties")
    system, user = render_prompt_pair(
        domain, "properties", fulltext=fulltext, material_hint=material_hint)
    output = invoke_messages(llm, system, user)
    return robust_json_parse(output.content)


def extract_structural_properties(fulltext: str, llm, material_names: list = None,
                                  *, domain: str = "thermoelectric") -> Dict:
    """提取材料结构信息的结构化数据。"""
    material_hint = build_material_hint(material_names, "structure")
    system, user = render_prompt_pair(
        domain, "structure", fulltext=fulltext, material_hint=material_hint)
    output = invoke_messages(llm, system, user)
    return robust_json_parse(output.content)


def extract_from_tables(table_data: list, llm, material_names: list = None,
                        *, domain: str = "thermoelectric") -> dict:
    """从论文表格中批量提取属性与结构数据。"""
    if not table_data:
        return {"materials": []}

    material_hint = build_material_hint(material_names, "tables")
    combined_block = build_combined_block(table_data)
    system, user = render_prompt_pair(
        domain, "tables", material_hint=material_hint, combined_block=combined_block)

    try:
        output = invoke_messages(llm, system, user)
        return robust_json_parse(output.content)
    except Exception as e:
        print("❌ Table extraction failed:", e)
        return {"materials": []}
