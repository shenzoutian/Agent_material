"""
litdiscovery.agent.extractor_agent_pipeline.preprocess —— 全文预处理阶段。

    run_to_markdown(source_dir)      各格式原文（pdfs/xmls/txts/texs）→ markdowns/ → end_mds/
    run_preprocess(input_dir, output_dir, stages)  预处理主入口

子模块：
    convert.py   各格式 → Markdown 转换器（PDF=Docling / XML / TXT / TEX）
    md_parser.py Markdown/XML 全文解析 → fulltext.md + table{i}.csv
    caption_cleaner.py / reference_truncator.py / sentence_filter.py / html_table_converter.py / csv_cleaner.py
"""

from pathlib import Path

from litdiscovery.config import MAX_CONVERT_SRC_BYTES

from .convert import (
    PDF2MarkdownConverter,
    convert_xml_to_md,
    convert_txt_to_md,
    convert_tex_to_md,
    _converter_for,
    _WorkerSupervisor,
)
from litdiscovery.agent.robust_agent import handle_exception
# —— 预处理工具集（原 data_preprocessing 公开 API） ——
from .md_parser import (
    clean,
    extract_md_article,
    extract_md_tables,
    process_md_directory,
    process_xml_directory,
)
from .caption_cleaner import clean_table_captions
from .sentence_filter import count_total_tokens
from .reference_truncator import (
    remove_introduction_and_references,
    remove_image_artifacts,
)
from .html_table_converter import convert_html_tables_to_csv
from .csv_cleaner import drop_sparse_columns


def run_to_markdown(source_dir, max_src_bytes=MAX_CONVERT_SRC_BYTES) -> None:
    """把批次内 pdfs/xmls/txts/texs 各格式原文统一转 markdowns/，再数据预处理到 end_mds/。

    逐文件 try/except，单篇失败不影响主流程；超大源文件直接跳过。
    """
    source_dir = Path(source_dir)
    markdowns = source_dir / "markdowns"
    markdowns.mkdir(parents=True, exist_ok=True)
    # PDF 用子进程隔离转换（Docling/ONNX 的 C++ 级 OOM 会硬杀进程，见 convert.py
    # _WorkerSupervisor 说明）；xml/txt/tex 无需模型，仍在进程内转换。
    pdf_jobs = []
    for sub in ("pdfs", "xmls", "txts", "texs"):
        subdir = source_dir / sub
        if not subdir.is_dir():
            continue
        files = [f for f in subdir.iterdir() if f.is_file()]
        if not files:
            continue
        for f in files:
            out = markdowns / (f.stem + ".md")
            # OA/API 获取阶段可能已提供同 DOI 的 Markdown。它是优先源，不能被
            # 为审计而保留的 XML/TXT/TeX 再转换覆盖。
            if out.exists():
                print(f"  [ToMD] 已有 Markdown 源，跳过转换: {out.name}")
                continue
            # 超大源文件保护
            try:
                if max_src_bytes and f.stat().st_size > max_src_bytes:
                    print(f"  [ToMD] 跳过（源文件过大 {f.stat().st_size / 1024 / 1024:.1f} MB）: {f.name}")
                    continue
            except OSError:
                pass
            kind, _ = _converter_for(str(f))
            if kind == "pdf":
                pdf_jobs.append((str(f), str(out)))
                continue
            try:
                src = str(f)
                if kind == "xml":
                    ok = convert_xml_to_md(src, str(out))
                elif kind == "txt":
                    ok = convert_txt_to_md(src, str(out))
                elif kind == "tex":
                    ok = convert_tex_to_md(src, str(out))
                else:
                    ok = True
                if not ok:
                    print(f"  [ToMD] 转换失败: {f.name}")
            except Exception as e:
                print(f"  [ToMD] {f.name} 转换异常: {type(e).__name__}: {e}")

    # PDF 批量走持久 worker 子进程（模型只加载一次），单篇崩溃/超时只跳过该篇。
    if pdf_jobs:
        supervisor = _WorkerSupervisor()
        for src, out in pdf_jobs:
            ok, err, fault = supervisor.convert(src, out)
            if not ok:
                print(f"  [ToMD] 转换失败: {Path(src).name}"
                      + (f" — {err}" if fault else ""))
                if fault:
                    handle_exception(MemoryError(err), stage="convert",
                                     operation=Path(src).name,
                                     batch_root=str(source_dir))

    print(f"[ToMD] 各格式原文已统一转 markdown → {markdowns}/")
    run_preprocess(markdowns, source_dir / "end_mds", stages=(1,))
    # 下载阶段只在源目录记录短文本状态；待 end_mds 由解析器创建后再同步标记，
    # 从而保证下载阶段不会预建或伪造处理产物。
    for marker in markdowns.glob("*.too_small"):
        processed_dir = source_dir / "end_mds" / marker.stem
        if processed_dir.is_dir():
            (processed_dir / ".too_small").write_text("", encoding="utf-8")
    print(f"[ToMD] 数据预处理完成 → {source_dir / 'end_mds'}/")


def run_preprocess(input_dir, output_dir, stages=(1,)) -> None:
    """数据预处理主入口（Markdown/XML → fulltext.md + table CSV，含清洗与 token 统计）。"""
    from .main import main as _main
    _main([
        "--input", str(input_dir), "--output", str(output_dir),
        "--stage", ",".join(str(s) for s in stages),
    ])


__all__ = [
    "run_to_markdown", "run_preprocess",
    "clean", "extract_md_article", "extract_md_tables",
    "process_md_directory", "process_xml_directory",
    "clean_table_captions", "count_total_tokens",
    "remove_introduction_and_references", "remove_image_artifacts",
    "convert_html_tables_to_csv", "drop_sparse_columns",
    "PDF2MarkdownConverter", "convert_xml_to_md", "convert_txt_to_md", "convert_tex_to_md",
]
