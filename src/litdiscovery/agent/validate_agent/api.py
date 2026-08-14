"""
litdiscovery/agent/validate_agent/api.py —— 材料数据库验证批量入口（库函数直调）。

run_validate 供 roles.tools.validate_formulas 与 CLI 直接调用。
"""

import csv
import time
from pathlib import Path

from litdiscovery.paths import VALIDATION_ROOT
from litdiscovery.agent.validate_agent import materials_project, oqmd, aflow
from litdiscovery.agent.validate_agent.report import build_comparison, write_report


def load_formulas_from_csv(props_csv: str | Path) -> list:
    """从 material_props.csv 提取去重后的 material_family 列表。"""
    formulas = []
    seen = set()
    with open(props_csv, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            fam = (row.get("material_family") or "").strip()
            if fam and fam not in seen:
                seen.add(fam)
                formulas.append(fam)
    return formulas


def _query_all(formula: str, delay: float = 0.3) -> dict:
    """查询三个库（带轻延迟，避免对公开 API 连发）。"""
    print(f"[Validate] 查询 {formula} ...")
    mp = materials_project.query_formula(formula)
    time.sleep(delay)
    oq = oqmd.query_formula(formula)
    time.sleep(delay)
    af = aflow.query_formula(formula)
    for name, res in (("MP", mp), ("OQMD", oq), ("AFLOW", af)):
        if res.get("error"):
            print(f"  [{name}] {res['error']}")
        else:
            print(f"  [{name}] {len(res.get('results') or [])} 条")
    return {"materials_project": mp, "oqmd": oq, "aflow": af}


def run_validate(formulas: list | str, batch: str | Path | None = None,
                 out_root: str | Path | None = None, delay: float = 0.3) -> dict:
    """批量验证材料化学式，产出 comparison.{md,json}。

    formulas: 化学式列表，或逗号分隔字符串。
    batch: 批次目录（None 时尝试从 <batch>/gap_output/material_props.csv 读 material_family）。
    out_root: 输出根（默认 VALIDATION_ROOT）。
    返回 {n_validated, n_available, summary_path}。
    """
    if isinstance(formulas, str) and formulas.strip():
        formula_list = [f.strip() for f in formulas.split(",") if f.strip()]
    elif isinstance(formulas, str):
        formula_list = []
    else:
        formula_list = list(formulas)

    # batch 缺省公式 → 从 material_props.csv 提取
    if not formula_list and batch is not None:
        props_csv = Path(batch) / "gap_output" / "material_props.csv"
        if props_csv.exists():
            formula_list = load_formulas_from_csv(props_csv)
            print(f"[Validate] 从 CSV 提取 {len(formula_list)} 个材料家族")

    if not formula_list:
        print("[Validate] 无待验证材料化学式。")
        return {"n_validated": 0, "n_available": 0, "summary_path": ""}

    out_root = Path(out_root) if out_root else VALIDATION_ROOT
    reports = []
    n_available = 0
    for formula in formula_list:
        db = _query_all(formula, delay=delay)
        comp = build_comparison(formula, db)
        md = write_report(comp, out_root / formula.replace("/", "_"))
        reports.append(md)
        if any(v.get("available") for v in db.values()):
            n_available += 1

    return {
        "n_validated": len(formula_list),
        "n_available": n_available,
        "reports": [str(r) for r in reports],
        "summary_path": str(out_root),
    }
