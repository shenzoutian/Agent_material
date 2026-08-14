"""可选 LLM 升级：仅未分类（UNKNOWN）异常才调用，默认关闭。

LLM 只负责从候选失败类里选一个，不做决策（决策仍是 fallback_handler 的纯规则）。
失败/解析失败一律回落 UNKNOWN，绝不让 LLM 成为新的失败源。
"""

from __future__ import annotations

from typing import Any

from .exceptions import FailureClass, FailureInfo

_TRIAGE_PROMPT = (
    "你是异常分类助手。给定一条异常的类型与消息，请从候选失败类别中选最贴切的一个，"
    "只输出类别名（小写、下划线连接），不要解释。\n"
    "候选类别: rate_limited, network_timeout, resource_exhausted, model_inference, "
    "corrupt_file, not_found, access_denied, unknown\n"
    "异常类型: {exc_name}\n异常消息: {message}\n"
    "请输出类别名："
)

_VALID = {fc.value for fc in FailureClass}


def classify(exc: Exception, info: FailureInfo, llm: Any) -> FailureClass:
    """让 LLM 从候选类里选一个；任何异常都回落 UNKNOWN。"""
    try:
        prompt = _TRIAGE_PROMPT.format(exc_name=type(exc).__name__,
                                       message=(info.message or "")[:500])
        reply = llm.invoke(prompt)
        text = getattr(reply, "content", None) or str(reply)
        text = str(text).strip().lower().splitlines()[0]
        # 容忍 LLM 带引号/标点
        for fc in FailureClass:
            if fc.value in text:
                return fc
    except Exception:
        pass
    return FailureClass.UNKNOWN
