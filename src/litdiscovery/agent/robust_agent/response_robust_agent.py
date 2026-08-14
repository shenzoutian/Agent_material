"""response_robust_agent —— 记录异常处理过程 + 向用户反馈。

职责：
    1. 把一次 (FailureInfo, Decision) 追加写入批次根的 robust_events.jsonl；
    2. 向终端打印一行 GBK 安全的反馈（避免 ⚠/✓ 等字符在 Windows GBK 控制台崩溃）。

断点/execution 回写不在本层做——由集成点（编排层）在拿到决策后自己写
run_state.json / execution.jsonl，保持本层只依赖纯文件追加、可单测。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from threading import Lock

from .exceptions import Decision, FailureInfo

_write_lock = Lock()


def _safe(s: str, limit: int = 160) -> str:
    """压平换行并截断，保证单行、可安全打印。"""
    s = (s or "").replace("\n", " ").replace("\r", " ").strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


def record_and_report(info: FailureInfo, decision: Decision,
                      batch_root: str | Path | None = None) -> dict:
    """记录一条异常处理事件并打印反馈，返回事件 dict 供上层复用。"""
    event = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "stage": info.stage,
        "operation": info.operation,
        "location": info.location,
        "failure_class": info.failure_class.value,
        "decision": decision.value,
        "error": _safe(info.message),
    }

    if batch_root:
        path = Path(batch_root) / "robust_events.jsonl"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with _write_lock, open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
                f.flush()
        except OSError:
            pass  # 记录失败不应阻断恢复流程

    # 终端反馈（GBK 安全，全 ASCII 标记）
    print(f"      [Robust] {info.stage}:{info.operation} "
          f"[{info.failure_class.value}] -> {decision.value} | {_safe(info.message)}")
    return event
