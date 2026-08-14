"""
litdiscovery/agent/extractor_agent_pipeline/tables/output.py — 落盘输出。

write_table_csvs    table{i}.csv + table{i}_caption.md
                    —— 命名与预处理链的表格节点一致，
                    使 fulltext-only 文件夹也能进入 LLM 表格提取路径。
write_rules_json    tables_rules.json —— 规则抓取通用记录 + 可选域 schema + 结构。
"""

import csv
import json
from pathlib import Path
from typing import List, Optional

from litdiscovery.agent.extractor_agent_pipeline.tables.extract import Table
from litdiscovery.agent.extractor_agent_pipeline.tables.rules import Record


def _csv_safe(v) -> str:
    """单元格规范为单行文本，防止嵌入换行破坏 CSV 记录结构。"""
    return str(v).replace("\r\n", " ").replace("\r", " ").replace("\n", " ")


def write_table_csvs(folder, tables: List[Table]) -> List[Path]:
    """写 table{i}.csv + table{i}_caption.md，返回写入的 csv 路径列表。"""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    written = []
    for t in tables:
        csv_path = folder / f"table{t.index}.csv"
        rows = (([t.header] if t.header else []) + list(t.rows))
        safe = [[_csv_safe(c) for c in row] for row in rows]
        try:
            with open(csv_path, "w", encoding="utf-8", newline="") as f:
                csv.writer(f).writerows(safe)
        except csv.Error:
            # 兜底：QUOTE_ALL 全量引号，任何单元格都不再触发 “need to escape”
            with open(csv_path, "w", encoding="utf-8", newline="") as f:
                csv.writer(f, quoting=csv.QUOTE_ALL).writerows(safe)
        caption_path = folder / f"table{t.index}_caption.md"
        caption_path.write_text(t.caption, encoding="utf-8")
        written.append(csv_path)
    return written


def _record_to_dict(r: Record) -> dict:
    return {
        "material": r.material,
        "table": r.table,
        "row": r.row,
        "property": r.property_id,
        "property_label": r.property_label,
        "property_symbol": r.property_symbol,
        "kind": r.kind,
        "value": r.value,
        "unit": r.unit,
        "raw": r.raw,
        "condition": r.condition,
    }


def write_rules_json(folder, records: List[Record],
                     domain: Optional[str] = None,
                     tables_schema: Optional[dict] = None,
                     structure: Optional[dict] = None,
                     n_tables: int = 0,
                     meta: Optional[dict] = None) -> Path:
    """写 tables_rules.json（通用记录 + 可选域 schema + 结构）。"""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    out = {
        "source_folder": str(folder),
        "domain": domain,
        "n_tables": n_tables,
        "n_records": len(records),
        "meta": meta or {},
        "records": [_record_to_dict(r) for r in records],
    }
    if tables_schema is not None:
        out["tables_schema"] = tables_schema
    if structure is not None:
        out["structure_schema"] = structure
    path = folder / "tables_rules.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
