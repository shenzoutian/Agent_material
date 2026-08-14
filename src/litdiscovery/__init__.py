"""
litdiscovery —— 科研文献信息提取驱动科研发现 agent。

统一包根。所有源码收于 src/litdiscovery/：
    paths/           产物根唯一事实源（artifacts/ 布局）
    config/          AGENT_ROLES 注册表 + create_agent 工厂
    llm_utils/       LLM 消息调用 + JSON 鲁棒解析共享底座
    common/          零依赖底座（net/json/logging/fs/markdown）
    prompts/         角色 system_prompt 单一事实源 + 属性域注册表
    roles/           executor 确定性工具集（build_tools）
    stages/          阶段实现层（extraction/gap/validate/tables/preprocess）
    retrieval/       文献检索与全文获取
    orchestrator/    planner 纯路由 + executor 确定性执行编排层
    memory/knowledge/ 长期记忆与知识沉淀
    cli/             统一命令行入口（litdiscovery）
    contracts/runtime/repositories/services/ 稳定契约与执行基础设施
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
