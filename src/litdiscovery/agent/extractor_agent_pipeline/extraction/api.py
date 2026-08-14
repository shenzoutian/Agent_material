"""
litdiscovery/agent/extractor_agent_pipeline/extraction/api.py —— 批量提取编排入口（库函数直调）。

run_extract_batch 对批次 end_mds/ 下全部论文跑提取工作流（RuntimeCfg 承载配置），
供 roles.tools 的角色级工具与 CLI 直接调用。
"""

import random
import time
import json
from pathlib import Path

from litdiscovery.paths import resolve_batch, data_doi_dir
from litdiscovery.config import MIN_FULLTEXT_USABLE_RATE
from litdiscovery.agent.extractor_agent_pipeline.extraction.graph import RuntimeCfg, State, build_graph
from litdiscovery.common.fs import write_json_atomic


def _find_latest_end_mds() -> Path:
    """定位最新批次目录的 end_mds/。"""
    batch = resolve_batch(None)
    return batch / "end_mds"


def run_extract_batch(base_dir: str | Path | None = None,
                      domain: str = "thermoelectric",
                      limit: int = 2000,
                      session_log: str | Path | None = None,
                      domain_registry: dict | None = None,
                      min_fulltext_usable_rate: float = MIN_FULLTEXT_USABLE_RATE,
                      allow_low_quality: bool = False) -> dict:
    """对 base_dir 下所有论文文件夹跑提取工作流。

    base_dir: end_mds/ 目录（None 用最新批次）；limit: 最多处理新篇数。
    domain_registry: 动态属性域注册表（write_domain_registry 产物）；
        供分类门命中动态域 label 时解析为完整域，None 则只用静态四域。
    返回 {base_dir, completed, failed, limit}。
    """
    base_dir = Path(base_dir) if base_dir else _find_latest_end_mds()
    audit_path = base_dir.parent / "orders" / "fulltext_quality.json"
    if limit != 0 and min_fulltext_usable_rate > 0 and not allow_low_quality:
        if not audit_path.exists():
            raise RuntimeError(f"缺少全文质量审计，拒绝抽取: {audit_path}；请先运行 preprocess")
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        rate = float(audit.get("usable_rate") or 0.0)
        if rate < min_fulltext_usable_rate:
            raise RuntimeError(
                f"全文可用率 {rate:.1%} 低于抽取门槛 {min_fulltext_usable_rate:.1%}；"
                "请补充全文，或显式设置 allow_low_quality=True")
    if session_log is None:
        # 每批一会话：提取阶段并入该批会话（会话名 = 批次名）
        from litdiscovery.common.logging import session_dir_for_batch
        batch_root = base_dir.parent if base_dir.name == "end_mds" else base_dir
        session_log = session_dir_for_batch(batch_root)
    session_log = Path(session_log)
    session_log.mkdir(parents=True, exist_ok=True)

    # 提取产物目录 = artifacts/extracted/<批次名>/（base_dir 名 = end_mds → 父目录名 = 批次名）
    batch_name = base_dir.parent.name if base_dir.name == "end_mds" else base_dir.name
    extracted_dir = data_doi_dir(batch_name)

    cfg = RuntimeCfg(domain=domain, session_log=session_log,
                     data_doi_dir=extracted_dir, registry=domain_registry)
    app = build_graph(cfg)

    completed_log = session_log / "completed_folders.txt"
    failed_log = session_log / "failed_folders.txt"

    completed_folders = set()
    if completed_log.exists():
        with open(completed_log, "r") as f:
            completed_folders = set(line.strip() for line in f)

    failed_folders = set()
    if failed_log.exists():
        with open(failed_log, "r") as f:
            failed_folders = set(line.strip() for line in f)

    new_count = 0
    failures = []
    for folder in sorted(base_dir.iterdir()):
        if not folder.is_dir():
            continue
        if folder.name in completed_folders or folder.name in failed_folders:
            continue

        try:
            print(f"[...] Running on folder {new_count + 1}: {folder.name}")
            app.invoke(State(
                folder=folder,
                fulltext=None,
                llm=None,
                route=None,
                domain=None,
                material_names=None,
                thermo=None,
                structure=None,
                process=None,
                retries=0,
                table_data=None,
                table_json_output=None,
                table_evidence=None,
                total_table_rows=0,
                skip=False,
            ))
            with open(completed_log, "a") as log_file:
                log_file.write(f"{folder.name}\n")
            new_count += 1
        except Exception as e:
            print(f"[WARN] Failed on {folder.name}: {e}")
            with open(failed_log, "a") as f:
                f.write(f"{folder.name}\n")
            failures.append({"paper": folder.name, "error_type": type(e).__name__,
                             "error": str(e)})

        t = random.uniform(6, 10)
        print(f"[Wait] Sleeping for {t:.2f} seconds after {folder.name} before next folder...")
        time.sleep(t)

        if new_count % 10 == 0:
            print(f"[Cooldown] Processed {new_count} papers - cooling down for 60 seconds...")
            time.sleep(60)
            print("[OK] Cooldown finished. Resuming...\n")

        if limit > 0 and new_count >= limit:
            print(f"[Limit] Reached limit of {limit} new folders.")
            break

    missing_conditions = []
    for perf_path in extracted_dir.glob("*/performance.json") if extracted_dir.exists() else []:
        try:
            payload = json.loads(perf_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for material in payload.get("materials", []):
            for field, entries in material.items():
                if not isinstance(entries, list):
                    continue
                for index, entry in enumerate(entries):
                    if isinstance(entry, dict) and not entry.get("evidence_quote"):
                        missing_conditions.append({"paper": perf_path.parent.name,
                                                   "material": material.get("name", ""),
                                                   "field": field, "entry": index,
                                                   "issue": "missing_evidence_quote"})
    quality = {"schema_version": 1, "completed": new_count, "failures": failures,
               "missing_or_unlocated_entries": missing_conditions}
    write_json_atomic(base_dir.parent / "orders" / "extraction_quality.json", quality)
    return {"base_dir": str(base_dir), "completed": new_count,
            "failed": len(failures), "limit": limit,
            "quality_report": str(base_dir.parent / "orders" / "extraction_quality.json")}
