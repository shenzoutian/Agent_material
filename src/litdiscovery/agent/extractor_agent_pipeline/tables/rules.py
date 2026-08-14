"""
litdiscovery/agent/extractor_agent_pipeline/tables/rules.py — 规则抓取（确定性提取，不调用 LLM）。

给定已分类的表头（TableClass），逐行把 材料列 × 属性列 交叉提取为 Record：
    material → 属性值(value+unit) + 测量条件(condition)

特性：
- 材料列合并单元格：空材料单元格向前继承（换行续行常见）；
- 属性列 value 解析交给 cells.parse_cell（范围取下限、科学计数、单位分离）；
- 结构列（structure 角色）按文本记录（kind="structure"，供 structure.json 合并）；
- 一列多值：单元格内的 "220, 240" 暂取主值（范围已处理），完整多值保留在 raw。

输出为通用记录流（domain 无关），由 schema.py 映射到具体属性域材料 JSON。
"""

from dataclasses import dataclass, field
from typing import List, Optional

from litdiscovery.agent.extractor_agent_pipeline.tables.extract import Table
from litdiscovery.agent.extractor_agent_pipeline.tables.headers import TableClass
from litdiscovery.agent.extractor_agent_pipeline.tables.cells import parse_cell
from litdiscovery.agent.extractor_agent_pipeline.tables.registry import UNIT_RE


@dataclass
class Record:
    material: str
    table: int
    row: int
    property_id: str
    property_label: str = ""
    property_symbol: str = ""
    kind: str = "numeric"               # numeric | structure | text
    value: Optional[float] = None
    unit: Optional[str] = None
    raw: str = ""
    condition: dict = field(default_factory=dict)


def _condition_dict(tclass: TableClass, row_cells: List[str]) -> dict:
    cond = {}
    for c in tclass.column("condition"):
        idx = c.col
        if idx >= len(row_cells):
            continue
        text = row_cells[idx].strip()
        if not text or text in ("—", "-", "n/a", "None"):
            continue
        pc = parse_cell(text)
        # 单元格无单位时继承表头单位（"T (°C)" 列，单元格只写数值）
        unit = pc["unit"]
        if pc["value"] is not None and not unit:
            hu = UNIT_RE.search(c.header)
            if hu:
                unit = hu.group(0)
        cond[c.header] = {
            "value": pc["value"],
            "unit": unit,
            "raw": text,
        }
    return cond


def extract_records(table: Table, tclass: TableClass) -> List[Record]:
    """从一张表 + 表头分类提取通用记录流。"""
    mat_cols = tclass.column("material")
    prop_cols = tclass.column("property")
    struct_cols = tclass.column("structure")

    records: List[Record] = []
    last_material = ""

    for ridx, row in enumerate(table.rows):
        # 材料：合并单元格继承
        material = ""
        for c in mat_cols:
            if c.col < len(row) and row[c.col].strip():
                material = row[c.col].strip()
                break
        if material:
            last_material = material
        elif last_material:
            material = last_material
        if not material:
            continue

        cond = _condition_dict(tclass, row)

        # 数值属性列
        for c in prop_cols:
            if c.col >= len(row):
                continue
            raw = row[c.col].strip()
            if not raw or raw in ("—", "-", "n/a", "None"):
                continue
            pc = parse_cell(raw)
            records.append(Record(
                material=material, table=table.index, row=ridx,
                property_id=c.property_id or c.header,
                property_label=c.property_label or "",
                property_symbol=c.property_symbol or "",
                kind="numeric",
                value=pc["value"], unit=pc["unit"], raw=raw,
                condition=cond,
            ))

        # 结构描述列（文本记录）
        for c in struct_cols:
            if c.col >= len(row):
                continue
            raw = row[c.col].strip()
            if not raw or raw in ("—", "-", "n/a", "None"):
                continue
            records.append(Record(
                material=material, table=table.index, row=ridx,
                property_id=c.header, property_label=c.header,
                property_symbol="", kind="structure",
                value=None, unit=None, raw=raw, condition=cond,
            ))

    return records


def extract_all_records(tables: List[Table], classes: List[TableClass]) -> List[Record]:
    records: List[Record] = []
    for t, tc in zip(tables, classes):
        records.extend(extract_records(t, tc))
    return records
