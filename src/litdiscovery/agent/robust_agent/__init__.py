"""robust_agent —— 统一异常处理与恢复框架。

三段式职责：
    primary_handler_agent   异常定位 + 类型反馈（规则优先，可选 LLM 升级）
    fallback_handler_agent  接收 FailureInfo，决策 skip / retry / abort / degrade
    response_robust_agent   记录处理过程 + 向用户反馈

对外统一入口：handle_exception / mark_success。
"""

from .exceptions import Decision, FailureClass, FailureInfo
from .handler import handle_exception, mark_success

__all__ = [
    "handle_exception",
    "mark_success",
    "Decision",
    "FailureClass",
    "FailureInfo",
]
