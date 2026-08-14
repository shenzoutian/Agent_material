"""robust_agent —— 异常分类与恢复决策的数据结构。

定义三类共享类型：
    - FailureClass   失败分类（决定"可重试 / 该降级 / 该跳过"）
    - FailureInfo    一次异常的完整上下文（位置、类型、可重试性）
    - Decision       恢复决策（skip / retry / abort / degrade）

所有分类与决策都是纯数据 + 枚举，便于单测，不依赖 I/O。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class FailureClass(str, Enum):
    """失败分类（对齐下载层 classify_response 的语义，扩展资源/推理类）。"""

    RATE_LIMITED = "rate_limited"            # 429 / 限流 → 可重试
    NETWORK_TIMEOUT = "network_timeout"      # 超时 / 断连 → 可重试
    RESOURCE_EXHAUSTED = "resource_exhausted"  # OOM / bad allocation → 降级或跳过，不重试
    MODEL_INFERENCE = "model_inference"      # 模型推理失败（如 ONNX 非 OOM 异常）→ 跳过
    CORRUPT_FILE = "corrupt_file"            # 文件结构损坏 → 跳过 + 标记
    NOT_FOUND = "not_found"                  # 404 / 文件缺失 → 跳过
    ACCESS_DENIED = "access_denied"          # 401 / 403 → 跳过
    UNKNOWN = "unknown"                      # 未分类 → 可选 LLM 升级，默认 abort


@dataclass
class FailureInfo:
    """一次异常的完整快照，供决策与审计使用。"""

    exception: Exception
    stage: str = ""                    # 阶段名，如 "fulltext" / "convert" / "pipeline_step"
    operation: str = ""                # 工具名 / 文件名 / 函数名
    context: dict[str, Any] = field(default_factory=dict)   # doi / file / args / attempt 等
    location: str = ""                 # primary_handler 定位：文件名:函数:行号
    failure_class: FailureClass = FailureClass.UNKNOWN
    retryable: bool = False
    message: str = ""


class Decision(str, Enum):
    """恢复决策。"""

    RETRY = "retry"      # 退避后重试（仅限可重试类）
    SKIP = "skip"        # 跳过当前项，继续下一项
    ABORT = "abort"      # 中止当前步骤/流程，交由上层
    DEGRADE = "degrade"  # 降级后续处理（如本批关闭 OCR）
