"""
litdiscovery.agent.extractor_agent_pipeline.extraction —— 属性/工艺提取阶段（extractor_agent 职责）。

属性域支持静态四域（agent_roles/prompts/extractor_prompts/<domain>.py）与运行时动态注册表（domain_registry），
统一由 normalize_domain 解析。

    domain_registry.py   动态属性域注册表：validate(Pydantic) / build_prompts /
                         normalize_domain / generate（LLM 生成 + 静态域回退）
    prompting.py         按属性域渲染 prompt（str 静态键 / dict 动态域统一解析）
    property_extract.py  材料候选/属性/结构/表格提取（LLM 无关，llm 由调用方传入）
    judge.py             judge_verify_properties（LLM 裁判验证）
    process_extract.py   classify 分类门（含动态域注入）+ 工艺提取
    graph.py             LangGraph 提取工作流（无模块级全局，RuntimeCfg 承载配置）
    api.py               run_extract_batch() 批量编排入口
"""

from . import (prompting, property_extract, judge, process_extract,  # noqa: F401
               domain_registry, graph, api)
