"""
litdiscovery/agent/extractor_agent_pipeline/tables/headers.py — 表头角色分类。

把表格每一列分类为一种角色：
    material   材料标识列（名称/化学式/掺杂变体）
    property   属性数值列（d33 / ZT / 带隙 ...，property_id 指向注册表）
    structure  结构描述列（space group / crystal structure ...）
    condition  测量条件列（温度 / 压力 / x 成分 / 极化场 ...）
    unit       独立单位列
    ignore     可忽略列（编号 / 脚注 / 参考文献）
    unknown    无法判定的列

两条路径：
1. classify_headers —— 纯规则启发式（快、确定性，默认）：
   ignore → material 词 → structure 词 → 注册表符号匹配（先剥离单位）→
   独立单位 → condition 词 → 化学式 → 数值列推断属性 → unknown。
   顺序经过斟酌："Curie temperature" 必须命中注册表（curie_temperature），
   不能被 condition 词 "temperature" 抢走，故注册表在 condition 之前。
2. classify_headers_llm —— LLM 批量分类（--llm-roles 开启，规则兜底）：
   一次调用分类论文内全部表格的所有列，返回角色 + 属性名。
"""

import re
from dataclasses import dataclass, field
from typing import List, Optional

from litdiscovery.agent.extractor_agent_pipeline.tables.registry import (
    build_registry, norm_key, strip_unit, looks_like_formula, UNIT_RE,
    STRUCTURE_KEYWORDS, MATERIAL_KEYWORDS, CONDITION_KEYWORDS,
    IGNORE_KEYWORDS, UNIT_HEADER_KEYWORDS,
)
from litdiscovery.agent.extractor_agent_pipeline.tables.cells import column_is_numeric
from litdiscovery.agent.extractor_agent_pipeline.tables.extract import Table


ROLES = ("material", "property", "structure", "condition", "unit", "ignore", "unknown")


@dataclass
class ColumnRole:
    col: int
    header: str
    role: str = "unknown"
    property_id: Optional[str] = None   # 命中注册表时填充
    property_label: Optional[str] = None
    property_symbol: Optional[str] = None


@dataclass
class TableClass:
    index: int
    caption: str
    columns: List[ColumnRole] = field(default_factory=list)

    def column(self, role: str) -> List[ColumnRole]:
        return [c for c in self.columns if c.role == role]


def _match_keywords(norm: str, keywords: list) -> bool:
    for kw in keywords:
        kn = norm_key(kw)
        if kn and kn in norm:
            return True
    return False


def _classify_one(col: int, header: str, table: Table, registry: list) -> ColumnRole:
    norm = norm_key(header)
    base_norm = norm_key(strip_unit(header))
    col_cells = [r[col] if col < len(r) else "" for r in table.rows]

    # 1. 忽略列
    if _match_keywords(norm, IGNORE_KEYWORDS):
        return ColumnRole(col, header, "ignore")

    # 2. 材料标识列
    if _match_keywords(norm, MATERIAL_KEYWORDS):
        return ColumnRole(col, header, "material")

    # 3. 结构描述列
    if _match_keywords(norm, STRUCTURE_KEYWORDS):
        return ColumnRole(col, header, "structure")

    # 4. 注册表符号匹配（先剥离单位；"Curie temperature" 命中 T_C 不被 condition 抢走）
    for spec in registry:
        if spec.matches_header(base_norm, base_norm):
            return ColumnRole(col, header, "property",
                              property_id=spec.property_id,
                              property_label=spec.label,
                              property_symbol=spec.symbol)
    # ascii 变体再试一次（ε_r 表头写成 "er" 等）
    from litdiscovery.agent.extractor_agent_pipeline.tables.registry import _ascii_alias
    ascii_base = _ascii_alias(base_norm)
    if ascii_base != base_norm:
        for spec in registry:
            if spec.matches_header(base_norm, ascii_base):
                return ColumnRole(col, header, "property",
                                  property_id=spec.property_id,
                                  property_label=spec.label,
                                  property_symbol=spec.symbol)

    # 5. 独立单位列
    if (_match_keywords(norm, UNIT_HEADER_KEYWORDS)
            or (not base_norm and UNIT_RE.search(header))):
        return ColumnRole(col, header, "unit")

    # 6. 测量条件列
    # 短精确 token（"T"/"temp"）单独判定：_match_keywords 是子串匹配，
    # "t" 会误伤任何含 t 的表头，且 "T (℃)" 需在数值列兜底之前被识别。
    if base_norm in ("t", "temp") or _match_keywords(norm, CONDITION_KEYWORDS):
        return ColumnRole(col, header, "condition")

    # 7. 化学式表头 → 材料
    if looks_like_formula(header):
        return ColumnRole(col, header, "material")

    # 8. 数值列推断为属性列
    if column_is_numeric(col_cells):
        return ColumnRole(col, header, "property")

    return ColumnRole(col, header, "unknown")


def classify_headers(table: Table, registry: Optional[list] = None,
                     domain: Optional[str] = None) -> TableClass:
    """规则启发式：给单张表所有列分类。"""
    registry = registry if registry is not None else build_registry()
    cols = [_classify_one(i, h, table, registry) for i, h in enumerate(table.header)]
    return TableClass(index=table.index, caption=table.caption, columns=cols)


def _ensure_material_column(tc: TableClass) -> TableClass:
    """兜底：有属性/结构列但无材料列时，把首列标记为材料列。

    常见于无表头的 LaTeX 结果表（首行即数据，表头缺省）。
    规则抓取据此仍能产出记录供后续 LLM 裁判核验。
    """
    if not tc.columns or tc.column("material"):
        return tc
    if not tc.column("property") and not tc.column("structure"):
        return tc
    first = tc.columns[0]
    first.role = "material"
    first.property_id = None
    first.property_label = None
    first.property_symbol = None
    return tc


def classify_tables(tables: List[Table], registry: Optional[list] = None,
                    domain: Optional[str] = None) -> List[TableClass]:
    """规则启发式：批量分类多张表。"""
    return [_ensure_material_column(classify_headers(t, registry, domain))
            for t in tables]


# ============================================================
# LLM 批量表头分类（--llm-roles；失败自动回退规则）
# ============================================================
_CLASSIFY_PROMPT = """你是一个科学表格表头解析助手。

下面是一次科学实验中若干表格的表头。请对**每个表头**判定其列角色，从以下取值中选一个：
- material    : 材料标识列（材料名 / 化学式 / 掺杂变体），如 "Material"、"Composition"、"Sample"
- property    : 材料性能数值列，如 "d33"、"ZT"、"band gap"、"electrical conductivity"
- structure   : 结构描述列，如 "space group"、"crystal structure"、"lattice constant"
- condition   : 测量条件列，如 "Temperature"、"Pressure"、"x"、"poling field"
- unit        : 独立的单位列
- ignore      : 编号 / 脚注 / 参考文献等无关列
- unknown     : 无法判断

对 property 列，请额外给出规范化属性名（如 "d33"、"ZT"、"band_gap"）。

输出严格 JSON：
{{
  "tables": [
    {{
      "table": 1,
      "columns": [
        {{"col": 0, "header": "Material", "role": "material", "property": null}},
        {{"col": 1, "header": "d33 (pC/N)", "role": "property", "property": "d33"}}
      ]
    }}
  ]
}}

表格：
{tables_block}
"""


def _build_tables_block(tables: List[Table]) -> str:
    lines = []
    for t in tables:
        lines.append(f"Table {t.index} (caption: {t.caption})")
        lines.append(f"  header: {t.header}")
    return "\n".join(lines)


def classify_headers_llm(tables: List[Table], llm=None,
                         registry: Optional[list] = None,
                         domain: Optional[str] = None) -> List[TableClass]:
    """LLM 批量分类全部表头；解析失败或缺少列时回退规则结果。

    llm 为空时直接走规则。
    """
    rule_result = classify_tables(tables, registry, domain)
    if llm is None or not tables:
        return rule_result

    from litdiscovery.config import create_agent
    from litdiscovery.llm_utils import robust_json_parse

    if isinstance(llm, str):           # 传角色名则实例化
        llm = create_agent(llm, temperature=0.0, max_tokens=2048)

    block = _build_tables_block(tables)
    try:
        from langchain_core.messages import HumanMessage
        out = llm.invoke([HumanMessage(content=_CLASSIFY_PROMPT.format(tables_block=block))])
        data = robust_json_parse(out.content)
        by_table = {int(c["table"]): c.get("columns", [])
                    for c in data.get("tables", [])}
    except Exception as e:
        print(f"      [LLM-Roles] 分类失败({type(e).__name__}), 回退规则: {e}")
        return rule_result

    merged: List[TableClass] = []
    for t in tables:
        cols = by_table.get(t.index)
        if not cols:
            merged.append(rule_result[t.index - 1])
            continue
        rc = rule_result[t.index - 1]
        new_cols = []
        for i, h in enumerate(t.header):
            match = next((c for c in cols if c.get("col") == i or c.get("header") == h), None)
            if match:
                role = match.get("role")
                pid = match.get("property")
                new_cols.append(ColumnRole(
                    col=i, header=h, role=role,
                    property_id=pid,
                    property_label=pid,
                    property_symbol=pid,
                ))
            else:
                new_cols.append(rc.columns[i] if i < len(rc.columns) else ColumnRole(i, h))
        merged.append(TableClass(index=t.index, caption=t.caption, columns=new_cols))
    return merged
