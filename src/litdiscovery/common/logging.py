"""
common/logging.py —— 会话日志统一实现。

日志约定：
    1. 每批一会话：会话目录名 = 批次目录名（artifacts/sessions/<批次名>/），
       planner / executor / CLI / 提取阶段全部写入同一会话，续跑不散建新会话；
    2. 终端 stdout 双写到 <会话>/result_log.txt（redirect_to_session，幂等）；
    3. 每步工具调用落一条结构化记录 <会话>/execution.jsonl
       {agent, tool, args, output, duration_ms, ts}（append_execution_record）。

也保留上下文管理器形式：

    with session_log(session_dir) as log_dir:
        ...  # 期间 stdout 双写到 <session_dir>/result_log.txt
"""

import os
import re
import sys
import json
import io
import threading
from datetime import datetime, timezone
from pathlib import Path

from litdiscovery.paths import BATCHES_ROOT, SESSIONS_ROOT, handoff_path
from litdiscovery.config import (
    DOI_LIST_FILE,
    RESULT_JSON_FILE,
    KEYWORDS_FILE,
)


class Tee:
    """把写入的文本同时输出到控制台与日志文件。"""

    def __init__(self, stream, log_path):
        self._stream = stream
        self._fh = open(log_path, "a", encoding="utf-8")

    def write(self, s):
        try:
            self._stream.write(s)
            self._stream.flush()
        except Exception:
            pass
        self._fh.write(s)
        self._fh.flush()

    def flush(self):
        try:
            self._stream.flush()
        except Exception:
            pass
        self._fh.flush()

    def reconfigure(self, *args, **kwargs):
        """兼容 stdout.reconfigure(...) 调用（Tee 双写不需重配，安全忽略）。"""
        pass

    def close(self):
        if not self._fh.closed:
            self._fh.flush()
            self._fh.close()


def reconfigure_utf8() -> None:
    """Windows 下把 stdin/stdout/stderr 统一为 UTF-8（幂等，安全）。

    stdout 可能是真实 TextIOWrapper（reconfigure 即可），也可能已被 Tee 双写包装
    （reconfigure 为无操作）——统一静默处理，绝不在 import 期替换 sys.stdout，
    避免破坏外层日志双写。
    """
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


class session_log:
    """上下文管理器：进入时把 stdout 双写到 <dir>/result_log.txt，退出恢复。

    用法:
        with session_log(session_dir) as log_dir:
            run_stages()
    """

    def __init__(self, session_dir: str | Path, tag: str = ""):
        self.session_dir = Path(session_dir)
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.session_dir / "result_log.txt"
        self._tag = tag
        self._saved_stdout = None
        self._tee = None

    def __enter__(self):
        if self._tag:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(f"\n{'=' * 60}\n[{self._tag}] {datetime.now().isoformat()}\n{'=' * 60}\n")
        self._saved_stdout = sys.stdout
        self._tee = Tee(sys.stdout, self.log_path)
        sys.stdout = self._tee
        return self.session_dir

    def __exit__(self, exc_type, exc, tb):
        if self._saved_stdout is not None:
            sys.stdout = self._saved_stdout
        if self._tee is not None:
            self._tee.close()
        return False


# 批次/会话目录名中需求文本的最大长度（字符）。
# 需求可能极长（如带路径的中文需求），超长目录名难浏览且易触发 Windows MAX_PATH；
# planner 概括名称通常较短；独立 CLI 的回退名称最多保留 40 个字符。
DIR_LABEL_MAX_LEN = 40


def sanitize_dir_name(name: str) -> str:
    """把需求文本清理为可用于文件夹名的安全字符串（公开 API，保留 80 字上限）。"""
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", name).strip(" ._")
    return name[:80] or "untitled"


def shorten_dir_label(requirement: str, max_len: int = DIR_LABEL_MAX_LEN) -> str:
    """把需求文本压缩为短目录标签：清理后截断保留前 max_len 个字符。

    - 中文需求 5 字即表达主题；英文需求截断到 5 字符（时间戳仍保证唯一）；
    - 空需求回退 "untitled"（不再截断，避免变 "untitl"）。
    """
    safe = sanitize_dir_name(requirement)
    return "untitled" if not safe else safe[:max_len]


def _unique_dir(root: Path, stamp: str, label: str) -> Path:
    """在 root 下建 <label>_<stamp> 目录；同秒重名时在时间戳前追加 _2/_3。

    截断到 5 字后，同秒内不同需求可能共享同一前缀——用递增后缀
    避免两次运行静默复用同一批次目录（破坏数据隔离）。
    """
    folder = root / f"{label}_{stamp}"
    n = 2
    while folder.exists():
        folder = root / f"{label}_{n}_{stamp}"
        n += 1
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def create_log_dir(requirement: str = "", *, label: str | None = None) -> Path:
    """创建批次目录 artifacts/batches/<任务概括>_<时间戳>/。"""
    stamp = datetime.now().strftime("%Y_%m_%d_%H%M%S")
    name = sanitize_dir_name(label) if label else shorten_dir_label(requirement)
    return _unique_dir(BATCHES_ROOT, stamp, name)


def create_session_log(requirement: str = "") -> Path:
    """创建统一日志会话目录 artifacts/sessions/<任务概括>_<时间戳>/。

    兼容旧调用（阶段散建）；新代码请用 session_dir_for_batch（每批一会话）。
    """
    stamp = datetime.now().strftime("%Y_%m_%d_%H%M%S")
    label = shorten_dir_label(requirement) if requirement else "run"
    return _unique_dir(SESSIONS_ROOT, stamp, label)


# ---- 每批一会话 + 结构化工具调用记录 ----

def session_dir_for_batch(batch: str | Path) -> Path:
    """批次固定会话目录 artifacts/sessions/<批次名>/（每批一会话，续跑不新建）。

    会话名 = 批次目录名，与批次一一对应；planner / executor / CLI / 提取阶段
    的终端记录 + execution.jsonl 全部收敛到同一会话，不再按阶段散建多个会话。
    """
    folder = SESSIONS_ROOT / Path(batch).name
    folder.mkdir(parents=True, exist_ok=True)
    return folder


_redirect_stack = []   # [(session_dir, 该次 Tee 包裹前的 stdout)]，支持切换会话


def redirect_to_session(session_dir: str | Path) -> None:
    """把 stdout 双写到 <会话>/result_log.txt。

    - 同一会话幂等（不重复挂 Tee，避免一行写多份）；
    - 切到新会话时先卸载旧 Tee 再挂新的（不留多层 Tee 叠加）。
    """
    global _redirect_stack
    session_dir = Path(session_dir)
    if _redirect_stack and _redirect_stack[-1][0] == session_dir:
        return
    if _redirect_stack:
        prev_stream = _redirect_stack[-1][1]
        current = sys.stdout
        sys.stdout = prev_stream
        _redirect_stack.pop()
        if isinstance(current, Tee):
            current.close()
    session_dir.mkdir(parents=True, exist_ok=True)
    prev_stream = sys.stdout
    sys.stdout = Tee(prev_stream, session_dir / "result_log.txt")
    _redirect_stack.append((session_dir, prev_stream))


_EVENT_LOCK = threading.Lock()
_SECRET_KEYS = ("key", "token", "secret", "password", "authorization")


def _redact(value, key: str = ""):
    if any(part in key.lower() for part in _SECRET_KEYS):
        return "***"
    if isinstance(value, dict):
        return {k: _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


def append_execution_record(batch: str | Path, record: dict) -> None:
    """Append one schema-versioned, redacted workflow event."""
    batch = Path(batch)
    normalized = {
        "schema_version": 1,
        "event": record.get("event", "step_completed"),
        "status": record.get("status", "succeeded"),
        "run_id": batch.name,
        "step_id": record.get("step_id", ""),
        "attempt": record.get("attempt", 1),
        "agent": record.get("agent", ""),
        "tool": record.get("tool", ""),
        "stage": record.get("stage", ""),
        "args": _redact(record.get("args", {})),
        "output": record.get("output", ""),
        "error": record.get("error", ""),
        "duration_ms": record.get("duration_ms", 0),
        "usage": record.get("usage", {}),
        "ts": record.get("ts") or datetime.now(timezone.utc).isoformat(),
    }
    p = session_dir_for_batch(batch) / "execution.jsonl"
    with _EVENT_LOCK, open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(normalized, ensure_ascii=False) + "\n")
        f.flush()


def save_results(papers: list, keywords: list, log_dir: Path):
    """保存完整检索结果（含摘要）到批次 orders/：doi_list.txt / doi_reach_results.json / keywords.txt。"""
    dois = list(dict.fromkeys((p.get("doi") or "").strip() for p in papers))
    dois = [d for d in dois if d]

    doi_list_path = handoff_path(log_dir, DOI_LIST_FILE)
    result_json_path = handoff_path(log_dir, RESULT_JSON_FILE)
    keywords_path = handoff_path(log_dir, KEYWORDS_FILE)
    for p in (doi_list_path, result_json_path, keywords_path):
        p.parent.mkdir(parents=True, exist_ok=True)
    doi_list_path.write_text("\n".join(dois) + ("\n" if dois else ""), encoding="utf-8")
    result_json_path.write_text(
        json.dumps(papers, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    keywords_path.write_text(
        "\n".join(keywords) + ("\n" if keywords else ""), encoding="utf-8"
    )

    print("\n" + "=" * 66)
    print(f"[Summary] 关键词 {len(keywords)} 个，最终收录论文 {len(papers)} 篇，DOI {len(dois)} 个")
    print(f"[Output]  {doi_list_path}（每行一个 DOI，供下载阶段使用）")
    print(f"[Output]  {result_json_path}（完整检索结果 JSON）")
    print(f"[Output]  {keywords_path}（确认后的关键词）")
    print("=" * 66)


def append_log_summary(log_dir: Path, requirement: str, keywords: list,
                       papers: list, status: str = "正常完成"):
    """在 result_log.txt 末尾追加本次运行的元信息摘要。"""
    log_path = log_dir / "result_log.txt"
    dois = list(dict.fromkeys((p.get("doi") or "").strip() for p in papers))
    dois = [d for d in dois if d]
    lines = [
        "",
        "=" * 66,
        f"[运行摘要] 状态: {status}",
        f"[运行时间] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"[科研需求] {requirement}",
        f"[关键词]   {len(keywords)} 个",
    ]
    lines.extend(f"    {i}. {kw}" for i, kw in enumerate(keywords, 1))
    lines.append(f"[检索论文] {len(papers)} 篇")
    lines.append(f"[DOI]      {len(dois)} 个")
    for d in dois:
        lines.append(f"    - {d}")
    lines.append("=" * 66)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
