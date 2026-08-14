"""
stages/validate/oqmd.py — OQMD（Open Quantum Materials Database）公开 API 查询。

无需 API key。材料科学第一性原理（VASP）计算数据库。

API: GET http://oqmd.org/oqmdapi/formationenergy?composition={formula}&limit=10&format=json
     （composition 为查询参数；filter= 需 OPTiMaDe 语法，不采用）
"""

from litdiscovery.common.net import _get


def query_formula(formula: str, limit: int = 10) -> dict:
    """按化学式查询 OQMD，返回 {available, error, results}。

    composition= 是包含式匹配（可命中超结构，如 AlN → Al3N4 等），
    results 内用 formationenergy_id 区分多态。endpoint 失败时标记不可用。
    """
    try:
        resp = _get("https://oqmd.org/oqmdapi/formationenergy",
                    params={"composition": formula, "limit": limit, "format": "json"},
                    timeout=60)
        if resp.status_code != 200:
            return {"available": True, "error": f"OQMD 返回 {resp.status_code}", "results": []}
        data = resp.json()
        results = []
        for d in data.get("data", []) or []:
            results.append({
                "formula": d.get("name"),
                "composition": d.get("composition"),
                "band_gap": d.get("band_gap"),
                "delta_e": d.get("delta_e"),                     # 形成能/原子(eV)
                "stability": d.get("stability"),                 # <0 稳定
                "space_group": d.get("spacegroup"),
                "prototype": d.get("prototype"),
                "formationenergy_id": d.get("formationenergy_id"),
            })
        return {"available": True, "error": None, "results": results}
    except Exception as e:
        return {"available": True, "error": f"查询失败: {type(e).__name__}: {e}", "results": []}
