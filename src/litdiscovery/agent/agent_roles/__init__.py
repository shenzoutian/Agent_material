"""
litdiscovery.agent.agent_roles —— executor 角色级能力工具集。

每个角色把阶段能力暴露为独立 @tool，由 executor（agent/orchestrator/pipeline.py）
按 runbook / plan 展开的步骤模板确定性调用。

    registry.py   角色→工具菜单的纯数据 + list_roles 逻辑（零依赖）
    tools.py      角色级 @tool + build_tools（依赖 langchain）

list_roles 从 registry 导出（无 langchain 可用）；build_tools 从 tools 导出。
"""

from .registry import ROLE_TOOL_MAP, format_role_menu  # noqa: F401

# 角色菜单逻辑唯一源在 registry（format_role_menu）；此处仅别名导出，无 langchain 依赖。
list_roles = format_role_menu


def build_tools() -> list:
    """组装 planner 工具列表（惰性加载 tools 模块，避免 CLI 无 langchain 时导入失败）。"""
    from .tools import build_tools as _bt
    return _bt()


__all__ = ["list_roles", "build_tools", "ROLE_TOOL_MAP", "format_role_menu"]
