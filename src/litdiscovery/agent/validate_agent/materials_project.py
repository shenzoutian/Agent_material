"""
stages/validate/materials_project.py — Materials Project v2 REST API 查询。

免费学术注册获取 API key：https://materialsproject.org/api
config.MATERIALS_PROJECT_API_KEY 为空时自动跳过（优雅降级，不影响 OQMD/AFLOW）。

API: GET https://api.materialsproject.org/materials/core/
     header: X-API-KEY
     query:  formula=AlN, _fields=..., _per_page=5
"""

import os

from litdiscovery.config import MATERIALS_PROJECT_API_KEY
from litdiscovery.common.net import _get


def _api_key() -> str:
    return (os.environ.get("MATERIALS_PROJECT_API_KEY")
            or MATERIALS_PROJECT_API_KEY or "").strip()


def is_available() -> bool:
    """是否有 MP API key（无 key 则整个源跳过）。"""
    return bool(_api_key())


def query_formula(formula: str, per_page: int = 5) -> dict:
    """按化学式查询 Materials Project，返回 {available, error, results}。"""
    if not is_available():
        return {"available": False, "error": "MATERIALS_PROJECT_API_KEY 未配置（跳过）", "results": []}
    try:
        resp = _get(
            "https://api.materialsproject.org/materials/core/",
            headers={"X-API-KEY": _api_key()},
            params={"formula": formula, "_per_page": per_page,
                    "_fields": "formula_pretty,band_gap,energy_above_hull,is_stable,"
                               "symmetry,structure"},
            timeout=60,
        )
        if resp.status_code == 401:
            return {"available": False, "error": "MP API key 无效(401)", "results": []}
        if resp.status_code == 402:
            return {"available": False, "error": "MP 配额耗尽(402)", "results": []}
        resp.raise_for_status()
        data = resp.json()
        results = []
        for d in data.get("data", []) or []:
            sym = d.get("symmetry") or {}
            results.append({
                "formula": d.get("formula_pretty"),
                "band_gap": d.get("band_gap"),
                "energy_above_hull": d.get("energy_above_hull"),
                "is_stable": d.get("is_stable"),
                "space_group": sym.get("symbol"),
                "space_group_number": sym.get("number"),
            })
        return {"available": True, "error": None, "results": results}
    except Exception as e:
        return {"available": True, "error": f"查询失败: {type(e).__name__}: {e}", "results": []}
