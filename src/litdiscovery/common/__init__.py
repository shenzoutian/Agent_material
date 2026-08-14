"""
litdiscovery.common —— 零依赖共享底座。

    fs.py        文件系统工具：safe_folder_name（DOI→目录名统一规约）、原子写、路径改写
    net.py       HTTP 请求（带退避重试）
    json.py      LLM 输出容错 JSON 提取 / 摘要重建 / 字段清洗
    logging.py   会话日志（Tee 双写 / 目录 / 结果落盘）
    markdown.py  XML / LaTeX → markdown 转换
"""

from . import fs, net, json, logging, markdown  # noqa: F401
