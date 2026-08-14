"""
common/markdown.py —— XML / LaTeX → markdown 转换。
"""

import re
import xml.etree.ElementTree as ET


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


def _xml_walk(elem, lines):
    """递归遍历 XML，按语义标签输出 markdown 结构。"""
    name = _localname(elem.tag)
    txt = (elem.text or "").strip()
    if name in ("section-title", "section_title", "ce:section-title"):
        lines.append(f"\n## {txt}\n" if txt else "")
    elif name in ("abstract", "abstract-sec", "abstractsec"):
        lines.append(f"\n## Abstract\n{txt}\n" if txt else "\n## Abstract\n")
    elif name in ("para", "p", "ce:para"):
        lines.append(f"\n{txt}\n" if txt else "")
    elif name in ("title", "article-title", "dc:title", "ce:title"):
        if txt:
            lines.append(f"\n# {txt}\n")
    if name in ("table", "ce:table", "table-wrap", "table-frame", "ce:table-frame"):
        lines.append(_render_table_xml(elem))
        # 表格内部不再作为纯文本递归，避免单元格被重复输出
        for tail in getattr(elem, "tail", None) or "":
            pass
        return
    for child in list(elem):
        _xml_walk(child, lines)
        if child.tail and child.tail.strip():
            lines.append(child.tail.strip())


def xml_to_markdown(xml_text: str):
    """任意学术 XML（Elsevier/NXML）→ (markdown 文本, status)。

    status ∈ {ok, abstract-only, degraded}。解析失败时降级为标签剥离。
    """
    try:
        root = ET.fromstring(xml_text)
        lines = []
        _xml_walk(root, lines)
        md = "\n".join(l for l in lines if l)
        has_body = len(md) > 200 and any(k in md.lower() for k in
                                         ("## ", "# ", "abstract"))
        status = "ok" if has_body else "abstract-only"
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
