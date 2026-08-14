"""
litdiscovery/agent/extractor_agent_pipeline/tables/cells.py — 表格单元格数值 + 单位解析。

parse_cell 把任意单元格文本解析为：
    {raw, value, unit, is_numeric}
- value: float 或 None（"~2.0"、"2.0"、"2.0 ± 0.1"、科学计数法都能取主值）
- unit:  单位字符串或 None
- is_numeric: 是否含可解析数值（供"该列基本是数值列→推断为属性列"用）

覆盖材料性能常见写法：
    "220" / "20.5" / "1.5×10³" / "1.2e3" / "~4.5" / "4.5 ± 0.3"
    "220 pC/N" / "pC/N"（只有单位）
    "25 °C" / "300 K" / "—" / "n/a" / "—" / "None"
"""

import re

from litdiscovery.agent.extractor_agent_pipeline.tables.registry import UNIT_RE

# 数字：可选前导 ~/±/≈、可选括号、科学计数、乘 10 幂次（×10³ / ×10^3）
_NUM_RE = re.compile(
    r"[~≈±]?\s*(?:\((?P<paren>[^()]+)\)\s*)?"
    r"(?P<num>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][+-]?\d+)?"
    r"(?:\s*[×x]\s*10\s*(?:\^?\{?[+-]?\d+\}?)?)?)",
    re.IGNORECASE,
)

_NON_NUMERIC = {"—", "-", "–", "--", "n/a", "na", "none", "null", "", "..."}

# 数字后紧跟的独立单位 token（不含已用 UNIT_RE 覆盖的复合形式时用）
_TOKEN_UNIT_RE = re.compile(
    r"(pC/N|nC/N|µC/m²|μC/m²|C/m²|W/mK|W/m·K|mV/K|V/K|S/cm|S/m|mS/cm|"
    r"µW/mK²|μW/mK²|µW/m·K²|μW/m·K²|GPa|MPa|eV|meV|kJ/mol|"
    r"°C|oC|K|%|nm|µm|μm|cm|GHz|MHz|kHz|Hz|V|mV|kV|A|mA|W|mW|Ω·cm|Ω.cm|kΩ|MΩ)"
)

# 范围分隔：4.5-5.0 / 3.2–3.5 / 4.2 - 4.8
_RANGE_RE = re.compile(
    r"(?P<lo>-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*[–—−-]\s*"
    r"(?P<hi>-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)"
)


def _to_float(s: str):
    s = s.replace(",", "").replace("×", "e")
    # 10^3 记法：把 "1.5e10^3" 变成 "1.5e3"
    m = re.search(r"e(?P<base>\d+)\^?(?:\{?\}(?P<pow>[+-]?\d+))?$", s)
    if m and m.group("pow"):
        s = s[:m.start("e")] + "e" + m.group("pow")
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def parse_number(s: str):
    """取单元格主数值（优先范围下限，其次第一数字）。"""
    if not s or s.strip().lower() in _NON_NUMERIC:
        return None
    r = _RANGE_RE.search(s)
    if r:
        return _to_float(r.group("lo"))
    m = _NUM_RE.search(s)
    if not m:
        return None
    num = m.group("num")
    if m.group("paren") and not num:
        num = m.group("paren")
    return _to_float(num)


def parse_unit(s: str):
    """取单元格单位：先 UNIT_RE 全匹配，再 token 级提取。"""
    if not s:
        return None
    u = UNIT_RE.search(s)
    if u:
        return u.group(0).strip()
    t = _TOKEN_UNIT_RE.search(s)
    return t.group(0).strip() if t else None


def parse_cell(s: str) -> dict:
    """完整解析一个单元格。"""
    s = (s or "").strip()
    is_numeric = False
    if s.lower() in _NON_NUMERIC:
        value, unit = None, None
    else:
        value = parse_number(s)
        unit = parse_unit(s)
        is_numeric = value is not None or bool(UNIT_RE.search(s))
    return {"raw": s, "value": value, "unit": unit, "is_numeric": is_numeric}


def column_is_numeric(cells: list, threshold: float = 0.7) -> bool:
    """判定一列是否以数值为主（供规则分类推断数值属性列）。"""
    if not cells:
        return False
    nonempty = [c for c in cells if c.strip().lower() not in _NON_NUMERIC]
    if not nonempty:
        return False
    numeric = sum(1 for c in nonempty if parse_number(c) is not None)
    return numeric / len(nonempty) >= threshold
