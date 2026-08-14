"""
stages/extraction/prompting.py —— 按属性域渲染提取 prompt。

依赖 PROPERTY_DOMAINS（依赖方向：extraction → prompts）与
domain_registry.normalize_domain（str 静态键 / dict 动态域统一解析）。
"""

from litdiscovery.llm_utils import render
from litdiscovery.agent.extractor_agent_pipeline.extraction.domain_registry import normalize_domain


def render_prompt_pair(domain, key: str, **user_kwargs) -> tuple:
    """按 domain 渲染指定 prompt 的 system/user 两段（属性/结构提取链的便利封装）。

    domain 支持 str（静态四域键 / 动态域 label）或 dict（完整域或注册表 spec），
    统一由 normalize_domain 解析为完整域后再取 prompts[key]。
    """
    dom = normalize_domain(domain)
    prompts = dom["prompts"][key]
    return render(prompts["system"], prompts["user"], **user_kwargs)
