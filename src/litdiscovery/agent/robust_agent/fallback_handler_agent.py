"""fallback_handler_agent —— 接收 FailureInfo，决定 skip / retry / abort / degrade。

纯策略层：不碰 I/O，只根据失败类 + 重试预算 + 熔断状态给出决策。
重试只授予可重试类（限流/网络），资源耗尽/模型推理/损坏/缺失一律不重试，
避免 OOM 之类被盲目重试再次崩溃。
"""

from __future__ import annotations

import time
from threading import Lock

from .exceptions import Decision, FailureClass, FailureInfo

# 每个 (stage, operation) 的重试预算（初始 1 次 + 后续最多 RETRY_BUDGET 次重试）
RETRY_BUDGET = 3
# 熔断阈值：同类失败连续 N 次后，本轮进程内禁用该 (stage, operation) 的重试
CIRCUIT_THRESHOLD = 5
# 熔断冷却时间（秒）：超过后重新放行
COOLDOWN_SECONDS = 600


class RetryBudget:
    """进程内的重试预算 + 熔断，按 (stage, operation) 隔离。"""

    def __init__(self):
        self._lock = Lock()
        self._attempts: dict[str, int] = {}
        self._fails: dict[str, int] = {}
        self._opened_at: dict[str, float] = {}

    @staticmethod
    def _key(stage: str, operation: str) -> str:
        return f"{stage}:{operation}"

    def allow_retry(self, stage: str, operation: str) -> bool:
        key = self._key(stage, operation)
        with self._lock:
            opened = self._opened_at.get(key)
            if opened is not None:
                if time.monotonic() - opened < COOLDOWN_SECONDS:
                    return False
                self._fails.pop(key, None)
                self._opened_at.pop(key, None)
            return self._attempts.get(key, 0) < RETRY_BUDGET

    def record_retry(self, stage: str, operation: str) -> None:
        key = self._key(stage, operation)
        with self._lock:
            self._attempts[key] = self._attempts.get(key, 0) + 1
            self._fails[key] = self._fails.get(key, 0) + 1
            if self._fails[key] >= CIRCUIT_THRESHOLD:
                self._opened_at[key] = time.monotonic()

    def mark_success(self, stage: str, operation: str) -> None:
        key = self._key(stage, operation)
        with self._lock:
            self._attempts.pop(key, None)
            self._fails.pop(key, None)
            self._opened_at.pop(key, None)


# 进程级单例：跨调用保持预算/熔断状态
budget = RetryBudget()


def decide(info: FailureInfo) -> Decision:
    """按失败类给出恢复决策。"""
    fc = info.failure_class

    if fc in (FailureClass.RATE_LIMITED, FailureClass.NETWORK_TIMEOUT):
        if budget.allow_retry(info.stage, info.operation):
            budget.record_retry(info.stage, info.operation)
            return Decision.RETRY
        return Decision.SKIP

    if fc is FailureClass.RESOURCE_EXHAUSTED:
        return Decision.DEGRADE

    if fc in (FailureClass.MODEL_INFERENCE, FailureClass.CORRUPT_FILE,
              FailureClass.NOT_FOUND, FailureClass.ACCESS_DENIED):
        return Decision.SKIP

    # UNKNOWN：不擅自吞掉，交由上层中止（可选 LLM 升级由 primary 负责）
    return Decision.ABORT
