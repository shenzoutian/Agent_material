"""
litdiscovery/agent/extractor_agent_pipeline/tables/api.py —— 表格解析编排库（提取 + 表头分类 + 规则抓取）。

run_table_parse(args) 接收统一 CLI `litdiscovery tables` 解析好的参数
（argparse.Namespace），批量处理含 fulltext.md 的论文文件夹：
    提取 pipe 表格 → 表头角色分类（规则，或 --llm-roles 用 LLM）→ 规则抓取记录

产出（写入论文文件夹）：
    table{i}.csv + table{i}_caption.md   供 LLM 表格提取节点消费
    tables_rules.json                     规则抓取通用记录 + 域 schema + 结构
"""

import sys
from pathlib import Path

from litdiscovery.agent.extractor_agent_pipeline.tables.extract import extract_tables_from_file
from litdiscovery.agent.extractor_agent_pipeline.tables.headers import classify_headers_llm, classify_tables
from litdiscovery.agent.extractor_agent_pipeline.tables.rules import extract_all_records
from litdiscovery.agent.extractor_agent_pipeline.tables.schema import to_domain_schema, structure_schema
from litdiscovery.agent.extractor_agent_pipeline.tables.output import write_table_csvs, write_rules_json
from litdiscovery.agent.extractor_agent_pipeline.tables.evidence import build_table_evidence_from_tables


def _role_summary(tclass) -> str:
    parts = []
    for c in tclass.columns:
        if c.role in ("property", "structure") and c.property_id:
            parts.append(f"[{c.col}]{c.role}:{c.property_id or c.header}")
        else:
            parts.append(f"[{c.col}]{c.role}")
    return ", ".join(parts)


def process_folder(folder: Path, *, domain=None, llm_roles=False,
                   write_csv=True, write_rules=True, verbose=False) -> dict:
    """处理单个论文文件夹：提取表格 + 表头分类 + 规则抓取 + 落盘。返回统计 dict。"""
    md_path = folder / "fulltext.md"
    tables = extract_tables_from_file(md_path)

    if not tables:
        print(f"  [Skip] {folder.name}: 无 pipe 表格")
        return {"folder": str(folder), "n_tables": 0, "n_records": 0}

    if llm_roles:
        classes = classify_headers_llm(tables, llm="extractor_agent")
    else:
        classes = classify_tables(tables)

    records = extract_all_records(tables, classes)

    if verbose:
        for t, tc in zip(tables, classes):
            print(f"    Table {t.index} ({t.caption[:40]}...)")
            print(f"      header: {t.header}")
            print(f"      roles : {_role_summary(tc)}")

    if write_csv:
        csv_paths = write_table_csvs(folder, tables)
        print(f"  [CSV] {len(csv_paths)} 张表 → {folder}")

    tables_schema = structure = None
    if domain:
        # The standalone CLI and extractor graph share this rule-derived schema.
        tables_schema = {"materials": build_table_evidence_from_tables(
            tables, domain, classes=classes)["materials"]}
        structure = structure_schema(records)

    if write_rules:
        path = write_rules_json(
            folder, records, domain=domain,
            tables_schema=tables_schema, structure=structure,
            n_tables=len(tables),
            meta={"llm_roles": llm_roles})
        print(f"  [Rules] {len(records)} 条记录 → {path.name}")
        if domain:
            n_mat = len(tables_schema.get("materials", []))
            print(f"  [Domain:{domain}] {n_mat} 个材料条目")

    return {"folder": str(folder), "n_tables": len(tables), "n_records": len(records)}


def run_table_parse(args) -> None:
    """批量表格解析（--folder 单篇优先；否则 --base-dir 或最新批次 end_mds/）。"""
    folders = []
    if getattr(args, "folder", None):
        folders = [Path(args.folder)]
    else:
        base_dir = getattr(args, "base_dir", None) or getattr(args, "batch", None)
        if base_dir:
            base = Path(base_dir)
        else:
            # 默认取最新批次的 end_mds/（与 paths.latest_batch 单一事实源对齐）
            from litdiscovery.paths import latest_batch
            base = Path(latest_batch(require_end_mds=False)) / "end_mds"
        if not base.is_dir():
            print(f"[ERROR] 批量目录不存在: {base}（用 --base-dir 或 --folder 指定）")
            sys.exit(1)
        folders = [p for p in base.iterdir() if p.is_dir() and (p / "fulltext.md").exists()]
        folders.sort()

    if not folders:
        print("[ERROR] 未找到含 fulltext.md 的文件夹（用 --folder 或 --base-dir 指定）")
        sys.exit(1)

    domain = getattr(args, "domain", None)
    llm_roles = getattr(args, "llm_roles", False)
    print(f"[TableParse] 处理 {len(folders)} 个文件夹"
          + (f" (domain={domain})" if domain else "")
          + (" (LLM 表头)" if llm_roles else " (规则表头)"))

    summary = []
    for folder in folders:
        summary.append(process_folder(
            folder, domain=domain, llm_roles=llm_roles,
            write_csv=not getattr(args, "no_csv", False),
            write_rules=not getattr(args, "no_rules", False),
            verbose=getattr(args, "verbose", False)))

    n_tables = sum(s["n_tables"] for s in summary)
    n_records = sum(s["n_records"] for s in summary)
    print("=" * 60)
    print(f"[Summary] {len(folders)} 文件夹, {n_tables} 张表, {n_records} 条规则记录")
