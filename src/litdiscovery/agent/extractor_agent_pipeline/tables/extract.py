"""
litdiscovery/agent/extractor_agent_pipeline/tables/extract.py — 从 markdown 全文中提取 pipe 表格。

面向**任意 fulltext.md**（包括 --fulltext 产出的 arXiv LaTeX / Elsevier XML /
CORE / Europe PMC 全文），把 pipe 表格统一提取为 Table 对象
（md_parser 只在预处理的 PDF→markdown 链生成 table{i}.csv）。

关键防御：**必须出现分隔行**（| --- | --- |）才确认是表格。否则论文正文中的
|V|、|E| 等集合符号 / 绝对值记号会被误判为表格行（arXiv 数学论文里很常见）。

输出：Table dataclass（caption + header + rows），供
- headers.classify_headers 做表头角色分类，
- rules.extract_records 做规则抓取，
- output.write_table_csvs 落盘为 table{i}.csv。
"""

import re
from dataclasses import dataclass, field
from typing import List, Optional


# Markdown 表格分隔行：| --- | :--: | --- |（可带对齐冒号、可无首尾竖线）
_SEPARATOR_RE = re.compile(r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$")

# 标题/非表格行判定
_HEADING_RE = re.compile(r"^\s*#{1,6}\s+")


@dataclass
class Table:
    """一个 markdown pipe 表格。

    header: 表头单元格（去竖线、去空白）；rows: 数据行（每行单元格列表）。
    """
    index: int                       # 1-based 表序号
    caption: str                     # 表上方紧邻的非表格行；缺省 "Table N"
    header: List[str] = field(default_factory=list)
    rows: List[List[str]] = field(default_factory=list)

    @property
    def ncols(self) -> int:
        return len(self.header) or (len(self.rows[0]) if self.rows else 0)

    def to_dict(self) -> dict:
        """供 LLM 表格提取路径消费的 dict。"""
        return {
            "filename": f"table{self.index}.csv",
            "caption": self.caption,
            "rows": [dict(zip(self.header, r)) for r in self.rows],
            "row_count": len(self.rows),
        }


def _clean_cell(cell: str) -> str:
    # 清理 markdown 强调（**0.51** → 0.51）与多余空白
    cell = re.sub(r"\*\*([^*]+)\*\*", r"\1", cell)
    cell = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", cell)
    return re.sub(r"\s+", " ", cell).strip()


def _split_row(line: str) -> List[str]:
    """拆分一行 pipe 表格：去首尾竖线，按 | 切分并清理。"""
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [_clean_cell(c) for c in line.split("|")]


def extract_tables(md_text: str) -> List[Table]:
    """从 markdown 文本提取全部 pipe 表格。

    算法：逐行扫描；遇到分隔行确认表格起点，向后收集连续表格行直到非表格行；
    分隔行上方最近的非表格、非标题行作为 caption（与 md_parser 一致）。

    返回: [Table, ...]（按出现顺序编号，从 1 开始）
    """
    lines = md_text.splitlines()
    tables: List[Table] = []
    i = 0
    n = len(lines)

    while i < n:
        stripped = lines[i].strip()
        if not stripped:
            i += 1
            continue

        # 只有出现分隔行才可能是一张表
        if not _SEPARATOR_RE.match(stripped):
            i += 1
            continue

        # 找表头：分隔行的前一行（必须是含 | 的行）
        header: List[str] = []
        if i > 0:
            prev = lines[i - 1].strip()
            if "|" in prev and not _SEPARATOR_RE.match(prev) and not _HEADING_RE.match(prev):
                header = _split_row(prev)

        # caption：再往前找最近的非表格、非空行；遇标题行则取其文本作 caption
        caption = ""
        j = i - 2
        while j >= 0:
            cand = lines[j].strip()
            if not cand:
                j -= 1
                continue
            if "|" in cand or _SEPARATOR_RE.match(cand):
                break
            hm = _HEADING_RE.match(cand)
            if hm:
                caption = _clean_cell(re.sub(r"^\s*#{1,6}\s+", "", cand))
                break
            caption = _clean_cell(cand)
            break

        # 收集数据行：分隔行之后的连续表格行
        rows: List[List[str]] = []
        k = i + 1
        while k < n:
            line = lines[k].strip()
            if "|" not in line:
                break
            if _SEPARATOR_RE.match(line):
                break
            if _HEADING_RE.match(line):
                break
            cells = _split_row(line)
            # 丢弃整行为空单元格的行
            if any(c for c in cells):
                rows.append(cells)
            k += 1

        # 统一列数（表头为准；表头缺省时以首行数据行宽度为准）
        ncols = len(header) if header else (len(rows[0]) if rows else 0)
        if header and rows:
            for r in rows:
                while len(r) < ncols:
                    r.append("")
                del r[ncols:]

        idx = len(tables) + 1
        tables.append(Table(
            index=idx,
            caption=caption or f"Table {idx}",
            header=header,
            rows=rows,
        ))

        # 跳过已消费的行
        i = k
        continue

    return tables


def extract_tables_from_file(md_path) -> List[Table]:
    """从文件读取并提取表格。"""
    with open(md_path, "r", encoding="utf-8") as f:
        return extract_tables(f.read())
