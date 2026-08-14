"""
litdiscovery/agent/filter_agent_pipeline/fulltext.py — 合法 OA 全文与原始格式获取。

获取阶段只写入批次的源文件目录：Markdown 写入 markdowns/，XML/TXT/TeX
分别写入对应格式目录。end_mds/ 仅由 extractor_agent_pipeline.preprocess 生成。
按序尝试（每篇只命中第一个成功源）：

1. arXiv LaTeX    —— DOI 为 10.48550/arXiv.xxx → e-print 源码 tar → LaTeX→markdown（表格最稳）
2. Elsevier XML   —— ScienceDirect Article Retrieval API（SciVerse 平台，需机构订阅/TDM 权限，
                     未授权只返回摘要 → 标记 abstract-only）
3. CORE 全文      —— 已有 CORE_API_KEY，OA 论文直接返回全文文本
4. Europe PMC     —— 免费 REST API，fullTextXML → markdown
"""

import io
import gzip
import os
import re
import tarfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

from litdiscovery.config import (
    ELSEVIER_API_KEY,
    CORE_API_KEY,
    DOWNLOAD_USER_AGENT,
    MIN_FULLTEXT_BYTES,
    TOO_SMALL_FULLTEXT_CHARS,
)
from litdiscovery.common.net import _get

HEADERS = {"User-Agent": DOWNLOAD_USER_AGENT}


def _safe_name(doi: str) -> str:
    """DOI → 安全文件夹名（统一委托 common.fs.safe_folder_name）。

    与 data_preprocessing.md_parser 的命名一致（去非词字符），
    使 download 建的 end_mds/<folder> 与数据预处理生成的 folder 名对齐，
    避免同名 DOI 因规范化差异产生两个目录（如括号 (01) 的 DOI）。
    """
    from litdiscovery.common.fs import safe_folder_name
    return safe_folder_name(doi)


def _format_subdir(ext: str) -> str:
    """按扩展名映射到与 pdfs/ 同级的格式目录名。"""
    return {".xml": "xmls", ".txt": "txts", ".tex": "texs"}.get(ext, "others")


# ============================================================
# 转换器
# ============================================================
def _localname(tag: str) -> str:
    """去掉 XML 命名空间前缀，取标签本地名。"""
    return tag.rsplit("}", 1)[-1]


def _render_table_xml(elem) -> str:
    """把 XML 表格元素（ce:table / NXML table）转为 markdown 管道表。"""
    rows = []
    for row in elem.iter():
        ln = _localname(row.tag)
        if ln in ("row", "ce:row", "tr"):
            cells = []
            for cell in row:
                cln = _localname(cell.tag)
                # 兼容 Elsevier ce:cell / NXML th,td
                if (cln in ("cell", "ce:cell") or cln.endswith("cell")
                        or cln in ("th", "td")):
                    txt = "".join(cell.itertext()).strip()
                    cells.append(txt.replace("\n", " ").replace("|", "\\|"))
            if cells:
                rows.append(cells)
    if not rows:
        # 退化为纯文本
        return "\n" + "".join(elem.itertext()).strip() + "\n"
    out = ["\n", "| " + " | ".join(rows[0]) + " |",
           "| " + " | ".join(["---"] * len(rows[0])) + " |"]
    for r in rows[1:]:
        while len(r) < len(rows[0]):
            r.append("")
        out.append("| " + " | ".join(r[:len(rows[0])]) + " |")
    out.append("\n")
    return "\n".join(out)


# 文本收集：保留嵌套子元素的文本（公式/引用编号/空格等），避免丢失
def _elem_text(elem) -> str:
    """取元素全部后代文本（itertext），清理空白后返回。"""
    return "".join(elem.itertext()).strip()


# 内联/公式元素：内容应保留为文本而非被递归输出独立段落
_INLINE_NAMES = {"ce:hsp", "mml:math", "ce:inline-formula", "ce:formula",
                 "ce:label", "ce:cross-ref", "ce:cross-refs", "ce:qid",
                 "ce:inf", "ce:sup", "ce:sub", "ce:inter-ref", "ce:cite",
                 "ce:display"}


def _xml_walk(elem, lines):
    """递归遍历 XML，按语义标签输出 markdown 结构。"""
    name = _localname(elem.tag)
    txt = _elem_text(elem)
    if name in ("section-title", "section_title", "ce:section-title"):
        lines.append(f"\n## {txt}\n" if txt else "")
        return
    if name in ("abstract", "abstract-sec", "abstractsec"):
        if txt:
            lines.append(f"\n## Abstract\n{txt}\n")
        return
    if name in ("para", "p", "ce:para", "simple-para", "ce:simple-para"):
        if txt:
            lines.append(f"\n{txt}\n")
        return
    if name in ("title", "article-title", "dc:title", "ce:title"):
        if txt:
            lines.append(f"\n# {txt}\n")
        return
    if name in ("fig", "figure", "ce:fig", "ce:figure"):
        # 图注转 markdown：<figure-title> / <caption> 文本
        cap = _elem_text(elem)
        if cap:
            lines.append(f"\n![figure]({cap})\n")
        return
    if name in ("caption", "ce:caption", "table-caption"):
        # 图/表标题行
        cap = _elem_text(elem)
        if cap:
            lines.append(f"\n**{cap}**\n")
        return
    if name in ("table", "ce:table", "table-wrap", "table-frame", "ce:table-frame"):
        lines.append(_render_table_xml(elem))
        return
    if name in _INLINE_NAMES:
        # 内联元素（公式/引用编号）：递归其子元素的文本，不再独立成段
        inner = "".join(child.tail or "" for child in elem) or ""
        if txt:
            lines.append(txt)
        if inner.strip():
            lines.append(inner.strip())
        return
    for child in list(elem):
        _xml_walk(child, lines)
        if child.tail and child.tail.strip():
            lines.append(child.tail.strip())


def xml_to_markdown(xml_text: str):
    """任意学术 XML（Elsevier/NXML）→ (markdown 文本, status)。

    status ∈ {ok, abstract-only, metadata-only, degraded}。
    - abstract-only：源 XML 无正文（Elsevier API 未授权全文），此时保留标题+摘要；
    - metadata-only：既无正文也无摘要（纯书目记录，如 struct-bib 参考条目）；
    - degraded：解析失败，降级为标签剥离。
    """
    try:
        root = ET.fromstring(xml_text)
        lines = []
        _xml_walk(root, lines)

        # 摘要在 dc:description（Elsevier 元数据-only 响应）——若正文缺失则输出它，
        # 避免"只有标题、摘要也丢"（apmt 类残篇）。
        # 判定标准：有无真实正文段落（para / simple-para 数量），而非标签 ce:body。
        para_count = sum(1 for e in root.iter()
                         if _localname(e.tag) in ("para", "ce:para", "simple-para"))
        if para_count == 0:
            desc = _elem_text(root.find(".//{*}description"))
            if desc:
                lines.append(f"\n## Abstract\n{desc}\n")

        md = "\n".join(l for l in lines if l)
        if para_count > 0:
            status = "ok"
        elif _elem_text(root.find(".//{*}description")):
            status = "abstract-only"
        else:
            status = "metadata-only"
        return md, status
    except Exception:
        # 兜底：剥离全部标签，保留文本
        text = re.sub(r"<[^>]+>", " ", xml_text)
        text = re.sub(r"\s+", " ", text).strip()
        return text, "degraded"


def latex_to_markdown(tex: str) -> str:
    """基础 LaTeX → markdown 转换（面向 LLM 全文提取的够用版本）。"""
    s = tex
    # 只保留正文（\begin{document} 之后）
    m = re.search(r"\\begin\{document\}(.*?)\\end\{document\}", s, re.DOTALL)
    if m:
        s = m.group(1)
    # 移除表格环境内的 LaTeX 命令，转换为 markdown 表（先做，再清命令）
    def _table_repl(match):
        body = match.group(1)
        rows = []
        for line in body.split("\\\\"):
            if "hline" in line or not line.strip():
                continue
            cells = [c.strip() for c in line.replace("\\hline", "").split("&")]
            rows.append(cells)
        if not rows:
            return ""
        out = ["", "| " + " | ".join(rows[0]) + " |",
               "| " + " | ".join(["---"] * len(rows[0])) + " |"]
        for r in rows[1:]:
            while len(r) < len(rows[0]):
                r.append("")
            out.append("| " + " | ".join(r[:len(rows[0])]) + " |")
        out.append("")
        return "\n".join(out)

    s = re.sub(r"\\begin\{tabular\}.*?\n(.*?)\\end\{tabular\}", _table_repl, s, flags=re.DOTALL)

    # 章节标题
    s = re.sub(r"\\section\{([^}]*)\}", r"\n## \1\n", s)
    s = re.sub(r"\\subsection\{([^}]*)\}", r"\n### \1\n", s)
    s = re.sub(r"\\subsubsection\{([^}]*)\}", r"\n#### \1\n", s)
    s = re.sub(r"\\paragraph\{([^}]*)\}", r"\n**\1**\n", s)
    # 强调
    s = re.sub(r"\\textbf\{([^}]*)\}", r"**\1**", s)
    s = re.sub(r"\\textit\{([^}]*)\}", r"*\1*", s)
    # 引用/公式标记清理
    s = re.sub(r"\\cite\{[^}]*\}", "", s)
    s = re.sub(r"\\ref\{[^}]*\}", "", s)
    s = re.sub(r"\\label\{[^}]*\}", "", s)
    s = re.sub(r"\$([^$]*)\$", r"\1", s)
    # 常见命令
    s = s.replace("\\&", "&").replace("\\%", "%").replace("\\_", "_")
    s = re.sub(r"\\\(|\\\)", "", s)
    s = re.sub(r"\\item\s*", "\n- ", s)
    # 重音/特殊符号命令（\'{e} → e, \vspace{} → 删除）
    s = re.sub(r"\\[^a-zA-Z0-9 ](?:\{([^{}]*)\})?", r"\1", s)
    s = re.sub(r"\\(?:v|h)space\*?\{[^}]*\}", "", s)
    # 残留 LaTeX 命令删除（\alpha 等保留为英文，其余命令删掉）
    s = re.sub(r"\\[a-zA-Z]+\{([^}]*)\}", r"\1", s)
    s = re.sub(r"\\(?:mathrm|text)\{([^}]*)\}", r"\1", s)
    s = re.sub(r"\\[a-zA-Z]+\s*", "", s)
    # 多余空行压缩
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


# ============================================================
# 各全文源
# ============================================================
def _arxiv_id_from_doi(doi: str):
    """从 DOI 提取 arXiv id；非 arXiv DOI 返回 None。"""
    s = doi.lower()
    if "arxiv" not in s and not s.startswith("10.48550/"):
        return None
    m = re.search(r"arxiv[.:/]?(\d{4}\.\d{4,5}(?:v\d+)?)", s)
    return m.group(1) if m else None


def _extract_tex_from_eprint(content: bytes):
    """从 arXiv e-print 响应字节提取主 .tex 源码（tar.gz 或 gzip 单文件）。"""
    # gzip 单文件源码（.tex.gz 或直接压缩的 tex）
    if content[:2] == b"\x1f\x8b":
        try:
            raw = gzip.decompress(content)
            if raw.lstrip().startswith((b"\\documentclass", b"\\input", b"%", b"\\")):
                return raw.decode("utf-8", errors="replace")
        except Exception:
            pass
    # tar 归档（可能含 tex + 图片）
    try:
        tf = tarfile.open(fileobj=io.BytesIO(content), mode="r:*")
        members = [m for m in tf.getmembers() if m.name.endswith(".tex") and m.isfile()]
        if not members:
            return None
        best, best_size = None, 0
        for m in members:
            try:
                data = tf.extractfile(m).read().decode("utf-8", errors="replace")
            except Exception:
                continue
            if "\\documentclass" in data:
                return data
            if m.size > best_size:
                best, best_size = data, m.size
        return best
    except Exception:
        return None


def _fetch_arxiv(doi: str):
    """arXiv LaTeX 全文。返回 dict 或 None。"""
    aid = _arxiv_id_from_doi(doi)
    if not aid:
        return None
    try:
        resp = _get(f"https://arxiv.org/e-print/{aid}", headers=HEADERS, timeout=120)
        if resp.status_code != 200:
            print(f"      [Fulltext/arXiv] e-print 返回 {resp.status_code}")
            return None
        tex = _extract_tex_from_eprint(resp.content)
        if not tex:
            return None
        md = latex_to_markdown(tex)
        if len(md) < 200:
            return None
        print(f"      [Fulltext/arXiv] 成功 ({len(md)} 字符)")
        return {"source": "arxiv_latex", "format": "tex", "text": md,
                "raw": tex, "raw_ext": ".tex", "status": "ok"}
    except Exception as e:
        print(f"      [Fulltext/arXiv] 失败: {type(e).__name__}: {e}")
        return None


def _fetch_elsevier(doi: str):
    """Elsevier ScienceDirect Article Retrieval XML（SciVerse 平台）。"""
    key = (os.environ.get("ELSEVIER_API_KEY") or ELSEVIER_API_KEY or "").strip()
    if not key or not doi.lower().startswith("10.1016/"):
        return None
    try:
        resp = _get(f"https://api.elsevier.com/content/article/doi/{doi}",
                    headers={"X-ELS-APIKey": key, "Accept": "application/xml", **HEADERS},
                    timeout=60)
        if resp.status_code != 200:
            print(f"      [Fulltext/Elsevier] API 返回 {resp.status_code}")
            return None
        md, status = xml_to_markdown(resp.text)
        if len(md) < 100:
            return None
        print(f"      [Fulltext/Elsevier] 成功 ({status}, {len(md)} 字符)")
        return {"source": "elsevier_xml", "format": "xml", "text": md,
                "raw": resp.text, "raw_ext": ".xml", "status": status}
    except Exception as e:
        print(f"      [Fulltext/Elsevier] 失败: {type(e).__name__}: {e}")
        return None


def _fetch_core(doi: str):
    """CORE 全文（OA 聚合库，已有 key）。"""
    key = (os.environ.get("CORE_API_KEY") or CORE_API_KEY or "").strip()
    if not key:
        return None
    try:
        import requests as _rq
        resp = _rq.post("https://api.core.ac.uk/v3/search/works",
                        headers={"Authorization": f"Bearer {key}",
                                 "Content-Type": "application/json", **HEADERS},
                        json={"q": f'doi:"{doi}"', "limit": 3}, timeout=60)
        if resp.status_code != 200:
            print(f"      [Fulltext/CORE] API 返回 {resp.status_code}")
            return None
        results = (resp.json() or {}).get("results", [])
        if not results:
            return None
        r0 = results[0]
        fulltext = r0.get("fulltext")
        if fulltext and len(str(fulltext)) > 200:
            print(f"      [Fulltext/CORE] 成功 ({len(str(fulltext))} 字符)")
            return {"source": "core", "format": "text",
                    "text": str(fulltext), "raw": str(fulltext), "raw_ext": ".txt",
                    "status": "ok"}
        print(f"      [Fulltext/CORE] 仅元数据，无全文（downloadUrl={r0.get('downloadUrl') or ''}）")
        return None
    except Exception as e:
        print(f"      [Fulltext/CORE] 失败: {type(e).__name__}: {e}")
        return None


def _fetch_europepmc(doi: str):
    """Europe PMC fullTextXML（免费）。"""
    try:
        resp = _get("https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                    params={"query": f'DOI:"{doi}"', "resultType": "core", "format": "json"},
                    timeout=60)
        if resp.status_code != 200:
            return None
        results = (resp.json() or {}).get("resultList", {}).get("result", [])
        if not results:
            return None
        r0 = results[0]
        src, pid = r0.get("source"), r0.get("id")
        fxt = (r0.get("fullTextXmlList") or {}).get("fullTextXml", [])
        if not (src and pid and fxt):
            return None
        xml_resp = _get(f"https://www.ebi.ac.uk/europepmc/webservices/rest/{src}/{pid}/fullTextXML",
                        timeout=60)
        if xml_resp.status_code != 200:
            return None
        md, status = xml_to_markdown(xml_resp.text)
        if len(md) < 100:
            return None
        print(f"      [Fulltext/EuropePMC] 成功 ({status}, {len(md)} 字符)")
        return {"source": "europepmc", "format": "xml", "text": md,
                "raw": xml_resp.text, "raw_ext": ".xml", "status": status}
    except Exception as e:
        print(f"      [Fulltext/EuropePMC] 失败: {type(e).__name__}: {e}")
        return None


# ============================================================
# 主入口
# ============================================================
def _fallback_pdf(doi: str, format_root, audit=None, paper=None) -> Optional[Path]:
    """PDF 兜底：只下载 PDF 到 <批次根>/pdfs/。

    PDF 转换和清洗由批次的 preprocess 阶段统一执行。延迟 import 避免
    fulltext↔pdf_fetch 循环依赖。
    """
    from litdiscovery.agent.filter_agent_pipeline.pdf_fetch import download_pdf_by_doi, PDF_OUTPUT_SUBDIR
    root = Path(format_root) if format_root else None
    pdf_dir = root / PDF_OUTPUT_SUBDIR if root else None
    if pdf_dir is None:
        return None
    try:
        return download_pdf_by_doi(doi, pdf_dir, audit=audit, paper=paper)
    except Exception as exc:
        print(f"      [Fulltext] PDF fallback failed: {type(exc).__name__}: {exc}")
        return None


def _call_pdf_fallback(doi: str, root, audit=None, paper=None) -> Optional[Path]:
    """Keep compatibility with tests/extensions that replace the two-arg fallback."""
    if audit is None and paper is None:
        return _fallback_pdf(doi, root)
    return _fallback_pdf(doi, root, audit=audit, paper=paper)


def fetch_fulltext_by_doi(doi: str, out_dir, format_root: str = None, audit=None,
                          paper=None) -> dict:
    """按 DOI 获取全文源文件，Markdown 统一写入 ``markdowns/``。

    ``out_dir`` 保留用于兼容调用方并用于检查已处理的 end_mds；
    ``format_root`` 应为批次根目录，缺省时从 out_dir 推断。

    返回: {doi, source, format, status, path, raw_path}；
    失败时 source/format 为 None, status="failed"。
    """
    doi = doi.strip()
    out_dir = Path(out_dir)
    root = Path(format_root) if format_root else (
        out_dir.parent if out_dir.name == "end_mds" else out_dir
    )
    safe_doi = _safe_name(doi)
    processed_path = out_dir / safe_doi / "fulltext.md"
    markdown_dir = root / "markdowns"
    md_path = markdown_dir / f"{safe_doi}.md"
    source_marker = markdown_dir / f"{safe_doi}.too_small"

    if processed_path.exists():
        print(f"[Fulltext] 已完成预处理，跳过: {processed_path}")
        return {"doi": doi, "source": "cached", "format": "markdown",
                "status": "too_small" if (processed_path.parent / ".too_small").exists() else "ok",
                "path": str(processed_path)}
    if md_path.exists() and not source_marker.exists():
        print(f"[Fulltext] Markdown 源文件已存在，等待预处理: {md_path}")
        return {"doi": doi, "source": "cached", "format": "markdown",
                "status": "too_small" if source_marker.exists() else "ok",
                "path": str(md_path)}

    provider_attempts = []
    result = None
    for provider, fetcher in (("arxiv", _fetch_arxiv), ("elsevier_sciverse", _fetch_elsevier),
                              ("core", _fetch_core), ("europepmc", _fetch_europepmc)):
        print(f"      [Fulltext] 尝试 {provider} ...")
        try:
            candidate = fetcher(doi)
            provider_attempts.append({"provider": provider,
                                      "status": "success" if candidate else "not_found"})
        except Exception as exc:
            candidate = None
            provider_attempts.append({"provider": provider, "status": "error",
                                      "error": f"{type(exc).__name__}: {exc}"})
        if candidate:
            result = candidate
            break
    if not result or not result.get("text"):
        # 无全文源。降级到 PDF 下载（pdfs/<doi>.pdf），再转 markdown。
        pdf_path = _call_pdf_fallback(doi, root, audit=audit, paper=paper)
        if pdf_path:
            return {"doi": doi, "source": "pdf_fallback", "format": "pdf",
                    "status": "ok", "path": str(pdf_path), "raw_path": str(pdf_path)}
        print(f"[Fulltext] 未获取到全文: {doi}（全文链 + PDF 兜底均失败）")
        return {"doi": doi, "source": None, "format": None, "status": "failed", "path": None,
                "provider_attempts": provider_attempts}

    # Elsevier 无正文（abstract-only 有摘要 / metadata-only 连摘要都没有，如 struct-bib
    # 纯书目记录）——不落盘为"全文"，改走 PDF 兜底；PDF 也失败时：
    #   abstract-only  保留摘要（比只有标题好）
    #   metadata-only  标记降级（无内容可留，交由下游跳过/补 PDF）
    if result.get("status") in ("abstract-only", "metadata-only"):
        pdf_path = _call_pdf_fallback(doi, root, audit=audit, paper=paper)
        if pdf_path:
            print(f"      [Fulltext] {result['status']}，PDF 兜底成功")
            return {"doi": doi, "source": "pdf_fallback", "format": "pdf",
                    "status": "ok", "path": str(pdf_path), "raw_path": str(pdf_path)}
        if result.get("status") == "metadata-only":
            print(f"      [Fulltext] metadata-only，PDF 兜底失败，无内容可保留。")
            # 不写无内容的 fulltext.md；raw xml 仍保留到 xmls/
            raw_path = None
            if result.get("raw") and result.get("raw_ext"):
                subdir = root / _format_subdir(result["raw_ext"])
                subdir.mkdir(parents=True, exist_ok=True)
                raw_path = subdir / f"{_safe_name(doi)}{result['raw_ext']}"
                raw_path.write_text(result["raw"], encoding="utf-8")
                print(f"      [Fulltext] raw 保留: {raw_path.name}")
            return {"doi": doi, "source": result["source"], "format": result["format"],
                    "status": "metadata-only", "path": None,
                    "raw_path": str(raw_path) if raw_path else None}
        print("      [Fulltext] 仅摘要且 PDF 兜底失败，保存 Markdown 源并标记 too_small。")
        result["status"] = "too_small"

    markdown_dir.mkdir(parents=True, exist_ok=True)
    md_path.write_text(result["text"], encoding="utf-8")
    # 保留原始格式（xml/tex/txt）到与 pdfs/ 同级的格式目录：<format_root>/xmls|txts|texs/<doi>.<ext>
    raw_path = None
    if result.get("raw") and result.get("raw_ext"):
        subdir = root / _format_subdir(result["raw_ext"])
        subdir.mkdir(parents=True, exist_ok=True)
        raw_path = subdir / f"{_safe_name(doi)}{result['raw_ext']}"
        raw_path.write_text(result["raw"], encoding="utf-8")
        print(f"      [Fulltext] 原始格式保留: {raw_path.name}（{subdir.name}/）")
    # 下限保护：文本过短（< MIN_FULLTEXT_BYTES）视为无有效正文（abstract-only / 解析失败），
    # 标记 .too_small，下游 extraction 跳过，避免仅有摘要/过短文档占用 LLM 资源。
    text_length = len(result["text"])
    too_small = result.get("status") == "too_small" or text_length < TOO_SMALL_FULLTEXT_CHARS
    if too_small:
        source_marker.write_text("", encoding="utf-8")
        result["status"] = "too_small"
        print(f"      [Fulltext] 文本接近摘要长度（< {TOO_SMALL_FULLTEXT_CHARS} 字符），已标记源文件。")
    elif text_length < MIN_FULLTEXT_BYTES:
        result["quality_warning"] = "short_fulltext"
    return {"doi": doi, "source": result["source"], "format": result["format"],
            "status": result.get("status", "ok"), "path": str(md_path),
            "raw_path": str(raw_path) if raw_path else None,
            "provider_attempts": provider_attempts}
