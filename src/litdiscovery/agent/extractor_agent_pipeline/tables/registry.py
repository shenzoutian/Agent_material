"""
litdiscovery/agent/extractor_agent_pipeline/tables/registry.py — 属性符号注册表与领域词表。

注册表由 prompts.PROPERTY_DOMAINS 动态构建（零硬编码），
这样新增属性域（在 agent_roles/prompts/extractor_prompts 中注册）后表格解析自动覆盖。

归一化 norm_key：统一小写、去空白/下划线/连字符、下标数字转普通数字，
并保留 unicode 希腊字母（ε_r → εr）；对常见希腊字母同时给 ascii 别名。

除属性符号外，还定义表头角色分类用到的词表：
    STRUCTURE_KEYWORDS  结构描述列（space group / crystal structure ...）
    MATERIAL_KEYWORDS   材料标识列（material / sample / composition ...）
    CONDITION_KEYWORDS  测量条件列（temperature / pressure / x ...）
    IGNORE_KEYWORDS     可忽略列（no. / ref / footnote ...）
    UNIT_TOKEN_RE       材料科学常见单位（用于单元格解析与表头去单位）
"""

import re
import unicodedata
from dataclasses import dataclass, field
from typing import List, Optional

from litdiscovery.agent.agent_roles.prompts import PROPERTY_DOMAINS


# === 归一化 ===
_SUBSCRIPT_DIGITS = {ord(c): str(i) for i, c in enumerate("₀₁₂₃₄₅₆₇₈₉")}
_GREEK_ASCII = {
    "ε": "e", "κ": "k", "σ": "s", "ρ": "rho", "τ": "t", "μ": "u",
    "α": "a", "β": "b", "γ": "g", "δ": "d", "ω": "w", "λ": "l",
    "χ": "x", "η": "h", "θ": "th", "ξ": "xi", "φ": "phi", "ψ": "psi",
    "Δ": "delta", "Σ": "sigma",
}


def norm_key(s: str) -> str:
    """归一化用于匹配：小写、去空白/下划线/连字符/点、下标转数字。"""
    s = unicodedata.normalize("NFKC", s).lower()
    s = s.translate(_SUBSCRIPT_DIGITS)
    return re.sub(r"[\s_\-\.·×()（）]", "", s)


def _ascii_alias(s: str) -> str:
    """把 unicode 希腊字母转成 ascii（用于模糊匹配增强）。"""
    for g, a in _GREEK_ASCII.items():
        s = s.replace(g, a)
    return s


# 拼写出的希腊字母名 → 希腊字母（LLM 常写 "epsilon_r" 而注册表是 "ε_r"）
_GREEK_NAMES = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
    "zeta": "ζ", "eta": "η", "theta": "θ", "iota": "ι", "kappa": "κ",
    "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ", "omicron": "ο",
    "pi": "π", "rho": "ρ", "sigma": "σ", "tau": "τ", "upsilon": "υ",
    "phi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω",
}


def expand_greek_names(s: str) -> str:
    """把拼写的希腊字母名替换成希腊字母（epsilon_r → ε_r）。"""
    for name, g in _GREEK_NAMES.items():
        s = s.replace(name, g)
    return s


def match_key(s: str) -> str:
    """统一匹配键：norm_key + 希腊字母拼写展开。

    两侧（注册表键 与 LLM 返回名）都用它，即可对齐：
        "ε_r"  vs "epsilon_r"  →  εr
        "d_33" vs "d33"        →  d33
        "k_p"  vs "kp"         →  kp
    """
    return expand_greek_names(norm_key(s))


# === 属性注册表 ===
@dataclass
class PropertySpec:
    domain: str          # 来源属性域，如 "piezoelectric"
    property_id: str     # "piezoelectric_coefficient_d33"
    label: str           # "压电常数d33"
    symbol: str          # "d_33"
    field: str           # "piezoelectric_coefficient_d33"
    numeric_key: str     # "d_33_value"
    unit_key: Optional[str]
    temperature_key: Optional[str]
    temperature_unit_key: Optional[str]
    aliases: List[str] = field(default_factory=list)   # 归一化后匹配别名

    def matches_header(self, header_key: str, header_key_ascii: str) -> bool:
        """header_key / header_key_ascii 是否命中本属性任意别名。

        双向匹配：
        - 精确：header == alias
        - 正向：长别名包含短表头（"seebeckcoefficient" 命中 "Seebeck"）
        - 反向：短别名被长表头包含（"d33" 在 "d33 (pC/N)" 去掉单位后命中）
        护栏：len>=3 才做包含匹配；反向含仅当表头 len>=4，
        避免 "t"/"k" 这类短表头误中任意长别名。
        """
        for a in self.aliases:
            if not a:
                continue
            if header_key == a:
                return True
            # 正向：别名在表头里（表头是长名，如 "electrical conductivity (S/cm)"）
            if len(a) >= 3 and a in header_key:
                return True
            # 反向：表头是别名的干净前缀（"Seebeck" ⊂ "seebeckcoefficient"）
            if len(header_key) >= 4 and header_key in a:
                return True
            if header_key_ascii:
                if len(a) >= 3 and a in header_key_ascii:
                    return True
                if len(header_key_ascii) >= 4 and header_key_ascii in a:
                    return True
        return False


def build_registry(domains: Optional[dict] = None) -> List[PropertySpec]:
    """从 PROPERTY_DOMAINS（或自定义 domains dict）构建属性注册表。"""
    domains = domains or PROPERTY_DOMAINS
    specs: List[PropertySpec] = []
    seen: set = set()
    for domain, dom in domains.items():
        for pid, prop in dom.get("properties", {}).items():
            spec = PropertySpec(
                domain=domain,
                property_id=pid,
                label=prop.get("label", ""),
                symbol=prop.get("symbol", ""),
                field=prop.get("field", pid),
                numeric_key=prop.get("numeric_key"),
                unit_key=prop.get("unit_key"),
                temperature_key=prop.get("temperature_key"),
                temperature_unit_key=prop.get("temperature_unit_key"),
            )
            # 别名：symbol / field / label 各归一化一份，希腊字母再给 ascii 版
            al = []
            for raw in (spec.symbol, spec.field, spec.label):
                if raw:
                    al.append(norm_key(raw))
                    al.append(_ascii_alias(norm_key(raw)))
            # 特例：d_33 → d33 已由去下划线覆盖；ε_r → εr 已覆盖。
            # 剔除单字母别名（t/k/s/σ...）：在表头里太模糊，
            # 会抢走 condition 列（如 "T (°C)"）；其长别名（field/label）仍可用。
            spec.aliases = list(dict.fromkeys(a for a in al if len(a) >= 2))
            key = (spec.property_id, spec.symbol)
            if key not in seen:
                seen.add(key)
                specs.append(spec)
    return specs


# === 表头角色词表 ===
STRUCTURE_KEYWORDS = [
    "space group", "spacegroup", "space_group", "crystal structure",
    "crystalstructure", "lattice", "latticeconstant", "symmetry",
    "prototype", "pearson", "spacegroupnumber", "structure",
]

MATERIAL_KEYWORDS = [
    "material", "sample", "composition", "formula", "compound",
    "specimen", "alloy", "name", "materials", "samples", "stoichiometry",
]

CONDITION_KEYWORDS = [
    "temperature", "temp", "pressure", "doping", "dopant", "content",
    "atmosphere", "voltage", "poling", "frequency", "wavelength",
    "polarization field", "electric field", "annealing", "sintering",
    "composition x", "x (in", "parameter",
]

IGNORE_KEYWORDS = ["no.", "no ", "index", "ref", "reference", "footnote", "remark", "note"]

# 表头可能是独立单位列
UNIT_HEADER_KEYWORDS = ["unit", "units"]

# 材料类表头：化学式模式，如 "Sc0.3Al0.7N" / "BaTiO3" / "PVDF"
_FORMULA_TOKEN_RE = re.compile(
    r"^[A-Z][a-z]?\d*(?:\.\d+)?(?:[A-Z][a-z]?\d*(?:\.\d+)?)*$")


def looks_like_formula(header: str) -> bool:
    """表头是否为化学式（如 BaTiO3 / Sc0.3Al0.7N / PZT 不可行 → 排除无数字多字母）。"""
    h = header.strip()
    return bool(_FORMULA_TOKEN_RE.match(h)) and len(h) >= 2


# === 材料科学常见单位 ===
# 优先级：复合单位在前（µW/m·K² 等），简单单位在后。
_UNIT_PATTERNS = [
    r"µW/m·K²", r"µW m[−-]1 K[−-]2", r"μW/mK", r"W/m·K", r"W/mK",
    r"W/cm·K", r"mV/K", r"V/K", r"S/cm", r"S/m", r"mS/cm", r"Ω·cm",
    r"Ω.cm", r"kΩ", r"MΩ", r"GPa", r"MPa", r"Pa", r"eV", r"meV",
    r"pC/N", r"nC/N", r"µC/m²", r"μC/m²", r"C/m²", r"kJ/mol", r"kJ kg[−-]1 K[−-]1",
    r"K", r"°C", r"oC", r"%", r"nm", r"Å", r"cm", r"mm", r"µm", r"μm", r"GHz",
    r"MHz", r"kHz", r"Hz", r"V", r"mV", r"kV", r"A", r"mA", r"W", r"mW",
]
UNIT_RE = re.compile("|".join(_UNIT_PATTERNS))


def strip_unit(header: str) -> str:
    """从表头剥离单位部分：去掉括号/方括号包裹的单位，再去掉尾部独立单位。"""
    h = header
    # (pC/N) 或 [pC/N] 或 （pC/N）
    h = re.sub(r"[\(\[（][^)\]]*[\)\]）]", " ", h)
    h = UNIT_RE.sub(" ", h)
    return re.sub(r"\s+", " ", h).strip()
