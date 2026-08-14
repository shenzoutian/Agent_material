"""
物化层：把 artifacts/extracted/ 结构化产物 + end_mds/ fulltext 摘要，物化为三张统一表。

- DOI 解析：override → folder 解码校验 → title 归一化匹配 → prefix 兜底（带 resolution 标记）
- spec 自动生成：从 PROPERTY_DOMAINS 生成各属性域的取值键，canonical_units 补单位换算因子
- 三表：material_props（长表）/ material_struct（宽表）/ paper_concepts（摘要概念）
- 性能值补洞：从 process.json 的 advantages 挖器件级数值，标记 source="process_claim"
- ledger 缓存：按 (folder, spec_version) 增量，避免重复 LLM 摘要概念提取
"""

import os
import re
import json
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

import pandas as pd

from litdiscovery.config import MAX_FULLTEXT_BYTES
from litdiscovery.agent.agent_roles.prompts import PROPERTY_DOMAINS


SPEC_VERSION = "1.0"


# 路径常量

@dataclass
class CorpusPaths:
    """一次语料的路径集合（一批检索的 end_mds + data_doi + 权威 doi 表）。"""
    end_mds: Path            # end_mds/ 目录（fulltext.md + token_count.txt）
    data_doi: Path           # data_doi/ 目录（结构化产物）
    doi_results_json: Path   # doi_reach_results.json（权威 DOI 表）
    gap_data_dir: Path       # 输出三表 + ledger + override 的目录


# ============================================================
# DOI 解析
# ============================================================

def _load_authority(doi_results_json: Path) -> List[dict]:
    """加载权威 DOI 表（doi_reach_results.json），返回记录列表。"""
    if not doi_results_json.exists():
        return []
    with open(doi_results_json, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list) and v:
                return v
        return []
    return data if isinstance(data, list) else []


def _normalize_title(title: str) -> str:
    """标题归一化：NFKD、小写、非字母数字折叠。"""
    if not title:
        return ""
    t = unicodedata.normalize("NFKD", str(title))
    t = t.lower()
    t = re.sub(r"[^a-z0-9]+", "", t)
    return t


def _folder_to_doi_candidate(folder_name: str) -> str:
    """folder 名 → DOI 候选：下划线还原为斜杠。"""
    return folder_name.replace("_", "/")


def _strip_parens(doi: str) -> str:
    """去括号（folder 名由 md_parser 剥掉了非词字符）。"""
    return re.sub(r"[^\w./\-]", "", doi)


def _lookup_by_title(title: str, authority: List[dict]) -> Optional[dict]:
    """按归一化标题在权威表中匹配。"""
    nt = _normalize_title(title)
    if not nt:
        return None
    for rec in authority:
        if _normalize_title(rec.get("title", "")) == nt:
            return rec
    return None


def _lookup_by_prefix(folder_name: str, authority: List[dict]) -> Optional[dict]:
    """按 DOI 前缀兜底：仅当唯一匹配时接受。"""
    prefix = folder_name.split("_")[0]
    hits = [r for r in authority if r.get("doi", "").startswith(prefix)]
    return hits[0] if len(hits) == 1 else None


def resolve_doi(folder_name: str, title: str, authority: List[dict],
                overrides: Dict[str, str]) -> dict:
    """四级解析 folder → 权威 DOI 记录。

    返回 dict: {doi, record, resolution}  resolution ∈ exact|decode|title|prefix|missing
    """
    # 1) override（人工修正 typo）
    if folder_name in overrides:
        doi = overrides[folder_name]
        rec = next((r for r in authority if r.get("doi") == doi), None)
        return {"doi": doi, "record": rec, "resolution": "override"}

    # 2) folder 直接解码，对权威列表校验
    decoded = _strip_parens(_folder_to_doi_candidate(folder_name))
    if decoded:
        rec = next((r for r in authority if r.get("doi") == decoded), None)
        if rec:
            return {"doi": rec["doi"], "record": rec, "resolution": "decode"}

    # 3) title 归一化匹配
    rec = _lookup_by_title(title, authority)
    if rec and rec.get("doi"):
        return {"doi": rec["doi"], "record": rec, "resolution": "title"}

    # 4) prefix 兜底（唯一匹配才接受）
    rec = _lookup_by_prefix(folder_name, authority)
    if rec and rec.get("doi"):
        return {"doi": rec["doi"], "record": rec, "resolution": "prefix"}

    return {"doi": decoded or folder_name, "record": None, "resolution": "missing"}


def load_overrides(gap_data_dir: Path) -> Dict[str, str]:
    """加载 folder→doi override 表（gap_data/doi_overrides.json）。"""
    path = gap_data_dir / "doi_overrides.json"
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


# ============================================================
# 概念提取 ledger 缓存（按 folder + spec_version 增量，避免重复 LLM 摘要概念提取）
# ============================================================

def _ledger_path(gap_data_dir: Path) -> Path:
    return Path(gap_data_dir) / "concepts_ledger.json"


def _load_ledger(gap_data_dir: Path) -> Dict[str, dict]:
    """读取概念提取 ledger，返回 {folder: {materials, methods, properties}}。

    仅当文件 spec_version 与当前 SPEC_VERSION 一致时返回条目；缺失/损坏/版本不符
    一律返回空 dict（触发重新提取）。
    """
    path = _ledger_path(gap_data_dir)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict) or data.get("spec_version") != SPEC_VERSION:
        return {}
    entries = data.get("entries") or {}
    return entries if isinstance(entries, dict) else {}


def _save_ledger(gap_data_dir: Path, entries: Dict[str, dict]) -> None:
    """原子写回概念提取 ledger。"""
    Path(gap_data_dir).mkdir(parents=True, exist_ok=True)
    tmp = _ledger_path(gap_data_dir).with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"spec_version": SPEC_VERSION, "entries": entries},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_ledger_path(gap_data_dir))


# ============================================================
# spec 自动生成
# ============================================================

# 规范单位注册表：(domain, prop_id) -> (target_unit, {unit_variant: factor})
# 没有条目的属性视为"原始单位即规范单位"，unit_known=False，矛盾检测降级。
CANONICAL_UNITS = {
    ("thermoelectric", "seebeck_coefficient"): ("μV/K", {"μV/K": 1, "uv/k": 1, "µV/K": 1, "V/K": 1e6, "v/k": 1e6}),
    ("thermoelectric", "electrical_conductivity"): ("S/m", {"S/m": 1, "s/m": 1, "S/cm": 100, "s/cm": 100}),
    ("thermoelectric", "electrical_resistivity"): ("Ω·m", {"Ω·m": 1, "ohm·m": 1, "Ω·cm": 0.01, "ohm·cm": 0.01}),
    ("thermoelectric", "power_factor"): ("μW/m·K²", {"μW/m·K²": 1, "uw/m·k2": 1}),
    ("thermoelectric", "thermal_conductivity"): ("W/m·K", {"W/m·K": 1, "w/m·k": 1}),
    ("ferroelectric", "remanent_polarization"): ("μC/cm²", {"μC/cm²": 1, "uc/cm2": 1}),
    ("ferroelectric", "saturation_polarization"): ("μC/cm²", {"μC/cm²": 1, "uc/cm2": 1}),
    ("ferroelectric", "coercive_field"): ("kV/cm", {"kV/cm": 1, "kv/cm": 1}),
    ("piezoelectric", "piezoelectric_coefficient_d33"): ("pC/N", {"pC/N": 1, "pc/n": 1, "C/N": 1e12}),
    ("piezoelectric", "piezoelectric_coefficient_d31"): ("pC/N", {"pC/N": 1, "pc/n": 1, "C/N": 1e12}),
    ("phasechange", "crystallization_temperature"): ("°C", {"°C": 1, "c": 1, "℃": 1}),
    ("phasechange", "melting_temperature"): ("°C", {"°C": 1, "c": 1, "℃": 1}),
    ("phasechange", "data_retention_temperature"): ("°C", {"°C": 1, "c": 1, "℃": 1}),
    ("phasechange", "amorphous_resistivity"): ("Ω·cm", {"Ω·cm": 1, "ohm·cm": 1, "Ω·m": 100}),
    ("phasechange", "crystalline_resistivity"): ("Ω·cm", {"Ω·cm": 1, "ohm·cm": 1, "Ω·m": 100}),
    ("phasechange", "thermal_conductivity"): ("W/m·K", {"W/m·K": 1, "w/m·k": 1}),
    ("phasechange", "threshold_switching_voltage"): ("V", {"V": 1, "v": 1, "mV": 0.001}),
    ("phasechange", "switching_speed"): ("ns", {"ns": 1, "nsec": 1, "µs": 1000, "μs": 1000}),
    ("phasechange", "endurance_cycles"): ("次", {"cycles": 1, "次": 1}),
}


def build_specs(domains=PROPERTY_DOMAINS, canonical_units=CANONICAL_UNITS) -> dict:
    """从 PROPERTY_DOMAINS 自动生成各属性域的取值 spec。

    返回 {domain:prop_id -> {domain, field, symbol, vkeys, tkeys, ukeys, tukeys, target_unit, unit_known}}
    """
    specs = {}
    for dkey, dom in domains.items():
        for pid, p in dom.get("properties", {}).items():
            k = f"{dkey}:{pid}"
            target, _factor = canonical_units.get((dkey, pid), ("", {}))
            specs[k] = {
                "domain": dkey,
                "field": p.get("field"),
                "symbol": p.get("symbol"),
                "vkeys": [p.get("numeric_key"), "value", "values"],
                "tkeys": ([p.get("temperature_key"), "temperature", "Temperature", "T"]
                          if p.get("temperature_key") else ["temperature", "Temperature", "T"]),
                "ukeys": ([p.get("unit_key"), "unit", "Unit"]
                          if p.get("unit_key") else ["unit", "Unit"]),
                "tukeys": ([p.get("temperature_unit_key"), "Temperature_unit", "T_unit", "temp_unit"]
                           if p.get("temperature_unit_key") else ["Temperature_unit", "T_unit", "temp_unit"]),
                "target_unit": target,
                "unit_known": bool(target),
            }
    return specs


# ============================================================
# 规范化
# ============================================================

_MATERIAL_FAMILY_PATTERNS = [
    # 提取基础化学式（去掉掺杂/相标签后的基体）
    (re.compile(r"^(.*?)[:：]\s*.*$"), r"\1"),                       # "GaN:Ge" -> "GaN"
    (re.compile(r"^([A-Za-z]{1,3}\d?[A-Za-z]?)\s*(?:-\s*.*|/.*)?$"), r"\1"),
]


def normalize_material(raw: str) -> Tuple[str, str]:
    """返回 (material_raw, material_family)。

    family 为去掉化学计量/掺杂/相标签后的基体名，用于跨论文分组（覆盖/共现）；
    raw 保留原名，用于证据与裁决。
    例：Sc0.3Al0.7N -> raw 保留, family=ScAlN；GaN:Ge -> family=GaN
    """
    raw = (raw or "").strip()
    if not raw:
        return raw, ""
    fam = raw
    # 去括号标签
    fam = re.sub(r"[\(\[（\[][^\)\]）\]]*[\)\]）\]]", "", fam).strip()
    # 去化学计量下标（数字压缩）
    fam = re.sub(r"\d+(\.\d+)?", "", fam)
    # 去相标签 / 变体后缀
    fam = re.sub(r"[_\-].*$", "", fam).strip()
    if not fam:
        return raw, raw
    # 应用 family 提取模式（"GaN:Ge"→"GaN"；首元素 + 连字符/斜杠后缀）
    for pat, repl in _MATERIAL_FAMILY_PATTERNS:
        if pat.fullmatch(fam) is not None:
            fam = pat.sub(repl, fam).strip()
            break
    return raw, fam or raw


def normalize_method(m: str) -> str:
    """工艺方法归一化：小写、去连字符与括号标签、取核心词。"""
    m = (m or "").strip().lower()
    m = re.sub(r"\([^)]*\)", "", m)          # "(PAMBE)" 去掉
    m = re.sub(r"[^a-z0-9 ]", " ", m)         # 连字符/斜杠 -> 空格
    m = re.sub(r"\s+", " ", m).strip()
    return m


def normalize_crystal(c: str) -> str:
    """晶体结构归一化：小写、去空格/连字符。"""
    c = (c or "").strip().lower()
    c = re.sub(r"[^a-z0-9]", "", c)
    return c


# ============================================================
# 数值抽取 / 单位换算
# ============================================================

def _to_num(x) -> Optional[float]:
    """尽力转 float。"""
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip().replace(",", "")
    m = re.search(r"-?\d+(\.\d+)?([eE][-+]?\d+)?", s)
    return float(m.group(0)) if m else None


def _convert_unit(value: float, unit: str, spec: dict) -> Tuple[float, str]:
    """按 spec 的 canonical unit 换算。无换算表时原值返回并标 unit_known=False。"""
    if not spec.get("unit_known") or not unit:
        return value, unit
    factor = _unit_factor(spec, unit)
    return value * factor, spec["target_unit"]


def _unit_factor(spec: dict, unit: str) -> float:
    """查 spec 对应属性的单位换算因子。"""
    for (dkey, pid), (target, factors) in CANONICAL_UNITS.items():
        if f"{dkey}:{pid}" == spec.get("_key"):
            return factors.get(unit.strip().lower(), 1.0)
    return 1.0


# 简化：把 _key 挂到 spec 上（build_specs 已建 dict，这里在调用方补充）
def _attach_key(specs: dict) -> dict:
    """为每个 spec 附 _key，供 _unit_factor 反查。"""
    out = {}
    for k, v in specs.items():
        v = dict(v)
        v["_key"] = k
        out[k] = v
    return out


# ============================================================
# 物化主流程
# ============================================================

def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _extract_abstract(fulltext: str) -> str:
    """从 fulltext.md 提取摘要段（正文 `Abstract -` 标记后，到下个 === 前）。"""
    m = re.search(r"Abstract\s*[-:]\s*(.*?)(?:\n\s*\n\s*===|\Z)", fulltext, re.DOTALL)
    if m:
        return m.group(1).strip()
    # fallback: Preamble 后首个段落
    m = re.search(r"=== Preamble ===\s*\n(.*?)(?:\n\s*\n\s*===|\Z)", fulltext, re.DOTALL)
    return m.group(1).strip()[:2000] if m else ""


def _extract_title(fulltext: str) -> str:
    """从 fulltext.md 提取标题（头部 Title: 行，若为 PEER REVIEWED 等垃圾则取正文首行）。"""
    m = re.search(r"^Title:\s*(.*)$", fulltext, re.MULTILINE)
    t = m.group(1).strip() if m else ""
    if not t or t.lower() in ("peer reviewed", "n/a", ""):
        # 取正文首个非空段落行作为标题（通常是作者行前的论文标题）
        for line in fulltext.splitlines():
            s = line.strip()
            if s and not s.startswith("===") and not s.lower().startswith(("title:", "doi:", "abstract:")):
                return s[:200]
    return t


def _iter_prop_rows(perf: dict, specs: dict) -> List[dict]:
    """从 performance.json（混合文件）提取属性数值行。

    performance.json 可能混入结构块（无数值键），这里只取有 spec 数值键的材料。
    对每个属性 spec，从材料里按 vkeys 取 value 数组，抽 (value, unit, temp)。
    """
    rows = []
    specs = _attach_key(specs)
    for mat in perf.get("materials", []):
        if not isinstance(mat, dict) or not mat.get("name"):
            continue
        raw, fam = normalize_material(mat.get("name"))
        for skey, spec in specs.items():
            vals = mat.get(spec["field"]) or []
            if not isinstance(vals, list):
                vals = [vals]
            for v in vals:
                if not isinstance(v, dict):
                    v = {"value": v}
                num = _to_num(v.get(spec["vkeys"][0])) or _to_num(v.get("value"))
                if num is None:
                    continue
                unit = _pick_str(v, spec["ukeys"]) or ""
                val_std, unit_std = _convert_unit(num, unit, spec)
                temp = _to_num(_pick_str(v, spec["tkeys"]))
                temp_unit = (_pick_str(v, spec["tukeys"]) or "K").lower()
                if temp is not None and "c" in temp_unit:
                    temp += 273.15
                rows.append({
                    "doi": "", "material_raw": raw, "material_family": fam,
                    "property_key": skey, "property_symbol": spec["symbol"],
                    "value": val_std, "unit": unit_std, "temp_K": temp,
                    "source": "structured",
                    "sample_form": v.get("sample_form") or "",
                    "phase_state": v.get("phase_state") or "",
                    "measurement_method": v.get("measurement_method") or "",
                    "heating_rate": v.get("heating_rate") or "",
                    "film_thickness": v.get("film_thickness") or "",
                    "pulse_type": v.get("pulse_type") or "",
                    "pulse_width": v.get("pulse_width") or "",
                    "evidence_quote": v.get("evidence_quote") or "",
                    "evidence_section": v.get("evidence_section") or "",
                    "evidence_page": v.get("evidence_page"),
                    "evidence_table": v.get("evidence_table") or "",
                    "evidence_table_row": v.get("evidence_table_row") or "",
                    "evidence_table_column": v.get("evidence_table_column") or "",
                    "crystallization_definition": v.get("crystallization_definition") or "",
                    "value_origin": v.get("value_origin") or "",
                    "endurance_basis": v.get("endurance_basis") or "",
                })
    return rows


def _pick_str(d: dict, keys: list) -> Optional[str]:
    for k in keys:
        if k and d.get(k) is not None and str(d.get(k)).strip():
            return str(d[k]).strip()
    return None


def _iter_struct_rows(struct: dict) -> List[dict]:
    """从 structure.json 提取结构行。"""
    rows = []
    for mat in struct.get("materials", []):
        if not isinstance(mat, dict) or not mat.get("name"):
            continue
        raw, fam = normalize_material(mat.get("name"))
        dop = mat.get("doping") or {}
        rows.append({
            "doi": "", "material_raw": raw, "material_family": fam,
            "processing_method_norm": normalize_method(mat.get("processing_method") or ""),
            "crystal_norm": normalize_crystal(mat.get("crystal_structure") or ""),
            "compound_type": mat.get("compound_type"),
            "space_group": mat.get("space_group"),
            "dopants": dop.get("dopants") or [],
            "source": "structured",
        })
    return rows


def _iter_process_claim_rows(proc: dict, specs: dict) -> List[dict]:
    """从 process.json 的 advantages 挖器件级数值（k²/Q/FoM 等），标记 process_claim。

    advantages 是文本字符串（可能含数值），作为"该材料报告了数值"的弱证据，
    用于 underexplored（哪些材料有/无任何数值）而非数值矛盾。
    """
    rows = []
    for mat in proc.get("materials", []):
        if not isinstance(mat, dict) or not mat.get("name"):
            continue
        raw, fam = normalize_material(mat.get("name"))
        advs = mat.get("advantages") or []
        # 有数值的 advantage 提取首个数值作为粗糙证据
        for adv in advs:
            num = _to_num(adv)
            rows.append({
                "doi": "", "material_raw": raw, "material_family": fam,
                "property_key": "process_claim", "property_symbol": "claim",
                "value": num, "unit": "", "temp_K": None,
                "source": "process_claim", "detail": adv,
            })
    return rows


def materialize(corpus: CorpusPaths, llm=None, *, force=False, limit: int = 0) -> dict:
    """主物化函数。返回 {props_df, struct_df, concepts_df, papers, specs, corpus}。

    force=True 忽略概念提取 ledger 强制重提取；limit>0 只物化前 limit 个文件夹。
    概念提取结果写入 <gap_data_dir>/concepts_ledger.json，供跨调用增量复用。
    """
    specs = build_specs()
    authority = _load_authority(corpus.doi_results_json)
    overrides = load_overrides(corpus.gap_data_dir)
    ledger = {} if force else _load_ledger(corpus.gap_data_dir)

    props_rows, struct_rows, concept_rows = [], [], []
    papers = {}  # folder -> {doi, title, resolution, source_flags}

    folders = sorted(corpus.end_mds.iterdir()) if corpus.end_mds.is_dir() else []
    if limit and limit > 0:
        folders = folders[:limit]
    for folder in folders:
        if not folder.is_dir():
            continue
        fname = folder.name
        fulltext_path = folder / "fulltext.md"
        fulltext = ""
        if fulltext_path.exists():
            # 超大全文保护：超过阈值视为无全文（跳过概念提取，避免读爆内存）
            if MAX_FULLTEXT_BYTES and fulltext_path.stat().st_size > MAX_FULLTEXT_BYTES:
                print(f"[Gap] Skipping oversized fulltext for {fname} "
                      f"(>{MAX_FULLTEXT_BYTES / 1024 / 1024:.0f} MB)")
            else:
                with open(fulltext_path, "r", encoding="utf-8") as f:
                    fulltext = f.read()
        title = _extract_title(fulltext)
        res = resolve_doi(fname, title, authority, overrides)
        doi = res["doi"]

        # data_doi 批次层：优先 <data_doi>/<批次名>/<fname>，回退旧扁平 <data_doi>/<fname>
        batch_name = corpus.end_mds.parent.name if corpus.end_mds else ""
        data_doi_dir = corpus.data_doi / batch_name / fname if batch_name else Path()
        if not data_doi_dir.is_dir():
            data_doi_dir = corpus.data_doi / fname
        has_structured = False
        if data_doi_dir.is_dir():
            perf = _read_json(data_doi_dir / "performance.json")
            struct = _read_json(data_doi_dir / "structure.json")
            proc = _read_json(data_doi_dir / "process.json")
            if perf.get("materials"):
                for r in _iter_prop_rows(perf, specs):
                    r["doi"] = doi
                    props_rows.append(r)
            if struct.get("materials"):
                for r in _iter_struct_rows(struct):
                    r["doi"] = doi
                    struct_rows.append(r)
                has_structured = True
            if proc.get("materials"):
                for r in _iter_process_claim_rows(proc, specs):
                    r["doi"] = doi
                    props_rows.append(r)
                has_structured = has_structured or True

        # 摘要概念（LLM 或规则降级）。始终尝试：能补未进 data_doi 的论文覆盖。
        abstract = _extract_abstract(fulltext)
        concepts = {"materials": [], "methods": [], "properties": []}
        if abstract and llm is not None:
            cached = ledger.get(fname)
            if isinstance(cached, dict) and isinstance(cached.get("materials"), list):
                concepts = cached
            else:
                concepts = _extract_concepts(abstract, llm)
                ledger[fname] = concepts
        elif abstract:
            concepts = _rule_concepts(abstract)  # 无 LLM 时的规则降级
        for ctype, items in concepts.items():
            for item in items:
                item = str(item).strip()
                if not item:
                    continue
                concept_rows.append({
                    # concept 统一小写，避免 "deposition"/"Deposition" 被当两个概念
                    "doi": doi, "type": ctype, "concept": item.lower(),
                    "source": "structured" if has_structured else "abstract",
                })

        papers[fname] = {
            "doi": doi, "title": title,
            "resolution": res["resolution"],
            "has_structured": has_structured,
            "has_fulltext": bool(fulltext),
            "abstract": abstract[:500],
        }

    props_df = pd.DataFrame(props_rows)
    struct_df = pd.DataFrame(struct_rows)
    concepts_df = pd.DataFrame(concept_rows)
    if llm is not None:
        _save_ledger(corpus.gap_data_dir, ledger)
    return {
        "props_df": props_df, "struct_df": struct_df, "concepts_df": concepts_df,
        "papers": papers, "specs": specs, "corpus": corpus,
    }


def _extract_concepts(abstract: str, llm) -> dict:
    """用 LLM 从摘要提取 {materials, methods, properties} 概念词。"""
    from litdiscovery.llm_utils import invoke_messages, robust_json_parse
    system = """你从论文摘要中提取三类概念词：
- materials: 材料/化合物名称（如 AlN, ScAlN, BaTiO3）
- methods: 制备/加工/表征方法（如 MBE, sputtering, X-ray diffraction）
- properties: 性能/参数类型（如 seebeck coefficient, k2, quality factor, resistivity）
只返回 JSON：{"materials": [...], "methods": [...], "properties": [...]}，去重，缺失给空数组。"""
    user = f"摘要：\n```{abstract}```"
    try:
        out = invoke_messages(llm, system, user)
        data = robust_json_parse(out.content)
        return {
            "materials": data.get("materials", []) or [],
            "methods": data.get("methods", []) or [],
            "properties": data.get("properties", []) or [],
        }
    except Exception:
        return _rule_concepts(abstract)


def _rule_concepts(abstract: str) -> dict:
    """无 LLM 时的规则降级：简单关键词抽取。"""
    materials = list(set(re.findall(r"\b(?:AlN|ScAlN|GaN|BaTiO3|PZT|LiNbO3|SiC|Al2O3|PVDF)\b", abstract)))
    methods = list(set(re.findall(r"\b(?:MBE|sputtering|CVD|MOCVD|deposition|annealing|sintering)\b", abstract, re.I)))
    props = list(set(re.findall(r"\b(?:seebeck|resistivity|conductivity|permittivity|coupling|quality factor|polarization)\b", abstract, re.I)))
    return {"materials": materials, "methods": methods, "properties": props}


def save_materialized(result: dict, out_dir: Path):
    """落盘三表 CSV + papers 清单。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    result["props_df"].to_csv(out_dir / "material_props.csv", index=False, encoding="utf-8")
    result["struct_df"].to_csv(out_dir / "material_struct.csv", index=False, encoding="utf-8")
    result["concepts_df"].to_csv(out_dir / "paper_concepts.csv", index=False, encoding="utf-8")
    with open(out_dir / "papers.json", "w", encoding="utf-8") as f:
        json.dump(result["papers"], f, indent=2, ensure_ascii=False)
