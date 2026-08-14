"""Build deterministic table evidence for the extraction judge."""

from dataclasses import asdict

from litdiscovery.agent.extractor_agent_pipeline.tables.extract import Table
from litdiscovery.agent.extractor_agent_pipeline.tables.headers import classify_tables
from litdiscovery.agent.extractor_agent_pipeline.tables.registry import build_registry
from litdiscovery.agent.extractor_agent_pipeline.tables.rules import extract_all_records
from litdiscovery.agent.extractor_agent_pipeline.tables.schema import to_domain_schema
from litdiscovery.agent.extractor_agent_pipeline.extraction.domain_registry import normalize_domain


def build_table_evidence_from_tables(tables: list[Table], domain,
                                     source_tables: list | None = None,
                                     classes: list | None = None) -> dict:
    """Return the shared rule-derived table artifact with row-level provenance."""
    if not tables:
        return {"materials": [], "records": []}

    active_domain = normalize_domain(domain)
    registry = build_registry({"active": active_domain})
    records = extract_all_records(
        tables, classes or classify_tables(tables, registry=registry))
    tables_by_index = {table.index: table for table in tables}
    serialized_records = []
    for record in records:
        payload = asdict(record)
        source = source_tables[record.table - 1] if source_tables else {}
        source_rows = source.get("rows") or []
        payload["source_filename"] = source.get("filename", f"table{record.table}.csv")
        table = tables_by_index.get(record.table)
        payload["source_caption"] = source.get("caption", table.caption if table else f"Table {record.table}")
        payload["source_row"] = (
            source_rows[record.row].get("__table_row", record.row + 2)
            if record.row < len(source_rows) else record.row + 2
        )
        serialized_records.append(payload)
    return {
        "materials": to_domain_schema(records, domain).get("materials", []),
        "records": serialized_records,
    }


def build_table_evidence(table_data: list, domain) -> dict:
    """Adapt loaded CSV rows into the shared deterministic table artifact."""
    tables = []
    for index, raw_table in enumerate(table_data or [], start=1):
        rows = raw_table.get("rows") or []
        if not rows:
            continue
        header = [name for name in rows[0] if name != "__table_row"]
        table_rows = [[str(row.get(name, "")) for name in header] for row in rows]
        tables.append(Table(
            index=index,
            caption=str(raw_table.get("caption") or f"Table {index}"),
            header=header,
            rows=table_rows,
        ))

    return build_table_evidence_from_tables(tables, domain, table_data)
