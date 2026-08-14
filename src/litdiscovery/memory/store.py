"""
litdiscovery/memory/store.py —— 历史数据规则匹配索引。

索引源：
    artifacts/extracted/*/       已提取的结构化产物（性能/工艺）
    artifacts/batches/*/         历史检索批次（DOI/标题/年份）
    artifacts/knowledge/*.jsonl  知识沉淀条目
"""

import json
import re
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional

from litdiscovery.paths import (
    BATCHES_ROOT,
    EXTRACTED_ROOT,
    KNOWLEDGE_ROOT,
    MEMORY_ROOT,
    read_handoff,
)

INDEX_PATH = MEMORY_ROOT / "index.json"


def _normalize_title(title: str) -> str:
    """标题归一化：NFKD、小写、非字母数字折叠，**保留中文字符**。

    原实现 re.sub(r"[^a-z0-9]+", "", t) 会删除所有中文，导致中文批次名/需求
    （如"滤波器"）归一化后为空、永远匹配不上。中文文献的批次名/标题常含中文，
    因此需保留 CJK 字符（[一-鿿]）。
    """
    if not title:
        return ""
    t = unicodedata.normalize("NFKD", str(title))
    t = t.lower()
    return re.sub(r"[^a-z0-9一-鿿]+", "", t)


def _norm_doi(doi: str) -> str:
    """DOI 归一化：去空格/大小写/前后缀。"""
    return (doi or "").strip().lower().rstrip(".")


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


# ============================================================
# 索引条目
# ============================================================
def _iter_batch_dois(batch_dir: Path, source: str):
    """从批次目录的 doi_reach_results.json 提取文献记录（新批次在 orders/，兼容旧批次根）。"""
    rj = read_handoff(batch_dir, "doi_reach_results.json")
    if not rj.exists():
        return
    data = _read_json(rj)
    if isinstance(data, list):
        recs = data
    elif isinstance(data, dict):
        recs = next((v for v in data.values() if isinstance(v, list)), [])
    else:
        recs = []
    for r in recs:
        doi = _norm_doi(r.get("doi"))
        if not doi:
            continue
        yield {
            "doi": doi, "title": (r.get("title") or ""),
            "year": r.get("year"), "venue": (r.get("venue") or ""),
            "batch": batch_dir.name, "has_structured": False,
            "source": source,
        }


def _iter_knowledge(batch_dir: Path):
    """从 artifacts/knowledge/*.jsonl 提取知识条目。"""
    store = KNOWLEDGE_ROOT
    if not store.is_dir():
        return
    for jl in store.glob("*.jsonl"):
        for line in jl.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            doi = _norm_doi(rec.get("doi") or "")
            if not doi:
                continue
            yield {
                "doi": doi, "title": (rec.get("title") or ""),
                "year": None, "venue": "",
                "batch": jl.stem, "has_structured": False,
                "source": "knowledge", "abstract": (rec.get("abstract") or ""),
            }


def _iter_data_doi():
    """扫描 artifacts/extracted/<批次>/<folder>/ 已提取产物（兼容旧扁平 <folder>/）。"""
    data_doi = EXTRACTED_ROOT
    if not data_doi.is_dir():
        return
    for top in data_doi.iterdir():
        if not top.is_dir():
            continue
        if (top / "performance.json").exists():
            # 顶层子目录直接是论文 folder（旧扁平结构）
            for rec in _data_doi_folder(top, "data_doi"):
                yield rec
            continue
        for folder in top.iterdir():
            if not folder.is_dir():
                continue
            for rec in _data_doi_folder(folder, top.name):
                yield rec


def _data_doi_folder(folder: Path, batch_label: str):
    """从一个论文文件夹生成索引记录。"""
    perf = _read_json(folder / "performance.json")
    struct = _read_json(folder / "structure.json")
    mats = []
    for src, data in (("perf", perf), ("struct", struct)):
        for m in data.get("materials", []) or []:
            if isinstance(m, dict) and m.get("name"):
                mats.append({"name": m["name"], "source": src})
    yield {
        "doi": folder.name.replace("_", "/"),
        "title": folder.name, "year": None, "venue": "",
        "batch": batch_label, "has_structured": bool(mats),
        "materials": mats, "source": "data_doi",
    }


# ============================================================
# 索引构建
# ============================================================
def build_index() -> List[dict]:
    """全量构建索引（data_doi + 历史批次 + knowledge_store）。"""
    records = []
    seen = set()
    for rec in _iter_data_doi():
        key = (rec["doi"], "data_doi")
        if key not in seen:
            seen.add(key)
            records.append(rec)
    if BATCHES_ROOT.is_dir():
        for batch in sorted(BATCHES_ROOT.iterdir(), key=lambda d: d.name):
            if not batch.is_dir():
                continue
            for rec in _iter_batch_dois(batch, "batch"):
                key = (rec["doi"], "batch")
                if key not in seen:
                    seen.add(key)
                    records.append(rec)
    for rec in _iter_knowledge(BATCHES_ROOT):
        key = (rec["doi"], "knowledge")
        if key not in seen:
            seen.add(key)
            records.append(rec)
    return records


def save_index(records: list, path: Optional[Path] = None) -> Path:
    path = path or INDEX_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_index(path: Optional[Path] = None) -> list:
    path = path or INDEX_PATH
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return build_index()


# ============================================================
# 检索
# ============================================================
def known_dois(records: list = None) -> set:
    """全部历史 DOI 集合。"""
    records = records if records is not None else load_index()
    return {r["doi"] for r in records if r.get("doi")}


def _query_dois(query: str) -> List[str]:
    """从 query 中提取 DOI 样式 token（10.xxxx/...）。"""
    return re.findall(r"10\.\d{4,9}/[\w.\-]+", query, re.I)  # noqa: W605 (docstring-safe)


def _query_keywords(query: str) -> List[str]:
    """从 query 提取非 DOI 的关键词（小写归一化，保留中文）。

    原实现按非词字符切分 + len>=3 会丢 2 字中文词（如"压电"），
    且后续 _normalize_title 删除中文导致永远匹配不上。改为：
    按空白/标点切分后，对每段取中文连续子串与英文单词，均保留。
    """
    tokens = []
    for seg in re.split(r"[\s,，。；;：:、/]+", query):
        if not seg:
            continue
        # 英文/数字词
        tokens.extend(w for w in re.findall(r"[a-z0-9][a-z0-9\-]*", seg.lower()) if len(w) >= 3)
        # 中文连续子串（去掉前后非中文后，作为整体）
        zh = re.sub(r"^[^一-鿿]+|[^一-鿿]+$", "", seg)
        if zh and zh not in tokens:
            tokens.append(zh)
    return tokens


def search(query: str, k: int = 20, records: list = None) -> List[dict]:
    """规则匹配检索历史数据。

    - query 中的 DOI 精确命中（最高优先级）
    - 标题/材料名归一化后做关键词子串匹配
    返回按匹配强度排序的记录。
    """
    records = records if records is not None else load_index()
    if not records:
        return []

    q_dois = [_norm_doi(d) for d in _query_dois(query)]
    q_keys = _query_keywords(query)

    scored = []
    for rec in records:
        score = 0
        doi = _norm_doi(rec.get("doi") or "")
        title_n = _normalize_title(rec.get("title") or "")
        batch_n = _normalize_title(rec.get("batch") or "")
        if q_dois and doi in q_dois:
            score += 100
        if q_keys:
            title_hit = sum(1 for kw in q_keys if kw in title_n)
            # 批次名匹配：中文需求（如"滤波器"）常命中批次名而非英文标题
            batch_hit = sum(1 for kw in q_keys if kw in batch_n)
            mat_hits = 0
            for m in rec.get("materials", []) or []:
                name_n = _normalize_title(m.get("name") or "")
                mat_hits += sum(1 for kw in q_keys if kw in name_n)
            score += title_hit * 3 + batch_hit * 2 + mat_hits * 4
        if rec.get("has_structured"):
            score += 5
        if score > 0:
            scored.append((score, rec))

    scored.sort(key=lambda x: (-x[0], x[1].get("year") or 0))
    return [rec for _, rec in scored[:k]]


def summary(records: list) -> str:
    """索引摘要（供 planner 上下文注入）。"""
    if not records:
        return "（历史索引为空）"
    n_doi = len(records)
    n_struct = sum(1 for r in records if r.get("has_structured"))
    return (f"历史沉淀 {n_doi} 条（含结构化 {n_struct} 条）："
            f"data_doi/{sum(1 for r in records if r.get('source')=='data_doi')} "
            f"+ 批次/{sum(1 for r in records if r.get('source')=='batch')} "
            f"+ 知识库/{sum(1 for r in records if r.get('source')=='knowledge')}")
