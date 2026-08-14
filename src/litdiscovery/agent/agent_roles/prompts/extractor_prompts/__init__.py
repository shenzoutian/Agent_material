"""Extractor-agent domain and process prompt registry.

All prompts used by ``extractor_agent`` live in this package. Other subsystems should
consume ``PROPERTY_DOMAINS`` and ``PROCESS_EXTRACTION`` instead of importing domain
modules directly.
"""

from .ferroelectric import PROPERTY_DOMAIN as _ferroelectric
from .phasechange import PROPERTY_DOMAIN as _phasechange
from .piezoelectric import PROPERTY_DOMAIN as _piezoelectric
from .process import PROCESS_EXTRACTION
from .thermoelectric import PROPERTY_DOMAIN as _thermoelectric

PROPERTY_DOMAINS = {
    "thermoelectric": _thermoelectric,
    "ferroelectric": _ferroelectric,
    "piezoelectric": _piezoelectric,
    "phasechange": _phasechange,
}

EXTRACTION_EVIDENCE_RULES = """

通用证据规则：
- 只提取原文明示的信息；不得用常识补全、由图形趋势估读、由其他属性计算，除非任务明确要求计算。
- 数值必须与材料、属性、单位和测试/计算条件来自同一证据上下文；无法可靠配对时留空。
- 保留原始材料写法、数值和单位，不擅自归一化；范围、误差和不等号不得压成单点值。
- 区分实验值、计算值、文献转引值与作者预测；综述引用的数据不能伪装成本论文实验结果。
- 若输出 schema 支持 source/evidence 字段，写入最短充分原句及章节/表号；不支持时不得把证据塞入其他字段。
- 缺失字段使用 null 或空数组。只输出 schema 指定的合法 JSON，不加解释或 Markdown。
"""

for _domain in PROPERTY_DOMAINS.values():
    for _prompt in _domain.get("prompts", {}).values():
        if isinstance(_prompt, dict) and _prompt.get("system"):
            _prompt["system"] = _prompt["system"].rstrip() + EXTRACTION_EVIDENCE_RULES

__all__ = ["PROPERTY_DOMAINS", "PROCESS_EXTRACTION", "EXTRACTION_EVIDENCE_RULES"]
