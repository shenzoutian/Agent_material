"""
stages/validate/aflow.py — AFLOW aflux REST API 查询。

无需 API key。AFLOW（Automatic FLOW）第一性原理计算材料库（含 ICSD 收录）。

aflux 语法（非 key=value，见 aflow.org/API/aflux/?help）：
    <summons> = catalog(icsd),compound(Al1N1),Egap,spacegroup_relax
    - 逗号是 AND 运算符；compound 需带配比（AlN → Al1N1）
    - 额外字段直接写在 summons 里即可随结果返回

API: GET http://aflow.org/API/aflux/?catalog(icsd),compound({formula}),Egap,spacegroup_relax
"""

import re

from litdiscovery.common.net import _get

AFLUX_BASE = "http://aflow.org/API/aflux/?"
EXTRA_FIELDS = "Egap,spacegroup_relax,Pearson_symbol_relax,auid,aurl"


def _aflow_formula(formula: str) -> str:
    """把用户化学式规范化为 aflux 的带配比格式：AlN → Al1N1, Al2O3 → Al2O3。

    解析为元素+数量 token，缺失数量补 1。小数配比（Sc0.3Al0.7N）原样保留，
    AFLOW 无该精确相时会返回空结果（可接受）。
    """
    tokens = re.findall(r"([A-Z][a-z]?)(\d*\.?\d*)?", formula)
    out = []
    for sym, num in tokens:
        if not sym:
            continue
        out.append(sym + (num if num else "1"))
    return "".join(out)


def query_formula(formula: str, limit: int = 5) -> dict:
    """按化学式查询 AFLOW，返回 {available, error, results}。

    compound() 是精确匹配（含配比），命中多态时用 auid 区分。
    """
    try:
        af = _aflow_formula(formula)
        summons = f"catalog(icsd),compound({af}),{EXTRA_FIELDS},paging(0,{limit})"
        resp = _get(AFLUX_BASE + summons, timeout=90)
        if resp.status_code != 200:
            return {"available": True, "error": f"AFLOW 返回 {resp.status_code}", "results": []}
        data = resp.json()
        # paging(0,N) 时返回 dict（{"1 of N": {...}}），否则返回 list
        if isinstance(data, dict):
            data = list(data.values())
        if not isinstance(data, list):
            return {"available": True, "error": f"AFLOW 返回非列表: {str(data)[:80]}", "results": []}
        results = []
        for d in data:
            results.append({
                "auid": d.get("auid"),
                "aurl": d.get("aurl"),
                "compound": d.get("compound"),
                "band_gap": d.get("Egap"),
                "space_group_number": d.get("spacegroup_relax"),
                "pearson_symbol": d.get("Pearson_symbol_relax"),
                "catalog": d.get("catalog"),
            })
        return {"available": True, "error": None, "results": results}
    except Exception as e:
        return {"available": True, "error": f"查询失败: {type(e).__name__}: {e}", "results": []}
