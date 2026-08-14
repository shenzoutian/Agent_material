"""
litdiscovery/agent/extractor_agent_pipeline/tables/schema.py — 通用记录流 → 属性域材料 JSON（与 tables_output.json 同构）。

to_domain_schema：把 rules.extract_records 的通用 Record 流映射到
agent_roles/prompts/extractor_prompts/<domain>.py 声明的材料性能 schema：
    {"materials": [{"name": ..., "<field>": [{"<numeric_key>": value, ...}]}]}
映射完全由 PROPERTY_DOMAINS[domain]["properties"] 驱动（零硬编码），
因此新增属性域自动获得表格规则抓取能力。

temperature 从测量条件列里识别：条件列名含 temp/temperature/等于 T 时，
其解析值填充该属性的 temperature_key / temperature_unit_key。

structure_schema：把 structure 角色列（space group / crystal structure / lattice）
映射为最小结构 JSON，供 structure.json 合并。
"""

from typing import List, Optional

from litdiscovery.agent.extractor_agent_pipeline.tables.rules import Record
from litdiscovery.agent.extractor_agent_pipeline.tables.registry import match_key, _ascii_alias


def _build_alias_map(domain) -> dict:
    """property_id / symbol / field / label 的归一化键 → 属性 spec。

    两侧都用 match_key（norm_key + 希腊字母拼写展开）归一化，
    使 LLM 返回名（"d33"/"epsilon_r"）与注册表键（"d_33"/"ε_r"）对齐。
    """
    from litdiscovery.agent.extractor_agent_pipeline.extraction.domain_registry import normalize_domain

    amap = {}
    for pid, spec in normalize_domain(domain)["properties"].items():
        for raw in (pid, spec.get("symbol"), spec.get("field"), spec.get("label")):
            if not raw:
                continue
            k = match_key(raw)
            amap.setdefault(k, spec)
            ak = _ascii_alias(k)
            if ak != k:
                amap.setdefault(ak, spec)
    return amap


def _lookup_spec(amap: dict, rec: Record):
    for raw in (rec.property_id, rec.property_symbol, rec.property_label):
        if not raw:
            continue
        k = match_key(raw)
        spec = amap.get(k) or amap.get(_ascii_alias(k))
        if spec:
            return spec
    return None


def _find_temp_condition(rec: Record):
    for cname, cval in rec.condition.items():
        low = cname.strip().lower()
        if "temp" in low or "temperature" in low or low == "t" or low.startswith("t "):
            return cval
    return None


def to_domain_schema(records: List[Record], domain) -> dict:
    """通用记录流 → 指定属性域的材料 JSON。"""
    amap = _build_alias_map(domain)
    from litdiscovery.agent.extractor_agent_pipeline.extraction.domain_registry import normalize_domain

    props = normalize_domain(domain)["properties"]

    materials: dict = {}
    order: List[str] = []
    for rec in records:
        if rec.kind != "numeric":
            continue
        spec = _lookup_spec(amap, rec)
        if not spec:
            continue
        m = materials.setdefault(rec.material, {"name": rec.material})
        if rec.material not in order:
            order.append(rec.material)

        field = spec["field"]
        entry = {}
        if spec.get("numeric_key"):
            entry[spec["numeric_key"]] = rec.value
        if spec.get("unit_key") and rec.unit:
            entry[spec["unit_key"]] = rec.unit
        tc = _find_temp_condition(rec)
        if tc is not None:
            if spec.get("temperature_key") and tc.get("value") is not None:
                entry[spec["temperature_key"]] = tc["value"]
            if spec.get("temperature_unit_key") and tc.get("unit"):
                entry[spec["temperature_unit_key"]] = tc["unit"]
        m.setdefault(field, []).append(entry)

    out = []
    for name in order:
        m = materials[name]
        for pid, spec in props.items():          # 所有属性字段补齐（空数组）
            m.setdefault(spec["field"], [])
        out.append(m)
    return {"materials": out}


def structure_schema(records: List[Record]) -> dict:
    """structure 角色列 → 最小结构 JSON。"""
    materials: dict = {}
    order: List[str] = []
    for rec in records:
        if rec.kind != "structure":
            continue
        m = materials.setdefault(rec.material, {"name": rec.material})
        if rec.material not in order:
            order.append(rec.material)
        h = rec.property_id.lower()
        if "space group" in h or "spacegroup" in h:
            m.setdefault("space_group", []).append(rec.raw)
        elif "crystal" in h or "crystal structure" in h:
            m.setdefault("crystal_structure", []).append(rec.raw)
        elif "lattice" in h:
            m.setdefault("lattice_structure", []).append(rec.raw)
        else:
            m.setdefault("structural_notes", []).append(rec.raw)

    out = []
    for name in order:
        m = materials[name]
        m.setdefault("space_group", None)
        m.setdefault("crystal_structure", None)
        m.setdefault("lattice_structure", None)
        # 数组转单值（取首个），None 保持 None
        for k in ("space_group", "crystal_structure", "lattice_structure"):
            v = m[k]
            if isinstance(v, list):
                m[k] = v[0] if v else None
        out.append(m)
    return {"materials": out}
