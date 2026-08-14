"""
litdiscovery/agent/extractor_agent_pipeline/tables — 表格解析脚手架。

流水线：fulltext.md → extract_tables（pipe 表格，分隔行确认）
               → classify_headers / classify_headers_llm（表头角色分类）
               → extract_records（规则抓取，确定性）
               → to_domain_schema / structure_schema（映射属性域材料 JSON）
               → write_table_csvs / write_rules_json（落盘）

与提取链的衔接：
- 产出 table{i}.csv + table{i}_caption.md → LLM 表格提取节点直接消费；
- 产出 tables_rules.json（含域 schema）→ 与 performance.json / structure.json 合并，
  或直接作为规则抓取结果（无需 LLM，零成本）。
"""

from litdiscovery.agent.extractor_agent_pipeline.tables.extract import Table, extract_tables, extract_tables_from_file
from litdiscovery.agent.extractor_agent_pipeline.tables.registry import build_registry, norm_key, match_key
from litdiscovery.agent.extractor_agent_pipeline.tables.headers import (
    ColumnRole, TableClass, classify_headers, classify_tables, classify_headers_llm,
)
from litdiscovery.agent.extractor_agent_pipeline.tables.cells import parse_cell, parse_number, parse_unit, column_is_numeric
from litdiscovery.agent.extractor_agent_pipeline.tables.rules import Record, extract_records, extract_all_records
from litdiscovery.agent.extractor_agent_pipeline.tables.schema import to_domain_schema, structure_schema
from litdiscovery.agent.extractor_agent_pipeline.tables.output import write_table_csvs, write_rules_json

__all__ = [
    "Table", "extract_tables", "extract_tables_from_file",
    "build_registry", "norm_key", "match_key",
    "ColumnRole", "TableClass",
    "classify_headers", "classify_tables", "classify_headers_llm",
    "parse_cell", "parse_number", "parse_unit", "column_is_numeric",
    "Record", "extract_records", "extract_all_records",
    "to_domain_schema", "structure_schema",
    "write_table_csvs", "write_rules_json",
]
