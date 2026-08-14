"""robust_agent 编排：定位 → 分类 → 决策 → 记录。

对调用方暴露两个入口：
    - handle_exception(...)  三阶段串联，返回 Decision
    - mark_success(...)      成功时重置对应 (stage, operation) 的重试预算/熔断
"""

from __future__ import annotations

from typing import Any

from . import fallback_handler_agent, primary_handler_agent, response_robust_agent
from .exceptions import Decision, FailureInfo


def handle_exception(exc: Exception, *, stage: str = "", operation: str = "",
                     context: dict[str, Any] | None = None, llm: Any = None,
                     batch_root: str | None = None) -> Decision:
    """统一异常处理：定位分类 → 决策 → 记录，返回恢复决策。"""
    info = primary_handler_agent.locate_and_classify(
        exc, stage=stage, operation=operation, context=context, llm=llm,
    )
    decision = fallback_handler_agent.decide(info)
    response_robust_agent.record_and_report(info, decision, batch_root=batch_root)
    return decision


def mark_success(stage: str = "", operation: str = "") -> None:
    """成功时调用：重置该 (stage, operation) 的重试预算与熔断状态。"""
    fallback_handler_agent.budget.mark_success(stage, operation)
