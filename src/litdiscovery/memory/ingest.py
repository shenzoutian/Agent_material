"""
litdiscovery/memory/ingest.py —— 增量摄取历史数据到索引。

ingest() 按目录 mtime 增量合并历史数据到索引，
索引缓存到 artifacts/memory/index.json。
"""

import json
from pathlib import Path

from litdiscovery.memory import store
from litdiscovery.paths import (
    BATCHES_ROOT,
    EXTRACTED_ROOT,
    KNOWLEDGE_ROOT,
    MEMORY_ROOT,
    read_handoff,
)

INDEX_PATH = MEMORY_ROOT / "index.json"


def _existing_keys(records: list) -> set:
    return {(r.get("source"), r.get("doi")) for r in records}


def _existing_batch_dirs() -> set:
    """当前磁盘上实际存在的批次名集合（用于剔除幽灵记录）。"""
    return {d.name for d in BATCHES_ROOT.iterdir() if d.is_dir()} if BATCHES_ROOT.is_dir() else set()


def _purge_ghost_records(records: list) -> list:
    """剔除幽灵记录：批次字段指向的目录已不在 BATCHES_ROOT。

    场景：批次被删除后，索引里旧记录仍残留，planner 查询 memory 时误以为
    "有历史数据"，但实际批次目录不存在 → 提取工具找不到 fulltext → 从头检索。
    每次 ingest 时清理，保证索引与实际数据一致。
    """
    existing_batches = _existing_batch_dirs()
    if not existing_batches:
        return records
    kept = []
    for r in records:
        batch = r.get("batch") or ""
        # 无批次归属的记录（如扁平 data_doi 旧结构）保留；批次目录存在则保留
        if not batch or batch in existing_batches:
            kept.append(r)
    dropped = len(records) - len(kept)
    if dropped:
        print(f"[Memory] 清理 {dropped} 条幽灵记录（批次目录已不存在）")
    return kept


def _batch_mtime(batch_dir: Path) -> float:
    """批次目录最新修改时间（判断是否需要重建索引）。"""
    mtimes = [batch_dir.stat().st_mtime]
    for f in ("doi_reach_results.json", "seed_papers.json", "snowball_candidates.json"):
        p = read_handoff(batch_dir, f)   # 新批次在 orders/，兼容旧批次根
        if p.exists():
            mtimes.append(p.stat().st_mtime)
    return max(mtimes)


def ingest(force: bool = False) -> list:
    """增量摄取历史数据到索引，返回全部记录。force=True 强制全量重建。"""
    records = store.load_index(INDEX_PATH) if INDEX_PATH.exists() and not force else []
    existing = _existing_keys(records)

    # data_doi 全量（已提取产物）
    if EXTRACTED_ROOT.is_dir():
        for rec in store._iter_data_doi():
            key = (rec["source"], rec["doi"])
            if key not in existing:
                records.append(rec)
                existing.add(key)

    # 各批次（按 mtime 增量）
    if BATCHES_ROOT.is_dir():
        meta_path = MEMORY_ROOT / "batch_mtime.json"
        last_mtimes = {}
        if meta_path.exists() and not force:
            try:
                last_mtimes = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        new_mtimes = {}
        for batch in sorted(BATCHES_ROOT.iterdir(), key=lambda d: d.name):
            if not batch.is_dir():
                continue
            mt = _batch_mtime(batch)
            new_mtimes[batch.name] = mt
            if force or last_mtimes.get(batch.name, 0) < mt:
                for rec in store._iter_batch_dois(batch, "batch"):
                    key = (rec["source"], rec["doi"])
                    if key not in existing:
                        records.append(rec)
                        existing.add(key)
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps(new_mtimes), encoding="utf-8")

    # knowledge_store
    if KNOWLEDGE_ROOT.is_dir():
        for rec in store._iter_knowledge(BATCHES_ROOT):
            key = (rec["source"], rec["doi"])
            if key not in existing:
                records.append(rec)
                existing.add(key)

    # 剔除幽灵记录：批次目录已不在磁盘的旧记录（防止 planner 误判"有数据"）
    records = _purge_ghost_records(records)

    store.save_index(records, INDEX_PATH)
    return records


def refresh() -> list:
    """强制全量重建索引。"""
    return ingest(force=True)
