"""
litdiscovery/llm_utils.py —— LLM 消息调用 + JSON 鲁棒解析共享底座。

parse_json_text / robust_json_parse / read_fulltext / render /
build_material_hint / build_combined_block / invoke_messages 等，
供 extraction / gap / report / tables 复用。依赖方向：llm_utils → config | common | langchain。

不放进本模块（留 stages/extraction/）：
    render_prompt_pair（由 stages.extraction.prompting 提供）
    _build_property_map / _normalize_judge_key（judge 专用）
    _build_domain_descriptions（classify 专用）
"""

import os
import re
import ast
import json
from typing import Any, Optional

# json5 为宽松解析的增强项，缺失时优雅降级（parse_json_text 仍可用 json 与 ast）
try:
    import json5
except ImportError:  # pragma: no cover
    json5 = None

from langchain_core.prompts import PromptTemplate
from langchain_core.messages import SystemMessage, HumanMessage

# ============================================================
# JSON / 基础工具
# ============================================================


def parse_json_text(text):
    """从 LLM 输出中恢复 JSON（dict 或 list），失败抛 ValueError。

    解析策略：清理 Markdown 标记 / 尾部逗号后依次尝试
        json.loads → json5 宽松解析 → ast.literal_eval → 单引号定界符兜底。
    不再全局替换单引号与 None，避免破坏字符串内部的撇号与字面量 "None"。
    与 robust_json_parse 的区别：本函数解析失败时抛异常，由调用方决定
    降级策略；robust_json_parse 返回默认 dict。planner 等需要"失败即
    走降级分支"的调用方复用本函数。
    """
    # hasattr check for AIMessage from .invoke()
    if hasattr(text, "content"):
        text = text.content

    # Strip Markdown formatting
    text = str(text).strip().removeprefix("```json").removesuffix("```").strip()

    # Try to extract first complete JSON object or array
    match = re.search(r'(\{.*\}|\[.*\])', text, re.DOTALL)
    if match:
        text = match.group(1)

    # Clean trailing commas
    text = re.sub(r',\s*([\]}])', r'\1', text)

    # 1. 标准 JSON（保留字符串内部的撇号）
    try:
        return json.loads(text)
    except Exception:
        pass

    # 2. JSON5 宽松解析（单引号 / 注释 / 尾逗号）
    if json5 is not None:
        try:
            return json5.loads(text)
        except Exception:
            pass

    # 3. Python 字面量（单引号字符串、None/True/False 等）
    try:
        return ast.literal_eval(text)
    except Exception:
        pass

    # 4. 兜底：单引号 JSON 定界符 → 双引号（仅在以上均失败时；撇号场景已被第 1 步命中）
    try:
        return json.loads(text.replace("'", '"'))
    except Exception:
        pass

    raise ValueError(f"JSON 解析失败，无法从输出恢复结构: {text[:200]!r}")


def robust_json_parse(text, default=None):
    """鲁棒 JSON 解析：成功返回解析值，失败返回 default（默认 {"materials": []}）。

    复用 parse_json_text 的全部恢复策略；唯一区别是失败时不抛异常，
    适合数据提取等"宁可空判也不中断"的场景。
    """
    try:
        return parse_json_text(text)
    except Exception:
        return default if default is not None else {"materials": []}


def read_fulltext(file_path: str) -> str:
    """以UTF-8编码读取指定路径的文本文件，返回全部内容。"""
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


# LLM 提取/分类的全文截断上限（字符）——超出则截断，避免 context 超限。
# DeepSeek 上下文约 100 万 token；单篇文献全文超限时截断保留头部（标题/摘要/引言）。
LLM_FULLTEXT_MAX_CHARS = 80_000


def read_fulltext_for_llm(file_path: str, max_chars: int = LLM_FULLTEXT_MAX_CHARS) -> str:
    """读取全文供 LLM 使用；超长时截断到头部 max_chars 字符并提示。

    论文全文可能远超 LLM context（如 250KB 长文），直接全量传入会触发
    context 超限。截断保留头部（通常含标题/摘要/引言/方法），
    对分类门与提取足够，对 5MB 级全文更是必要保护。
    """
    text = read_fulltext(file_path)
    if max_chars and len(text) > max_chars:
        print(f"  [WARN] fulltext 过长（{len(text)} 字符），截断到 {max_chars} 字符供 LLM 使用")
        return text[:max_chars]
    return text


# ============================================================
# Prompt 渲染共享助手
# ============================================================

# 属性/结构链的"仅提取以下材料"提示脚手架
_MATERIAL_HINT_TEMPLATES = {
    "properties": '仅提取以下材料的条目（忽略其他材料，除非名称明确匹配其变体）："{names}"。\n',
    "structure":  "仅提取以下材料的结构性能数据：{names}。\n",
    "tables":     "仅提取以下材料的数据：{names}。\n",
}

# 工艺提取的 material_hint 脚手架
_PROCESS_HINT_TEMPLATE = '仅总结以下材料（及其变体）的优势："{names}"。\n'


def build_material_hint(material_names: Optional[list], kind: str) -> str:
    """构造限定材料范围的提示前缀。material_names 为空时返回空串。"""
    if not material_names:
        return ""
    formatted = ", ".join(f'"{name}"' for name in material_names)
    return _MATERIAL_HINT_TEMPLATES[kind].format(names=formatted)


def build_combined_block(table_data: list) -> str:
    """将所有表格的标题与 CSV 数据拼接为一个文本块。"""
    combined_block = ""
    for i, table in enumerate(table_data, 1):
        combined_block += f"### 表格 {i} 标题：\n{table['caption']}\n\n"
        combined_block += f"### 表格 {i} CSV数据：\n{json.dumps(table['rows'], indent=2)}\n\n"
    return combined_block


def render(system_tpl: str, user_tpl: str, system_kwargs: dict = None, **user_kwargs) -> tuple:
    """渲染 system/user 两段 prompt。

    system 段若含 {placeholder}（如 domain_descriptions），经 system_kwargs 填充；
    user 段用 user_kwargs 填充；模板中的 {{ }} 由 .format() 反转义为 JSON 字面量。
    """
    system = PromptTemplate.from_template(system_tpl).format(**(system_kwargs or {}))
    user = PromptTemplate.from_template(user_tpl).format(**user_kwargs)
    return system, user


def invoke_messages(llm, system: str, user: str):
    """以 system/user 两条消息调用 LLM，返回 AIMessage（带 .content）。"""
    return llm.invoke([
        SystemMessage(content=system),
        HumanMessage(content=user),
    ])
