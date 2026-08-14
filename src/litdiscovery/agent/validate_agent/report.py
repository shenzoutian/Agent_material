"""
stages/validate/report.py — 汇总对比报告。

把各库查询结果汇总为统一对比表，输出 comparison.md + comparison.json：
    material | 文献提取值 | Materials Project | OQMD | AFLOW | 一致性判定
"""

import json
from pathlib import Path


def _fmt(v):
    if v is None or v == "":
        return "—"
    if isinstance(v, float):
        return f"{v:.3g}"
    return str(v)


def build_comparison(formula: str, db_results: dict) -> dict:
    """构建统一对比结构。

    db_results: {"materials_project": {...}, "oqmd": {...}, "aflow": {...}}
    每项为 query_formula 返回的 {available, error, results}。
    """
    def _top(name):
        res = db_results.get(name) or {}
        avail = res.get("available")
        if not avail:
            return {"status": "skip", "detail": res.get("error") or "未配置"}
        if res.get("error"):
            return {"status": "error", "detail": res["error"]}
        results = res.get("results") or []
        return {"status": "ok", "detail": f"{len(results)} 条", "results": results[:3]}

    return {
        "formula": formula,
        "materials_project": _top("materials_project"),
        "oqmd": _top("oqmd"),
        "aflow": _top("aflow"),
    }


def _markdown_row(cols):
    return "| " + " | ".join(_fmt(c) for c in cols) + " |"


def write_report(comparison: dict, out_dir) -> Path:
    """写 comparison.md + comparison.json，返回 md 路径。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    formula = comparison["formula"]

    lines = [
        f"# 材料 {formula} — 数据库交叉验证报告",
        "",
        "对文献提取数据的**最后验证**：用第一性原理/实验数据库核对结构与性能。",
        "",
        "## 各库查询状态",
        "",
    ]
    for name, label in (("materials_project", "Materials Project"),
                        ("oqmd", "OQMD"),
                        ("aflow", "AFLOW")):
        c = comparison[name]
        status = {"ok": "✅", "skip": "⏭️", "error": "⚠️"}[c["status"]]
        lines.append(f"- **{label}**: {status} {c['detail']}")
    lines.append("")

    # 逐库明细（只对 status=ok 的输出表格）
    for name, label in (("materials_project", "Materials Project"),
                        ("oqmd", "OQMD"),
                        ("aflow", "AFLOW")):
        c = comparison[name]
        if c["status"] != "ok" or not c.get("results"):
            continue
        lines.append(f"## {label} 结果（{formula}）")
        lines.append("")
        rows = c["results"]
        headers = list(rows[0].keys()) if rows else ["(空)"]
        lines.append(_markdown_row(headers))
        lines.append(_markdown_row(["---"] * len(headers)))
        for r in rows:
            lines.append(_markdown_row([r.get(h) for h in headers]))
        lines.append("")

    # 一致性小结
    lines += [
        "## 一致性判定（人工核对）",
        "",
        "> 用法：将文献提取值（material_props.csv / structure.json）与上表对应项对比。",
        "> - band_gap / space_group 等结构描述符可直接比对；",
        "> - 实验性能值（ZT/d33 等）无第一性原理对应，用形成能/带隙等计算值辅助判断材料体系合理性。",
        "",
    ]

    md = "\n".join(lines)
    md_path = out_dir / "comparison.md"
    md_path.write_text(md, encoding="utf-8")
    json_path = out_dir / "comparison.json"
    json_path.write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[Report] comparison.md → {md_path}")
    print(f"[Report] comparison.json → {json_path}")
    return md_path
