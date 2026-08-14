"""
输出层：把检测+裁决结果写为 research_gaps.json（结构化）+ research_gaps.md（人类可读）。

research_gaps.json 含 corpus manifest（spec_version/generated_at/论文统计），原子写入。
每条 gap 的 evidence 都带权威 DOI，可溯源。
"""

import json
import datetime
import tempfile
import os
from pathlib import Path

TYPE_LABEL = {
    "underexplored": "未被充分探索的方向",
    "missing_link": "缺失的知识连接",
    "contradiction": "矛盾结论",
}


def _fold_verdicts(verdicts: list) -> list:
    """把接受/拒绝的 verdict 折叠为最终 gap 列表。"""
    gaps = []
    for v in verdicts:
        c = v["candidate"]
        if not v["accept"]:
            continue
        gaps.append({
            "id": c["id"],
            "type": c["type"],
            "subtype": c.get("subtype"),
            "statement": v.get("refined_statement") or c.get("statement", ""),
            "concept_a": c.get("concept_a"),
            "concept_b": c.get("concept_b"),
            "confidence": v.get("confidence", "low"),
            "reason": v.get("reason", ""),
            "evidence": c.get("evidence", []),
            "evidence_doi": v.get("evidence_doi") or [e.get("doi") for e in c.get("evidence", [])],
        })
    return gaps


def _corpus_stats(result: dict, verdicts: list) -> dict:
    papers = result.get("papers", {})
    n_fulltext = sum(1 for p in papers.values() if p.get("has_fulltext"))
    n_structured = sum(1 for p in papers.values() if p.get("has_structured"))
    n_resolved = sum(1 for p in papers.values() if p.get("resolution") != "missing")
    return {
        "n_papers_total": len(papers),
        "n_fulltext": n_fulltext,
        "n_structured": n_structured,
        "n_doi_resolved": n_resolved,
        "spec_version": "1.0",
    }


def write_gaps(result: dict, verdicts: list, out_dir: Path):
    """写 research_gaps.json + research_gaps.md，返回 gaps 列表。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    gaps = _fold_verdicts(verdicts)
    n_accept = len(gaps)
    n_reject = len(verdicts) - n_accept
    manifest = {
        "generated_at": datetime.datetime.now().isoformat(),
        "corpus": _corpus_stats(result, verdicts),
        "counts": {
            "accepted_gaps": n_accept,
            "rejected": n_reject,
            "total_candidates": len(verdicts),
        },
        "gaps": gaps,
    }
    # 原子写入 json
    fd, tmp = tempfile.mkstemp(dir=str(out_dir), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    os.replace(tmp, out_dir / "research_gaps.json")

    # 人类可读 md
    md = _render_md(manifest)
    with open(out_dir / "research_gaps.md", "w", encoding="utf-8") as f:
        f.write(md)
    return gaps


def _render_md(manifest: dict) -> str:
    c = manifest["corpus"]
    lines = [
        "# Research Gap 报告",
        "",
        f"- 生成时间: {manifest['generated_at']}",
        f"- 语料: {c['n_papers_total']} 篇（全文 {c['n_fulltext']} / 结构化 {c['n_structured']} / DOI 可解析 {c['n_doi_resolved']}）",
        f"- 候选 {manifest['counts']['total_candidates']} 个 → 接受 {manifest['counts']['accepted_gaps']} / 拒绝 {manifest['counts']['rejected']}",
        "",
    ]
    for i, g in enumerate(manifest["gaps"], 1):
        tlabel = TYPE_LABEL.get(g["type"], g["type"])
        lines.append(f"## {i}. [{tlabel}] {g['statement']}")
        lines.append(f"   - 置信度: {g['confidence']} | 裁决理由: {g['reason']}")
        lines.append(f"   - 证据 DOI: {', '.join(g['evidence_doi']) or '(无)'}")
    if not manifest["gaps"]:
        lines.append("（当前语料未发现被接受的真实 gap，或数据不足以支撑。）")
    lines.append("")
    return "\n".join(lines)
