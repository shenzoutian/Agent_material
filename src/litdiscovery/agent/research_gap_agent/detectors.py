"""
检测层：纯 pandas 确定性检测，无 LLM。

- detect_underexplored：well-studied 材料缺某属性
- detect_missing_links：两高频概念从未共现
- detect_structure_contradiction：同材料家族 ≥2 篇不同晶体结构
- detect_numeric_contradiction：同 (材料,属性,温度桶) 数值差超阈值（数据不足时标记）

每个候选带 evidence[{doi, title, detail}] 供裁决层排除假阳性。
"""

import pandas as pd

MIN_SUPPORT = 2
TOP_K_MISSING = 200


def _evidence(doi, title, detail, source):
    return {"doi": doi, "title": title, "detail": detail, "source": source}


def detect_underexplored(props_df, struct_df, papers, specs, *, min_papers=MIN_SUPPORT):
    """well-studied 材料（≥min_papers 篇）缺失某属性 → 候选。

    属性 universe 取 specs 里 unit_known 的属性（可测可比）；process_claim 不计入。
    """
    if props_df.empty and struct_df.empty:
        return []
    # 材料家族 → 论文数（struct/process/props 出现均计入，更稳健）
    paper_by_fam = {}
    for _, r in struct_df.iterrows():
        if r.get("material_family"):
            paper_by_fam.setdefault(r["material_family"], set()).add(r["doi"])
    for _, r in props_df.iterrows():
        if r.get("material_family"):
            paper_by_fam.setdefault(r["material_family"], set()).add(r["doi"])
    well_studied = {f for f, s in paper_by_fam.items() if len(s) >= min_papers}
    if not well_studied:
        return []

    # 已覆盖的 (family, property)：只计结构化数值与 process_claim（已报告数值）
    covered = set()
    for _, r in props_df.iterrows():
        if r.get("material_family") and r.get("property_key"):
            covered.add((r["material_family"], r["property_key"]))

    # 属性 universe：specs 中 unit_known 的属性键（domain:prop）
    prop_keys = sorted(k for k, s in specs.items() if s.get("unit_known"))

    candidates = []
    for fam in sorted(well_studied):
        for pk in prop_keys:
            if (fam, pk) not in covered:
                dois = sorted(paper_by_fam[fam])
                candidates.append({
                    "id": f"ug-{len(candidates)+1:03d}",
                    "type": "underexplored",
                    "statement": f"材料 {fam} 已被多篇研究但未报道属性 {pk}",
                    "concept_a": fam, "concept_b": pk,
                    "evidence": [_evidence(d, papers.get(_folder_by_doi(papers, d), {}).get("title", ""),
                                           f"{fam} 出现于该篇", "structured") for d in dois],
                    "support_papers": dois,
                    "source": "detector",
                })
    return candidates


def _folder_by_doi(papers, doi):
    """反查 doi → folder（用于 evidence title）。papers 是 folder->meta dict。"""
    for fname, meta in papers.items():
        if meta.get("doi") == doi:
            return fname
    return ""


def detect_missing_links(concepts_df, *, min_papers=MIN_SUPPORT, top_k=TOP_K_MISSING):
    """两概念各自高频出现但从未共现 → 候选。

    用 paper_concepts 的 materials×methods 共现矩阵（pivot O(n)），
    过滤 min-support 行列后枚举零元格，按 min(support_a, support_b) 排序取 top_k。
    """
    if concepts_df.empty or not {"doi", "concept", "type"}.issubset(concepts_df.columns):
        return []
    mat = concepts_df[concepts_df["type"] == "materials"]
    met = concepts_df[concepts_df["type"] == "methods"]
    if mat.empty or met.empty:
        return []
    # 每篇：材料集合 × 方法集合（同一篇算共现）
    co = []
    for doi in set(mat["doi"]):
        mats = set(mat[mat["doi"] == doi]["concept"])
        meths = set(met[met["doi"] == doi]["concept"])
        for m in mats:
            for h in meths:
                co.append({"doi": doi, "material": m, "method": h})
    co_df = pd.DataFrame(co)
    if co_df.empty:
        return []
    counts = co_df.pivot_table(index="material", columns="method", aggfunc="size", fill_value=0)
    row_sup = counts.sum(axis=1)
    col_sup = counts.sum(axis=0)
    ra = row_sup[row_sup >= min_papers].index
    cb = col_sup[col_sup >= min_papers].index
    if len(ra) == 0 or len(cb) == 0:
        return []
    zero = counts.loc[ra, cb] == 0
    candidates = []
    for m in ra:
        for h in cb:
            if zero.loc[m, h]:
                candidates.append({
                    "id": f"ml-{len(candidates)+1:03d}",
                    "type": "missing_link",
                    "statement": f"材料 {m} 与工艺 {h} 各自被研究但从未在同一篇论文中结合",
                    "concept_a": m, "concept_b": h,
                    "support_score": min(row_sup[m], col_sup[h]),
                    "evidence": [
                        _evidence(doi, "", f"材料 {m} 出现", "abstract")
                        for doi in sorted(mat[mat["concept"] == m]["doi"])
                    ][:8] + [
                        _evidence(doi, "", f"工艺 {h} 出现", "abstract")
                        for doi in sorted(met[met["concept"] == h]["doi"])
                    ][:8],
                    "source": "detector",
                })
    candidates.sort(key=lambda c: c["support_score"], reverse=True)
    return candidates[:top_k]


def detect_structure_contradiction(struct_df, papers, *, min_papers=MIN_SUPPORT):
    """同材料家族 ≥2 篇不同 normalized crystal → 候选（当前唯一生效的矛盾检测）。"""
    if struct_df.empty or not {"material_family", "crystal_norm", "doi"}.issubset(struct_df.columns):
        return []
    # 只保留有 crystal 且跨 ≥2 篇的家族
    rows = struct_df[struct_df["crystal_norm"].str.len() > 0].copy()
    if rows.empty:
        return []
    grp = rows.groupby("material_family")
    candidates = []
    for fam, g in grp:
        if g["doi"].nunique() >= min_papers and g["crystal_norm"].nunique() >= 2:
            by_crystal = g.groupby("crystal_norm")["doi"].unique().to_dict()
            candidates.append({
                "id": f"sc-{len(candidates)+1:03d}",
                "type": "contradiction",
                "subtype": "structure",
                "statement": (f"材料 {fam} 在不同论文中报道了不同晶体结构："
                              + "; ".join(f"{c}({len(d)}篇)" for c, d in by_crystal.items())),
                "concept_a": fam, "concept_b": "crystal_structure",
                "evidence": [
                    _evidence(d, papers.get(_folder_by_doi(papers, d), {}).get("title", ""),
                              f"晶体结构={c}", "structured")
                    for c, dlist in by_crystal.items() for d in dlist
                ],
                "support_papers": sorted(g["doi"].unique()),
                "source": "detector",
            })
    return candidates


def detect_numeric_contradiction(props_df, *, min_papers=MIN_SUPPORT, rel_diff=0.5, temp_bucket=50):
    """同 (材料,属性,温度桶) 数值差超阈值 → 候选。

    数据不足时返回 (候选[], {data_sufficient: False, note, missing_groups})。
    """
    if props_df.empty:
        return [], {"data_sufficient": False, "note": "无可比数值", "missing_groups": []}
    # 只看结构化数值（排除 process_claim 文本）
    num = props_df[props_df["source"] != "process_claim"].copy()
    if num.empty:
        return [], {"data_sufficient": False, "note": "无结构化数值", "missing_groups": []}
    num["temp_bucket"] = (num["temp_K"] // temp_bucket).fillna(-1).infer_objects(copy=False)
    candidates = []
    for (fam, pk, tb), g in num.groupby(["material_family", "property_key", "temp_bucket"]):
        if g["doi"].nunique() < min_papers:
            continue
        vals = g["value"].dropna()
        if len(vals) < min_papers:
            continue
        span = vals.max() - vals.min()
        base = max(abs(vals.min()), 1e-9)
        if span / base > rel_diff:
            candidates.append({
                "id": f"nc-{len(candidates)+1:03d}",
                "type": "contradiction", "subtype": "numeric",
                "statement": (f"材料 {fam} 在 {pk} 上报道差异 >{rel_diff*100:.0f}%"
                              f"（温度桶 {tb}，值 {vals.min():.3g}~{vals.max():.3g}）"),
                "concept_a": fam, "concept_b": pk,
                "evidence": [
                    _evidence(r["doi"], "", f"{pk}={r['value']:.3g}@{r.get('temp_K')}",
                              r["source"])
                    for _, r in g.iterrows()
                ],
                "support_papers": sorted(g["doi"].unique()),
                "source": "detector",
            })
    return candidates, {"data_sufficient": True, "note": ""}


def run_all(result: dict, papers_meta: dict) -> dict:
    """运行全部检测器，返回 {underexplored, missing_links, contradictions, numeric_contradiction, flags}。"""
    props_df = result["props_df"]
    struct_df = result["struct_df"]
    concepts_df = result["concepts_df"]
    specs = result.get("specs", {})
    ug = detect_underexplored(props_df, struct_df, papers_meta, specs)
    ml = detect_missing_links(concepts_df)
    sc = detect_structure_contradiction(struct_df, papers_meta)
    nc, nc_flags = detect_numeric_contradiction(props_df)
    return {
        "underexplored": ug,
        "missing_links": ml,
        "structure_contradictions": sc,
        "numeric_contradictions": nc,
        "numeric_flags": nc_flags,
    }
