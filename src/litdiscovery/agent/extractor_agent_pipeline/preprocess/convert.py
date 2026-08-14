"""
stages/preprocess/convert.py —— 多格式文献统一转 Markdown 工具。

将 PDF / XML / TXT / LaTeX(tex) 文献统一转换为 Markdown，供预处理链统一处理为
end_mds/<doi>/fulltext.md（提取入口）。

默认处理对象:
    自动定位最新批次根中 最小一级格式目录：
      pdfs/   → PDF（Docling 版面分析）
      xmls/   → XML 全文（Elsevier/NXML，复用 common.markdown）
      txts/   → TXT 纯文本（CORE/EuropePMC 全文）
      texs/   → LaTeX 源码（arXiv，复用 common.markdown）
    只处理各格式目录内的原文文件，统一输出到与它们同级的 markdowns/。
    也可通过 -i/--input 指定自定义路径（文件或目录，按扩展名分发）。

输出:
    Markdown 文件默认保存在与 pdfs 文件夹同级的 markdowns/ 文件夹中，
    文件名与原始文件一致（仅扩展名替换为 .md）。
    可通过 -o/--output 自定义输出目录。

PDF 转换（Docling）:
    - 基于视觉语言模型的高精度文档解析（layout + table + reading order）
    - 首次运行需下载约 500MB 模型权重（HF_ENDPOINT=https://hf-mirror.com 可加速）
    - XML/TXT/TEX 无需模型，快速转换（纯文本/转换器）

依赖:
    pip install docling   （仅 PDF 转换需要；xml/txt/tex 无需）

用法:
    # 默认转换最新批次 pdfs/ + xmls/ + txts/ + texs/
    python to_markdown.py

    # 指定输入路径（按扩展名分发转换器）
    python to_markdown.py -i ./my_pdfs

    # 指定输出目录
    python to_markdown.py -i ./my_pdfs -o ./md_output

    # 跳过已存在的输出文件（增量转换）
    python to_markdown.py -i ./my_pdfs --skip-existing
"""

import os
import sys

import argparse
import collections
import json
import subprocess
import threading
import queue
import time
import logging
import warnings

from litdiscovery.common.logging import reconfigure_utf8

# 保证 UTF-8 输出（幂等；stdout 已被 Tee 双写包装时静默跳过）。
reconfigure_utf8()

from litdiscovery.config import MAX_CONVERT_SRC_BYTES, OCR_BATCH_SIZE, CONVERT_WORKER_TIMEOUT
from litdiscovery.paths import SESSIONS_ROOT, latest_batch
from litdiscovery.agent.robust_agent import Decision, handle_exception

# 日志配置（写入统一产物根，不再落在项目根/包内）
LOG_FILE = SESSIONS_ROOT / "to_markdown.txt"

logger = logging.getLogger("litdiscovery.to_markdown")
logger.setLevel(logging.INFO)

formatter = logging.Formatter(
    fmt="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# 文件 Handler — 写入独立的 to_markdown.txt（会话目录自动创建）
SESSIONS_ROOT.mkdir(parents=True, exist_ok=True)
file_handler = logging.FileHandler(str(LOG_FILE), encoding="utf-8")
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

# 控制台 Handler — 同步输出到终端
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

# 批次根下直接存放文献原文的最小一级格式目录
FORMAT_SUBDIRS = ("pdfs", "xmls", "txts", "texs")

# 默认输出目录：与 pdfs 同级的 markdowns/
def get_default_output_dir(input_path: str) -> str:
    """计算默认输出目录。

    规则: 若输入是 pdfs/ 目录（其兄弟目录为 markdowns/），则输出到
    与 pdfs 同级的 markdowns/ 文件夹；否则输出到输入目录本身。

    参数:
        input_path: 输入路径（pdfs 目录或自定义路径）

    返回:
        输出目录路径
    """
    input_abs = os.path.abspath(input_path)
    base = os.path.basename(input_abs).lower()
    # pdfs/ 或批次根 → 输出到同级的 markdowns/
    if os.path.isdir(input_abs) and base in ("pdfs", "xmls", "txts", "texs"):
        return os.path.join(os.path.dirname(input_abs), "markdowns")
    return os.path.join(input_abs, "markdowns")


# HF 镜像配置（必须在 import docling 之前设置）
def setup_hf_mirror(use_mirror: bool = False, mirror_url: str = "https://hf-mirror.com"):
    """配置 HuggingFace 镜像端点，需在导入 docling 之前调用。"""
    if use_mirror or os.environ.get("HF_MIRROR") == "1":
        os.environ["HF_ENDPOINT"] = mirror_url
        logger.info(f"已启用 HF 镜像: {mirror_url}")

# Docling 转换器封装
class PDF2MarkdownConverter:
    """封装 IBM Docling 的 PDF → Markdown 转换逻辑。

    使用 Docling 的 StandardPdfPipeline：
    1. 页面渲染 + 版面分析（Accurate Layout Model）
    2. 表格检测（TableFormer）→ 结构化 Markdown 表格（可选）
    3. 阅读顺序重建
    4. 导出为 Markdown

    首次初始化时需下载约 500MB 模型权重。若表格模型不可用，
    会自动降级为跳过表格结构识别（表格区域仍会保留为文本）。
    """

    def __init__(self, enable_table_structure: bool = True):
        self._converter = None
        self._table_structure_enabled = enable_table_structure
        self._actual_table_structure = False  # 实际是否成功启用表格识别

    def _make_pdf_option(self, do_table: bool):
        """构建 PdfFormatOption（正确的 Docling API：PdfPipelineOptions 需
        通过 PdfFormatOption 包装后才能传入 format_options）。"""
        from docling.document_converter import PdfFormatOption
        from docling.datamodel.pipeline_options import PdfPipelineOptions

        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_table_structure = do_table
        # 缩小 OCR 页批大小：ConvTranspose 上采样内存峰值与批大小成正比，
        # 批处理 4 页在扫描版大页上会 onnxruntime "bad allocation"（OOM）。
        pipeline_options.ocr_batch_size = OCR_BATCH_SIZE
        return PdfFormatOption(pipeline_options=pipeline_options)

    @property
    def converter(self):
        """延迟加载 Docling DocumentConverter，表格模型缺失时自动降级。"""
        if self._converter is None:
            try:
                from docling.document_converter import DocumentConverter, InputFormat

                if self._table_structure_enabled:
                    logger.info(
                        "Docling 初始化中"
                        "（首次运行需下载 ~500MB 模型权重）..."
                    )
                else:
                    logger.info("Docling 初始化中（已禁用表格结构识别）...")

                fmt_opt = self._make_pdf_option(self._table_structure_enabled)
                self._converter = DocumentConverter(
                    format_options={InputFormat.PDF: fmt_opt},
                )
                self._actual_table_structure = self._table_structure_enabled
                logger.info("Docling 初始化成功")

                status = "已启用" if self._table_structure_enabled else "已禁用"
                logger.info(f"表格结构识别: {status}")

                hf_home = os.environ.get("HF_HOME", os.path.expanduser("~/.cache/huggingface"))
                logger.info(f"HF 模型缓存目录: {hf_home}")

            except ImportError:
                logger.error("未安装 docling。请执行: pip install docling")
                raise
            except Exception as e:
                msg = str(e)
                if "ConnectTimeout" in msg or "ConnectError" in msg:
                    logger.error(
                        "HuggingFace 连接超时。请尝试：\n"
                        "  1) 使用 --hf-mirror 参数启用国内镜像\n"
                        "  2) 设置环境变量: set HF_ENDPOINT=https://hf-mirror.com\n"
                        f"原始错误: {e}"
                    )
                    raise

                # 如果表格模型不可用且原本想启用，则降级重试
                if "TableModel" in msg or "Not able to find a model file" in msg:
                    if self._table_structure_enabled:
                        logger.warning(
                            "表格结构识别模型不可用，自动降级："
                            "跳过表格结构提取（表格内容仍会保留为文本）。\n"
                            "  如需完整表格识别，请确保网络可访问 HuggingFace "
                            "并使用 --hf-mirror 下载模型权重。"
                        )
                        self._converter = None
                        self._table_structure_enabled = False
                        from docling.document_converter import DocumentConverter, InputFormat
                        fmt_opt = self._make_pdf_option(False)
                        self._converter = DocumentConverter(
                            format_options={InputFormat.PDF: fmt_opt},
                        )
                        self._actual_table_structure = False
                        logger.info("Docling 初始化成功（无表格结构识别模式）")
                    else:
                        raise
                else:
                    logger.error(f"Docling 初始化失败: {e}")
                    raise
        return self._converter

    def convert_single(self, pdf_path: str, output_path: str) -> bool:
        """转换单个 PDF 文件为 Markdown。

        参数:
            pdf_path: 输入 PDF 文件的路径
            output_path: 输出 .md 文件的目标路径

        返回:
            True 表示转换成功，False 表示失败
        """
        try:
            logger.info(f"转换中: {pdf_path}")
            start_time = time.time()

            # 调用 Docling 转换
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                result = self.converter.convert(pdf_path)

            # 部分失败显式化：Docling 会把页级 OCR/解析失败吞进 result.status，
            # 若不检查就会写出缺页/空 Markdown 并误报成功。这里转成异常，
            # 交由 robust_agent 分类/记录/跳过。
            status = str(getattr(result, "status", "") or "")
            if status in ("failure", "partial_success"):
                n_err = len(getattr(result, "errors", []) or [])
                raise RuntimeError(f"Docling 转换 {status}（errors={n_err}）")

            # 导出为 Markdown
            try:
                markdown_text = result.document.export_to_markdown(
                    page_break_placeholder="\n\n[[PAGE_BREAK]]\n\n")
            except TypeError:
                # Older Docling releases do not expose page_break_placeholder.
                markdown_text = result.document.export_to_markdown()

            elapsed = time.time() - start_time

            # 确保输出目录存在
            out_dir = os.path.dirname(output_path)
            if out_dir:
                os.makedirs(out_dir, exist_ok=True)

            with open(output_path, "w", encoding="utf-8") as f:
                f.write(markdown_text)

            file_size = os.path.getsize(output_path)
            logger.info(
                f"完成: {output_path} "
                f"({len(markdown_text)} 字符, {file_size} 字节, "
                f"耗时 {elapsed:.1f}s)"
            )
            return True

        except Exception as e:
            batch_root = os.path.dirname(os.path.dirname(os.path.abspath(output_path)))
            decision = handle_exception(
                e, stage="convert", operation=os.path.basename(pdf_path),
                batch_root=batch_root,
            )
            if decision is Decision.DEGRADE:
                logger.warning(
                    f"{pdf_path} 触发资源降级（{type(e).__name__}）："
                    "本机内存可能吃紧，建议缩小 OCR 页批/关闭表格识别后重试。")
            logger.error(f"失败: {pdf_path} — {type(e).__name__}: {e}")
            return False


# ---------------------------------------------------------------------------
# 子进程隔离：把 Docling PDF 转换放进持久 worker 子进程，隔离 C++ 级 OOM。
#
# 背景：onnxruntime 在 ConvTranspose 上采样时若分配失败会抛 std::bad_alloc，一旦
# 发生在未被 Python 包装的位置，会触发 std::terminate/abort() 直接杀死整个 Python
# 进程——绕过 convert_single 的 try/except，日志里既无"失败"也无"完成"、更无
# traceback（即此前观察到的"进程再次中断"）。把它放进子进程后，崩的只是 worker，
# 父进程识别崩溃/超时后跳过该篇并重启 worker，整批转换得以继续。
# ---------------------------------------------------------------------------

_MODULE = "litdiscovery.agent.extractor_agent_pipeline.preprocess.convert"

_TIMEOUT = object()  # _readline 超时哨兵（区别于 None=EOF）


def _worker_main(enable_table_structure: bool = True) -> None:
    """worker 子进程入口：stdin 逐行收 JSON 请求，逐篇转换，stdout 逐行回 JSON。

    协议（一行一 JSON，UTF-8，真实 stdout 只承载协议）：
        请求  {"pdf": str, "out": str}
        响应  {"status": "ok"|"fail", "pdf": str, "error": str}

    复用同一个 PDF2MarkdownConverter（Docling 模型只加载一次），连续多篇不重复
    初始化。Python 层异常由 convert_single 捕获并回 "fail"；C++ 级 abort 会直接
    杀死本进程——父进程据此判定"崩溃"。
    """
    # 协议只走真实 stdout；把 logger 的控制台 handler 改道 stderr，并让所有 print
    # （含 robust_agent.record_and_report 的 [Robust] 反馈）也落 stderr，避免污染协议流。
    real_stdout = sys.stdout
    sys.stdout = sys.stderr
    for h in list(logger.handlers):
        if isinstance(h, logging.StreamHandler) and getattr(h, "stream", None) is real_stdout:
            logger.removeHandler(h)
            logger.addHandler(logging.StreamHandler(sys.stderr))

    converter = PDF2MarkdownConverter(enable_table_structure)
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
        except json.JSONDecodeError:
            continue
        pdf_path = req.get("pdf", "")
        out_path = req.get("out", "")
        try:
            ok = converter.convert_single(pdf_path, out_path)
            resp = {"status": "ok" if ok else "fail", "pdf": pdf_path,
                    "error": "" if ok else f"{os.path.basename(pdf_path)} 转换失败"}
        except Exception as e:  # convert_single 内部已兜底，这里再兜一层防协议中断
            resp = {"status": "fail", "pdf": pdf_path,
                    "error": f"{type(e).__name__}: {e}"}
        real_stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
        real_stdout.flush()


class _WorkerSupervisor:
    """持久 worker 子进程监督器：PDF 转换隔离到子进程 + 看门狗超时。"""

    def __init__(self, enable_table_structure: bool = True):
        self._enable_table = enable_table_structure
        self._proc = None
        self._lines = None        # queue.Queue：读线程 → 主线程 的 stdout 行
        self._stderr_tail = None  # 最近若干行 stderr（崩溃/超时诊断用）

    def _spawn(self) -> None:
        proc = subprocess.Popen(
            [sys.executable, "-u", "-m", _MODULE, "--worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", bufsize=1,
        )
        self._proc = proc
        lines = queue.Queue()
        self._lines = lines
        stderr_tail = collections.deque(maxlen=200)
        self._stderr_tail = stderr_tail

        def drain_stdout():
            for line in proc.stdout:
                lines.put(line)
            lines.put(None)  # EOF 哨兵

        def drain_stderr():
            for line in proc.stderr:
                stderr_tail.append(line.rstrip("\n"))

        threading.Thread(target=drain_stdout, daemon=True).start()
        threading.Thread(target=drain_stderr, daemon=True).start()

    @property
    def stderr_snippet(self) -> str:
        tail = list(self._stderr_tail or [])
        return "\n".join(tail[-40:])

    def _terminate(self) -> None:
        try:
            self._proc.kill()
        except Exception:
            pass

    def convert(self, pdf_path: str, out_path: str):
        """转换一篇 PDF。返回 (ok, err, fault)：
            - ok: 是否成功；
            - err: 失败描述（成功时为 None）；
            - fault: 失败是否发生在 worker 之外（崩溃/超时/协议异常）。fault=True 时
              父进程需自己补记"失败"日志与 robust 事件（worker 已无法记录）。
        """
        if self._proc is None or self._proc.poll() is not None:
            self._spawn()
        req = json.dumps({"pdf": pdf_path, "out": out_path}, ensure_ascii=False)
        try:
            self._proc.stdin.write(req + "\n")
            self._proc.stdin.flush()
        except Exception:
            # worker 已死（写 stdin 失败）：重启后重发一次
            self._terminate()
            self._spawn()
            self._proc.stdin.write(req + "\n")
            self._proc.stdin.flush()

        line = self._readline()
        if line is _TIMEOUT:
            self._terminate()
            return (False,
                    f"转换超时（看门狗 {CONVERT_WORKER_TIMEOUT}s，疑似 OCR 内存耗尽卡死）",
                    True)
        if line is None:
            self._terminate()
            return (False, "worker 进程崩溃（疑似 OCR OOM 触发 onnxruntime C++ abort）", True)
        try:
            resp = json.loads(line)
        except json.JSONDecodeError:
            return (False, f"worker 协议异常: {line[:200]!r}", True)
        ok = resp.get("status") == "ok"
        err = "" if ok else (resp.get("error") or "未知失败")
        return ok, (err or None), False

    def _readline(self):
        try:
            return self._lines.get(timeout=CONVERT_WORKER_TIMEOUT)
        except queue.Empty:
            return _TIMEOUT


# 非 PDF 格式转换（xml / txt / tex → markdown）
def convert_xml_to_md(xml_path: str, output_path: str) -> bool:
    """XML 全文（Elsevier/NXML）→ Markdown。复用 download/fulltext.xml_to_markdown。"""
    try:
        from litdiscovery.agent.filter_agent_pipeline.fulltext import xml_to_markdown
        with open(xml_path, "r", encoding="utf-8") as f:
            xml_text = f.read()
        md, status = xml_to_markdown(xml_text)
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(md)
        logger.info(f"完成(XML→md): {output_path} ({len(md)} 字符, status={status})")
        return True
    except Exception as e:
        logger.error(f"失败(XML): {xml_path} — {e}")
        return False


def convert_txt_to_md(txt_path: str, output_path: str) -> bool:
    """TXT 纯文本 → Markdown（复制文本，包装标题）。"""
    try:
        with open(txt_path, "r", encoding="utf-8") as f:
            text = f.read()
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(text)
        logger.info(f"完成(TXT→md): {output_path} ({len(text)} 字符)")
        return True
    except Exception as e:
        logger.error(f"失败(TXT): {txt_path} — {e}")
        return False


def convert_tex_to_md(tex_path: str, output_path: str) -> bool:
    """LaTeX 源码 → Markdown。复用 download/fulltext.latex_to_markdown。"""
    try:
        from litdiscovery.agent.filter_agent_pipeline.fulltext import latex_to_markdown
        with open(tex_path, "r", encoding="utf-8") as f:
            tex = f.read()
        md = latex_to_markdown(tex)
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(md)
        logger.info(f"完成(TEX→md): {output_path} ({len(md)} 字符)")
        return True
    except Exception as e:
        logger.error(f"失败(TEX): {tex_path} — {e}")
        return False


# 扩展名 → 转换器映射
_EXT_CONVERTERS = {
    ".pdf": "pdf",
    ".xml": "xml",
    ".txt": "txt",
    ".tex": "tex",
}


def _converter_for(path: str):
    """按文件扩展名选择转换函数。返回 (converter_callable, display_name) 或 (None, None)。"""
    ext = os.path.splitext(path)[1].lower()
    kind = _EXT_CONVERTERS.get(ext)
    if kind == "pdf":
        return "pdf", "PDF"
    if kind == "xml":
        return "xml", "XML"
    if kind == "txt":
        return "txt", "TXT"
    if kind == "tex":
        return "tex", "TEX"
    return None, None


# 主入口
def main():
    parser = argparse.ArgumentParser(
        description="基于 IBM Docling将PDF转换为 Markdown",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    # 默认: 转换最新批次根 pdfs/ 中的 PDF
    python -m litdiscovery.agent.extractor_agent_pipeline.preprocess.convert

    # 指定输入路径
    python -m litdiscovery.agent.extractor_agent_pipeline.preprocess.convert -i ./pdf_folder

    # 指定输出目录
    python -m litdiscovery.agent.extractor_agent_pipeline.preprocess.convert -i ./pdf_folder -o ./md_output

    # 使用 HF 镜像下载模型
    python -m litdiscovery.agent.extractor_agent_pipeline.preprocess.convert --hf-mirror
        """,
    )

    parser.add_argument(
        "-i", "--input",
        default=None,
        help="输入文件或目录路径（默认: 最新批次根的最小一级格式目录 pdfs/xmls/txts/texs）",
    )
    parser.add_argument(
        "-o", "--output",
        default=None,
        help="输出 Markdown 文件目录（默认: 与 pdfs 同级的 markdowns/ 文件夹）",
    )
    parser.add_argument(
        "-r", "--recursive",
        action="store_true",
        help="递归处理子目录中的 PDF 文件，并在输出中保留目录结构",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="跳过输出文件已存在的情况（增量转换）",
    )
    parser.add_argument(
        "--hf-mirror",
        action="store_true",
        help="使用 hf-mirror.com 国内镜像下载 HuggingFace 模型",
    )
    parser.add_argument(
        "--hf-mirror-url",
        default="https://hf-mirror.com",
        help="自定义 HF 镜像地址（默认: https://hf-mirror.com）",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="输出详细日志（DEBUG 级别）",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help=f"日志文件路径（默认: {LOG_FILE}）",
    )

    # 无参数时打印帮助并退出
    if len(sys.argv) == 1:
        parser.print_help()
        print("\n" + "─" * 50)
        print("提示: 也可直接回车，在下方交互式输入路径：")
        print("─" * 50)

    args = parser.parse_args()

    # ---- 配置 HuggingFace 镜像（必须在导入 docling 之前） ----
    setup_hf_mirror(use_mirror=args.hf_mirror, mirror_url=args.hf_mirror_url)

    # ---- 确定输入来源 ----
    if args.input:
        # 自定义输入（文件或目录，按扩展名分发）
        input_path = args.input
        input_abs = os.path.abspath(input_path)
        logger.info(f"使用自定义输入路径: {input_abs}")
        src_paths = []
        if os.path.isfile(input_abs):
            src_paths = [input_abs]
        elif os.path.isdir(input_abs):
            if args.recursive:
                for root, dirs, files in os.walk(input_abs):
                    dirs.sort()
                    for fname in files:
                        if _converter_for(fname)[0]:
                            src_paths.append(os.path.join(root, fname))
            else:
                for fname in sorted(os.listdir(input_abs)):
                    if _converter_for(fname)[0]:
                        src_paths.append(os.path.join(input_abs, fname))
        else:
            logger.error(f"路径不存在: {input_path}")
            sys.exit(1)
        # 自定义输入输出目录（pdfs/ 等格式目录 → 同级 markdowns/，其余 → 输入下 markdowns/）
        out_dir = os.path.abspath(args.output) if args.output else get_default_output_dir(input_abs)
    else:
        # 默认: 最新批次根下 最小一级格式目录 pdfs/xmls/txts/texs，
        # 只处理各格式目录内的原文文件，统一输出到与它们同级的 markdowns/
        try:
            latest_folder = latest_batch(require_end_mds=False)
        except FileNotFoundError as e:
            logger.error(str(e))
            logger.info("请通过 -i/--input 指定输入路径。")
            sys.exit(1)
        format_dirs = [os.path.join(latest_folder, sub) for sub in FORMAT_SUBDIRS
                       if os.path.isdir(os.path.join(latest_folder, sub))]
        logger.info(f"默认输入: 最新批次最小一级格式目录 "
                    + ", ".join(os.path.basename(d) for d in format_dirs))
        src_paths = []
        for fmt_dir in format_dirs:
            for fname in sorted(os.listdir(fmt_dir)):
                if _converter_for(fname)[0]:
                    src_paths.append(os.path.join(fmt_dir, fname))
        # 与格式目录同级统一输出 markdowns/
        out_dir = os.path.abspath(args.output) if args.output else os.path.join(latest_folder, "markdowns")

    logger.info(f"输出目录: {out_dir}")
    os.makedirs(out_dir, exist_ok=True)

    # 若指定了自定义日志文件，追加一个额外的 FileHandler
    if args.log_file:
        custom_handler = logging.FileHandler(args.log_file, encoding="utf-8")
        custom_handler.setLevel(logging.DEBUG)
        custom_handler.setFormatter(formatter)
        logger.addHandler(custom_handler)
        logger.info(f"日志文件: {args.log_file}")

    if args.verbose:
        logger.setLevel(logging.DEBUG)
        for h in logger.handlers:
            h.setLevel(logging.DEBUG)

    if not src_paths:
        logger.warning("未找到任何需要转换的文献文件（pdf/xml/txt/tex）")
        sys.exit(0)

    # 分类统计
    from collections import Counter
    kind_counts = Counter(_converter_for(p)[0] for p in src_paths)
    logger.info(f"找到 {len(src_paths)} 个文献文件待转换: "
                + ", ".join(f"{k}={n}" for k, n in kind_counts.items()))

    # ---- 逐个转换 ----
    # PDF 走子进程隔离（Docling/ONNX 的 C++ 级 OOM 会硬杀进程，见 _WorkerSupervisor），
    # xml/txt/tex 无需模型，仍在进程内转换。
    supervisor = _WorkerSupervisor()  # 持久 worker：Docling 模型只加载一次
    batch_root = os.path.dirname(os.path.abspath(out_dir))
    success_count = 0
    fail_count = 0

    for i, src_path in enumerate(src_paths, 1):
        logger.info(f"[{i}/{len(src_paths)}] {os.path.basename(src_path)}")

        # 确定输出路径（文件名保持一致，仅替换扩展名为 .md）
        # 默认分支（最小一级格式目录）统一平铺输出到 markdowns/，不做相对结构
        if args.recursive and args.input and os.path.isdir(input_abs):
            rel_path = os.path.relpath(os.path.dirname(src_path), input_abs)
            out_subdir = os.path.join(out_dir, rel_path)
        else:
            out_subdir = out_dir
        out_name = os.path.splitext(os.path.basename(src_path))[0] + ".md"
        out_path = os.path.join(out_subdir, out_name)

        # 如果该输出文件已存在且启用了 skip-existing，则跳过
        if args.skip_existing and os.path.exists(out_path):
            logger.info(f"跳过（已存在）: {out_path}")
            continue

        # 超大源文件保护：文件内存过大时直接跳过，避免 Docling 转换期内存/时间爆炸
        try:
            src_size = os.path.getsize(src_path)
        except OSError:
            src_size = 0
        if MAX_CONVERT_SRC_BYTES and src_size > MAX_CONVERT_SRC_BYTES:
            logger.warning(
                f"跳过（源文件过大 {src_size / 1024 / 1024:.1f} MB > "
                f"MAX_CONVERT_SRC_BYTES={MAX_CONVERT_SRC_BYTES / 1024 / 1024:.0f} MB）: {src_path}"
            )
            continue

        kind, _label = _converter_for(src_path)
        if kind == "pdf":
            ok, err, fault = supervisor.convert(src_path, out_path)
            if ok:
                success_count += 1
                continue
            fail_count += 1
            if fault:
                # worker 崩溃/超时/协议异常：worker 已无法自记录，父进程补记日志 + robust 事件
                logger.error(f"失败: {src_path} — {err}")
                tail = supervisor.stderr_snippet
                if tail:
                    logger.warning(f"[Worker stderr] {os.path.basename(src_path)}:\n{tail}")
                handle_exception(MemoryError(err), stage="convert",
                                 operation=os.path.basename(src_path),
                                 batch_root=batch_root)
            # fault=False 时 worker 已在 convert_single 内记录"失败"与 robust 事件
            continue
        if kind == "xml":
            ok = convert_xml_to_md(src_path, out_path)
        elif kind == "txt":
            ok = convert_txt_to_md(src_path, out_path)
        elif kind == "tex":
            ok = convert_tex_to_md(src_path, out_path)
        else:
            logger.warning(f"不支持格式，跳过: {src_path}")
            continue

        if ok:
            success_count += 1
        else:
            fail_count += 1

    # ---- 汇总 ----
    logger.info("=" * 50)
    logger.info(
        f"转换完成 — 成功: {success_count}, 失败: {fail_count}, "
        f"总计: {len(src_paths)}"
    )

    if fail_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    # 子进程 worker 模式：不进入 argparse，直接跑 stdin/stdout 协议循环。
    # 由 _WorkerSupervisor 以 `python -m <module> --worker` 拉起。
    if "--worker" in sys.argv[1:]:
        _worker_main(enable_table_structure=True)
        sys.exit(0)
    main()
