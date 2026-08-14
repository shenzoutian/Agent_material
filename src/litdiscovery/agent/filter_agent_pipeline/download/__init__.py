"""分层文献下载编排。

下载候选按免费开放源、出版社官方 API、可选外部工具三个层级发现。
所有层级均只返回可审计的 URL，实际下载与 PDF 校验统一由 ``pdf_fetch`` 完成。
"""

from .runner import run_download

__all__ = ["run_download"]
