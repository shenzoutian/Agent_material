"""
orchestrator/report.py —— 聚合各阶段产物 → report_writer → 结构化调研报告。

聚合输入（按批次目录）：
    <batch>/orders/doi_reach_results.json 文献清单（检索阶段）
    <batch>/gap_output/papers.json        语料统计（gap 物化）
    <batch>/gap_output/research_gaps.json research-gap 结论
    artifacts/extracted/<批次>/           提取的性能/工艺
    artifacts/validation/                 验证库对照

输出：<batch>/report.md + report.json
"""

import json
from pathlib import Path

from litdiscovery.config import create_agent
from litdiscovery.llm_utils import invoke_messages, robust_json_parse
from litdiscovery.paths import EXTRACTED_ROOT, VALIDATION_ROOT, read_handoff
from litdiscovery.agent.agent_roles.prompts.registry import REPORT_WRITER

DEFAULT_SECTIONS = ["需求概述", "文献调研概览", "材料-性能汇总", "工艺方法汇总",
                    "research gap", "验证库对照", "未来方向建议"]


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _load_papers(batch: Path) -> list:
    data = _load_json(read_handoff(batch, "doi_reach_results.json"))
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list) and v:
                return v
    return []


def _load_gaps(batch: Path) -> dict:
    return _load_json(batch / "gap_output" / "research_gaps.json")


def _load_papers_stats(batch: Path) -> dict:
    return _load_json(batch / "gap_output" / "papers.json")


def _load_validation(batch: Path) -> dict:
    """从 validation/ 聚合验证库对照（comparison.json 列表）。"""
    if not VALIDATION_ROOT.is_dir():
        return {}
    comparisons = []
    for d in sorted(VALIDATION_ROOT.iterdir()):
        cj = d / "comparison.json"
        if cj.exists():
            comparisons.append(_load_json(cj))
    return {"comparisons": comparisons[:20], "n": len(comparisons)}


def _load_structured_materials(batch: Path) -> list:
    """从 artifacts/extracted/<批次>/ 按批次映射的结构化提取。"""
    papers_stats = _load_papers_stats(batch)
    materials = []
    for folder_name in (papers_stats or {}):
        ddir = EXTRACTED_ROOT / batch.name / folder_name
        if not ddir.is_dir():
            continue
        perf = _load_json(ddir / "performance.json")
        proc = _load_json(ddir / "process.json")
        for m in perf.get("materials", []) or []:
            materials.append({"folder": folder_name, "source": "performance", **m})
        for m in proc.get("materials", []) or []:
            materials.append({"folder": folder_name, "source": "process", **m})
    return materials[:200]


def _load_evidence(batch: Path) -> tuple[list, dict]:
    """Load claim IDs and quality metrics without treating untraceable rows as facts."""
    claims_path = batch / "evidence" / "claims.jsonl"
    quality = _load_json(batch / "evidence" / "quality.json")
    claims = []
    if claims_path.exists():
        for line in claims_path.read_text(encoding="utf-8").splitlines()[:100]:
            try:
                claims.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return claims, quality


def build_context(batch: Path) -> dict:
    """聚合各阶段产物为 report_writer 的输入 context。"""
    papers = _load_papers(batch)
    gaps = _load_gaps(batch)
    stats = _load_papers_stats(batch)
    materials = _load_structured_materials(batch)
    validation = _load_validation(batch)
    claims, evidence_quality = _load_evidence(batch)
    return {
        "batch": str(batch),
        "papers": papers[:50],
        "n_papers_total": len(papers),
        "corpus_stats": {
            "n_papers": len(stats),
            "n_fulltext": sum(1 for p in (stats or {}).values() if p.get("has_fulltext")),
            "n_structured": sum(1 for p in (stats or {}).values() if p.get("has_structured")),
        },
        "gaps": gaps.get("gaps", []),
        "gap_counts": gaps.get("counts", {}),
        "materials": materials,
        "validation": validation,
        "claims": claims,
        "evidence_quality": evidence_quality,
    }


def _render_markdown(report: dict) -> str:
    lines = [f"# {report.get('title', '材料调研报告')}", ""]
    for sec in report.get("sections", []):
        lines.append(f"## {sec.get('heading', '')}")
        lines.append("")
        lines.append(sec.get("content", ""))
        for t in sec.get("tables", []):
            lines.append("")
            lines.append(t)
        lines.append("")
    lines.append(f"> 摘要: {report.get('summary', '')}")
    lines.append("")
    return "\n".join(lines)


def generate_report(batch, sections: list = None, llm=None) -> dict:
    """聚合产物 → report_writer → 写 report.md + report.json，返回 report dict。"""
    batch = Path(batch)
    context = build_context(batch)
    llm = llm or create_agent("report_writer")

    secs = sections or DEFAULT_SECTIONS
    user_prompt = (
        f"报告章节: {', '.join(secs)}\n\n"
        f"聚合产物 context:\n```json\n{json.dumps(context, ensure_ascii=False, indent=1)[:12000]}\n```")

    out = invoke_messages(llm, REPORT_WRITER, user_prompt)
    report = robust_json_parse(out.content)
    if not report.get("title"):
        report = {"title": f"材料调研报告（{batch.name}）", "sections": [], "summary": "", **report}

    quality = context.get("evidence_quality") or {}
    if quality and quality.get("traceability_rate", 0) < 1.0:
        report.setdefault("sections", []).append({
            "heading": "证据质量与人工复核",
            "content": (f"当前 Claim 共 {quality.get('claims', 0)} 条，"
                        f"可定位到原文片段/页表的比例为 {quality.get('traceability_rate', 0):.1%}。"
                        "未完成定位的事实仅供人工复核，不应作为强结论。"),
            "tables": [],
        })

    md = _render_markdown(report)
    (batch / "report.md").write_text(md, encoding="utf-8")
    (batch / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[Report] → {batch / 'report.md'}")
    print(f"[Report] → {batch / 'report.json'}")
    return report
