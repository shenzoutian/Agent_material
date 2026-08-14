"""CLI 下载命令的兼容编排入口。"""

import sys
from pathlib import Path

from litdiscovery.config import DOI_LIST_FILE
from litdiscovery.agent.filter_agent_pipeline.pdf_fetch import default_doi_list, download_batch, load_doi_list, PDF_OUTPUT_SUBDIR
from litdiscovery.agent.filter_agent_pipeline.fulltext import fetch_fulltext_by_doi

FULLTEXT_OUT_SUBDIR = "end_mds"


def _resolve_dois(args):
    if args.doi:
        return [args.doi], Path(".")
    if args.doi_file:
        path = Path(args.doi_file)
        return load_doi_list(path), path.parent
    if args.doi_dir:
        path = Path(args.doi_dir)
        return load_doi_list(path / DOI_LIST_FILE), path
    path = default_doi_list()
    if path is None:
        raise FileNotFoundError(f"未找到 {DOI_LIST_FILE}")
    return load_doi_list(path), path.parent


def run_download(args) -> Path:
    dois, source_dir = _resolve_dois(args)
    if not dois:
        raise ValueError("DOI 列表为空")
    if getattr(args, "fulltext", False):
        out_dir = Path(args.fulltext_out) if args.fulltext_out else source_dir / FULLTEXT_OUT_SUBDIR
        for doi in dois:
            fetch_fulltext_by_doi(doi, out_dir, format_root=source_dir)
        from litdiscovery.agent.extractor_agent_pipeline.preprocess import run_to_markdown
        run_to_markdown(source_dir)
        return source_dir
    output_dir = Path(args.output) if args.output else source_dir / PDF_OUTPUT_SUBDIR
    success, failed = download_batch(dois, output_dir, skip_existing=not args.no_skip)
    print(f"[Summary] 成功 {success}，失败 {failed}，总计 {len(dois)}")
    if failed:
        sys.exit(1)
    return source_dir
