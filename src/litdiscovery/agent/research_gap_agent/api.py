"""
litdiscovery/agent/research_gap_agent/api.py —— research-gap 流水线统一入口。

run_gap 串起物化 → 检测 → 裁决 → 报告四层（内存直连，保持原有行为），供
roles.tools 与 CLI 直接调用。此外暴露四个独立 stage 函数（materialize_stage /
detect_stage / adjudicate_stage / report_stage），每个 stage 落盘其中间产物，供
executor 的细粒度工具按需单独调用，避免「一个工具重跑整个 run_gap」造成的重复
LLM 概念提取与重复裁决：

    gap_output/
        material_props.csv / material_struct.csv / paper_concepts.csv  物化三表
        papers.json                                                    语料清单
        concepts_ledger.json                                           概念提取增量缓存
        gap_candidates.json                                            检测候选（det 全量）
        gap_verdicts.json                                              裁决 verdicts
        research_gaps.{json,md}                                        最终报告
"""

import json
from pathlib import Path

import pandas as pd

from litdiscovery.config import create_agent
from litdiscovery.paths import (
    resolve_batch, EXTRACTED_ROOT, read_handoff,
)
from litdiscovery.common.fs import write_text_atomic
from litdiscovery.agent.research_gap_agent.materialize import (
    CorpusPaths, materialize, save_materialized, build_specs,
)
from litdiscovery.agent.research_gap_agent.detectors import run_all
from litdiscovery.agent.research_gap_agent.adjudicate import adjudicate
from litdiscovery.agent.research_gap_agent.report import write_gaps


def _json_safe(obj):
    """把 numpy 标量（int64/float64/bool_）递归转为原生类型，保证 JSON 可序列化。"""
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if hasattr(obj, "item") and callable(obj.item):
        try:
            return obj.item()
        except Exception:
            return obj
    return obj


def _write_json(path: Path, data) -> None:
    write_text_atomic(path, json.dumps(_json_safe(data), ensure_ascii=False, indent=2))


def _corpus(batch: str | Path):
    batch = resolve_batch(batch)
    return batch, CorpusPaths(
        end_mds=batch / "end_mds",
        data_doi=EXTRACTED_ROOT,
        doi_results_json=read_handoff(batch, "doi_reach_results.json"),
        gap_data_dir=batch / "gap_output",
    )


def _load_materialized(gap_out: Path) -> dict:
    """从 gap_output 读回三表 CSV + papers.json + specs（供检测/报告复用）。

    用 keep_default_na=False 保持空字符串为空字符串（与内存 DataFrame 一致），
    再把 value / temp_K 两个数值列显式转回数值，避免 CSV 往返引入 dtype 漂移。
    """

    def _read_csv(name: str) -> pd.DataFrame:
        p = gap_out / name
        if not p.exists() or p.stat().st_size == 0:
            return pd.DataFrame()
        try:
            df = pd.read_csv(p, keep_default_na=False)
        except pd.errors.EmptyDataError:
            return pd.DataFrame()
        for col in ("value", "temp_K"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df

    papers = {}
    papers_path = gap_out / "papers.json"
    if papers_path.exists():
        try:
            papers = json.loads(papers_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            papers = {}
    return {
        "props_df": _read_csv("material_props.csv"),
        "struct_df": _read_csv("material_struct.csv"),
        "concepts_df": _read_csv("paper_concepts.csv"),
        "papers": papers if isinstance(papers, dict) else {},
        "specs": build_specs(),
    }


def _flatten_candidates(det: dict) -> list:
    return (
        det.get("underexplored", []) + det.get("missing_links", [])
        + det.get("structure_contradictions", []) + det.get("numeric_contradictions", [])
    )


def _adjudicate_candidates(all_candidates: list, judge_llm, skip_llm: bool) -> list:
    if skip_llm or not all_candidates:
        verdicts = [{
            "id": c["id"], "accept": True,
            "reason": "跳过裁决（--skip-llm 或检测全拒绝）",
            "refined_statement": c["statement"], "evidence_doi": [],
            "confidence": "low", "candidate": c,
        } for c in all_candidates]
        print(f"[Gap] skipped adjudication, {len(verdicts)} candidates passed through")
    else:
        if judge_llm is None:
            judge_llm = create_agent("gap_adjudicator")
        verdicts = adjudicate(all_candidates, judge_llm)
        print(f"[Gap] adjudicated {len(verdicts)} candidates")
    return verdicts


def _load_candidates(gap_out: Path) -> list:
    p = gap_out / "gap_candidates.json"
    if not p.exists():
        return []
    try:
        det = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return _flatten_candidates(det if isinstance(det, dict) else {})


def _load_verdicts(gap_out: Path) -> list:
    p = gap_out / "gap_verdicts.json"
    if not p.exists():
        return []
    try:
        verdicts = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return verdicts if isinstance(verdicts, list) else []


# ---- 四个独立 stage（供 executor 细粒度工具单独调用）----

def materialize_stage(batch: str | Path | None = None, skip_llm: bool = False,
                      limit: int = 0, concept_llm=None, force: bool = False) -> dict:
    """仅物化：把提取产物 + 摘要物化为三表并落盘（含概念提取 ledger 缓存）。"""
    batch, corpus = _corpus(batch)
    gap_out = corpus.gap_data_dir
    if concept_llm is None and not skip_llm:
        concept_llm = create_agent("gap_concept_extractor")
    result = materialize(corpus, llm=concept_llm, force=force, limit=limit)
    save_materialized(result, gap_out)
    print(f"[Gap] materialized: props={len(result['props_df'])} "
          f"struct={len(result['struct_df'])} concepts={len(result['concepts_df'])} "
          f"papers={len(result['papers'])}")
    return {
        "batch": str(batch), "gap_out": str(gap_out),
        "n_props": len(result["props_df"]), "n_struct": len(result["struct_df"]),
        "n_concepts": len(result["concepts_df"]), "n_papers": len(result["papers"]),
    }


def detect_stage(batch: str | Path | None = None) -> dict:
    """仅检测：读已物化三表 → 纯 pandas 检测 → 落盘候选。"""
    batch, corpus = _corpus(batch)
    gap_out = corpus.gap_data_dir
    result = _load_materialized(gap_out)
    det = run_all(result, result["papers"])
    n_ug = len(det["underexplored"]); n_ml = len(det["missing_links"])
    n_sc = len(det["structure_contradictions"]); n_nc = len(det["numeric_contradictions"])
    print(f"[Gap] detected: underexplored={n_ug} missing_links={n_ml} "
          f"struct_contradictions={n_sc} numeric_contradictions={n_nc}")
    if not det["numeric_flags"]["data_sufficient"]:
        print(f"[Gap] numeric contradiction: DATA INSUFFICIENT -> {det['numeric_flags']['note']}")
    _write_json(gap_out / "gap_candidates.json", det)
    return {"batch": str(batch), "gap_out": str(gap_out),
            "n_detected": len(_flatten_candidates(det))}


def adjudicate_stage(batch: str | Path | None = None, judge_llm=None,
                     skip_llm: bool = False) -> dict:
    """仅裁决：读检测候选 → LLM 反证裁决 → 落盘 verdicts。"""
    batch, corpus = _corpus(batch)
    gap_out = corpus.gap_data_dir
    all_candidates = _load_candidates(gap_out)
    verdicts = _adjudicate_candidates(all_candidates, judge_llm, skip_llm)
    _write_json(gap_out / "gap_verdicts.json", verdicts)
    accepted = sum(1 for v in verdicts if v["accept"])
    return {"batch": str(batch), "gap_out": str(gap_out),
            "n_verdicts": len(verdicts), "accepted": accepted}


def report_stage(batch: str | Path | None = None) -> dict:
    """仅报告：读 verdicts + 语料统计 → 写 research_gaps.{json,md}。"""
    batch, corpus = _corpus(batch)
    gap_out = corpus.gap_data_dir
    verdicts = _load_verdicts(gap_out)
    result = _load_materialized(gap_out)
    gaps = write_gaps(result, verdicts, gap_out)
    accepted = sum(1 for v in verdicts if v["accept"])
    print(f"[Gap] done: {accepted}/{len(verdicts)} gaps accepted -> {gap_out / 'research_gaps.json'}")
    if not gaps:
        print("[Gap] no accepted gaps (语料过薄或全部被裁决拒绝)")
    return {"batch": str(batch), "gap_out": str(gap_out), "accepted": accepted, "gaps": gaps}


def run_gap(batch: str | Path | None = None, skip_llm: bool = False,
            limit: int = 0, concept_llm=None, judge_llm=None) -> dict:
    """物化 → 检测 → 裁决 → 报告（内存直连全链，等价于依次调用四个 stage）。

    batch: 批次目录（None 用最新）；limit: 最多物化篇数（0=全部）。
    skip_llm: True 跳过 gap 链的 LLM 调用（摘要概念提取降级为规则 + 跳过裁决）。
    返回 {batch, gap_out, n_detected, n_verdicts, accepted, gaps}。
    """
    batch, corpus = _corpus(batch)
    gap_out = corpus.gap_data_dir
    if concept_llm is None and not skip_llm:
        concept_llm = create_agent("gap_concept_extractor")
    result = materialize(corpus, llm=concept_llm, limit=limit)
    save_materialized(result, gap_out)
    print(f"[Gap] materialized: props={len(result['props_df'])} "
          f"struct={len(result['struct_df'])} concepts={len(result['concepts_df'])} "
          f"papers={len(result['papers'])}")

    det = run_all(result, result["papers"])
    n_ug = len(det["underexplored"]); n_ml = len(det["missing_links"])
    n_sc = len(det["structure_contradictions"]); n_nc = len(det["numeric_contradictions"])
    print(f"[Gap] detected: underexplored={n_ug} missing_links={n_ml} "
          f"struct_contradictions={n_sc} numeric_contradictions={n_nc}")
    if not det["numeric_flags"]["data_sufficient"]:
        print(f"[Gap] numeric contradiction: DATA INSUFFICIENT -> {det['numeric_flags']['note']}")

    all_candidates = _flatten_candidates(det)
    _write_json(gap_out / "gap_candidates.json", det)
    verdicts = _adjudicate_candidates(all_candidates, judge_llm, skip_llm)
    _write_json(gap_out / "gap_verdicts.json", verdicts)

    gaps = write_gaps(result, verdicts, gap_out)
    accepted = sum(1 for v in verdicts if v["accept"])
    print(f"[Gap] done: {accepted}/{len(verdicts)} gaps accepted -> {gap_out / 'research_gaps.json'}")
    if not gaps:
        print("[Gap] no accepted gaps (语料过薄或全部被裁决拒绝)")

    return {
        "batch": str(batch), "gap_out": str(gap_out),
        "n_detected": len(all_candidates),
        "n_verdicts": len(verdicts), "accepted": accepted,
        "gaps": gaps,
    }
