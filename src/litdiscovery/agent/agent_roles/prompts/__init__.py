"""
litdiscovery/agent/agent_roles/prompts —— 角色提示词与 extractor 提示词的公共导出边界。

extractor_agent 的属性域和工艺提示词全部位于 extractor_prompts/；本模块只做
稳定的兼容导出，使业务模块不依赖提示词文件的内部布局。

roles 的 system_prompt 单一事实源见 prompts/registry.py（ROLE_SYSTEM_PROMPTS）。
"""

from litdiscovery.agent.agent_roles.prompts.extractor_prompts import (
    EXTRACTION_EVIDENCE_RULES,
    PROCESS_EXTRACTION,
    PROPERTY_DOMAINS,
)

__all__ = ["PROPERTY_DOMAINS", "PROCESS_EXTRACTION", "EXTRACTION_EVIDENCE_RULES"]
