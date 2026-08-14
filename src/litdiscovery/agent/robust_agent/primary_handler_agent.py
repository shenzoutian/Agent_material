"""primary_handler_agent —— 异常定位 + 类型反馈（规则优先，LLM 仅作升级）。

常见异常走纯规则映射，不调用 LLM；仅未命中且开启 LLM_TRIAGE_ENABLED 且
调用方传入 llm 实例时才升级到 llm_triage。这样每个常见失败都是零 token 的
确定性分类，只有真正陌生的异常才花一次 LLM 调用。
"""

from __future__ import annotations

import traceback
from typing import Any

from .exceptions import FailureClass, FailureInfo

# 是否对未分类异常启用 LLM 定位（默认关闭）。开启后仍只对 UNKNOWN 类生效。
LLM_TRIAGE_ENABLED = False

# 可重试的失败类（决策层据此给 RETRY，其余类一律不重试）
_RETRYABLE = {FailureClass.RATE_LIMITED, FailureClass.NETWORK_TIMEOUT}

# HTTP 状态 → 失败类（对齐 acquisition.classify_response 的语义，自包含避免耦合）
_STATUS_CLASS = {
    401: FailureClass.ACCESS_DENIED,
    403: FailureClass.ACCESS_DENIED,
    404: FailureClass.NOT_FOUND,
    410: FailureClass.NOT_FOUND,
    429: FailureClass.RATE_LIMITED,
}
_RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


def _status_code(exc: Exception) -> int | None:
    """从 requests 风格异常里尽量挖出 HTTP 状态码。"""
    resp = getattr(exc, "response", None)
    for attr in ("status_code", "status"):
        v = getattr(resp, attr, None)
        if isinstance(v, int):
            return v
    v = getattr(exc, "status_code", None)
    return v if isinstance(v, int) else None


def _locate(exc: Exception) -> str:
    """从 traceback 取最内层调用帧，格式化为 `文件名:函数:行号`。"""
    tb = exc.__traceback__
    if tb is None:
        return f"{type(exc).__name__}"
    frame = None
    while tb is not None:
        frame = tb.tb_frame
        tb = tb.tb_next
    if frame is None:
        return f"{type(exc).__name__}"
    return (f"{frame.f_code.co_filename}:"
            f"{frame.f_code.co_name}:{frame.f_lineno}")


def _rule_classify(exc: Exception) -> FailureClass:
    """纯规则分类：异常类型名 + 消息 + 状态码 → FailureClass。

    顺序敏感：先判资源耗尽（含 ONNX 的 bad allocation），再判模型推理。
    """
    name = type(exc).__name__
    msg = str(exc)

    # 1. 资源耗尽（OOM / bad allocation）——不可重试，降级/跳过
    if name == "MemoryError" or "bad allocation" in msg or "out of memory" in msg.lower():
        return FailureClass.RESOURCE_EXHAUSTED

    # 2. 网络超时 / 断连——可重试
    if name in ("Timeout", "ConnectionError", "ConnectTimeout", "ReadTimeout",
                "ProxyError", "SSLError"):
        return FailureClass.NETWORK_TIMEOUT
    if "timeout" in msg.lower() or "timed out" in msg.lower():
        return FailureClass.NETWORK_TIMEOUT

    # 3. HTTP 状态码（requests.HTTPError 等）
    code = _status_code(exc)
    if code is not None:
        if code in _STATUS_CLASS:
            return _STATUS_CLASS[code]
        if code in _RETRYABLE_STATUS:
            return FailureClass.RATE_LIMITED if code == 429 else FailureClass.NETWORK_TIMEOUT

    # 4. 模型推理失败（ONNX/推理引擎非 OOM 异常，或 Docling 转换失败）
    #    注意：不把通用 RuntimeError 归入此类——它太宽泛，会误伤编排层的
    #    各种运行时错误（超时/参数错误等），应当走 UNKNOWN → ABORT。
    if name in ("ONNXRuntimeError", "RuntimeException", "ModelInferenceError") \
            or "docling" in msg.lower():
        return FailureClass.MODEL_INFERENCE

    # 5. 文件损坏 / 缺失
    if name in ("FileNotFoundError", "IsADirectoryError", "NotADirectoryError"):
        return FailureClass.NOT_FOUND
    if name in ("UnicodeDecodeError", "BadZipFile", "EOFError"):
        return FailureClass.CORRUPT_FILE

    # 6. 认证/权限
    if name in ("PermissionError", "HTTPBasicError"):
        return FailureClass.ACCESS_DENIED

    return FailureClass.UNKNOWN


def locate_and_classify(exc: Exception, *, stage: str = "", operation: str = "",
                        context: dict[str, Any] | None = None,
                        llm: Any = None) -> FailureInfo:
    """定位异常并返回带分类的 FailureInfo。"""
    info = FailureInfo(
        exception=exc,
        stage=stage,
        operation=operation,
        context=context or {},
        location=_locate(exc),
        message=str(exc),
    )
    fc = _rule_classify(exc)
    if fc is FailureClass.UNKNOWN and LLM_TRIAGE_ENABLED and llm is not None:
        fc = _llm_triage_classify(exc, info, llm)
    info.failure_class = fc
    info.retryable = fc in _RETRYABLE
    return info


def _llm_triage_classify(exc: Exception, info: FailureInfo, llm: Any) -> FailureClass:
    """未分类异常才走这里：让 LLM 从候选类里选一个。失败则回落 UNKNOWN。"""
    from . import llm_triage
    return llm_triage.classify(exc, info, llm)


# 供外部（如 llm_triage 开关配置）读取失败类候选
FAILURE_CLASSES = [fc.value for fc in FailureClass]
