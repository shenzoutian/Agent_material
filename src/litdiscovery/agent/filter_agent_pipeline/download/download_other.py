"""可选外部工具适配层。

Academic-MCP 与 paper-search-cli 的协议和安装方式并不稳定，因此只在用户配置了
命令模板时调用。模板必须输出 JSON 数组或 {"urls": [...]}，未安装/失败直接跳过。
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess

from litdiscovery.agent.filter_agent_pipeline.acquisition import DownloadCandidate, unique_candidates


def _command_candidates(env_name: str, provider: str, doi: str) -> list[DownloadCandidate]:
    template = os.environ.get(env_name, "").strip()
    if not template:
        return []
    try:
        command = shlex.split(template.format(doi=doi))
        completed = subprocess.run(command, capture_output=True, text=True, timeout=45, check=False)
        if completed.returncode:
            return []
        payload = json.loads(completed.stdout)
        urls = payload.get("urls", []) if isinstance(payload, dict) else payload
        return [DownloadCandidate(provider, str(url)) for url in urls if isinstance(url, str)]
    except (OSError, ValueError, subprocess.SubprocessError):
        return []


def discover(doi: str) -> list[DownloadCandidate]:
    return unique_candidates([
        *_command_candidates("ACADEMIC_MCP_DOWNLOAD_COMMAND", "academic_mcp", doi),
        *_command_candidates("PAPER_SEARCH_CLI_DOWNLOAD_COMMAND", "paper_search_cli", doi),
    ])
