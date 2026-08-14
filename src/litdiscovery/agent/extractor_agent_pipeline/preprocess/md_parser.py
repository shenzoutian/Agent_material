"""
Markdown 论文全文解析器。

从 Markdown 论文文件中提取：
- 元数据（DOI、标题、摘要）—— 从 YAML frontmatter
- 分节正文内容 —— 从 Markdown 标题层级
- 表格数据（CSV + 标题文本）

通过参数控制输出目录。
"""

import os
import csv
import re
import unicodedata
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any

# YAML frontmatter 解析（可选依赖）
try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False


# 文本清洗工具
def clean(text: Optional[str]) -> str:
    """清洗并规范化文本：去除首尾空白、统一Unicode编码、合并多余空白字符。"""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text.strip())
    return re.sub(r"\s+", " ", text)


# 纯文本章节标题（无 # 前缀）：独立成行的编号/罗马数字标题，如 "1. Introduction"、"2 Results"
# 要求行较短且不以句点结尾，避免把普通句子误判为标题。
_PLAIN_HEADER_RE = re.compile(
    r'^\d+(?:\.\d+)*\.?\s+[A-Z][A-Za-z0-9&/\-() ]{1,60}$'
)
_ROMAN_HEADER_RE = re.compile(
    r'^[IVXLC]+\.\s+[A-Z][A-Za-z0-9&/\-() ]{1,60}$'
)


# YAML Frontmatter 解析
def _parse_simple_frontmatter(fm_text: str) -> Dict[str, Any]:
    """简易 frontmatter 解析器（PyYAML 不可用时的回退方案）。

    仅处理单行 key: value 格式，不支持嵌套结构。
    """
    metadata: Dict[str, Any] = {}
    for line in fm_text.strip().split('\n'):
        line = line.strip()
        if ':' in line:
            key, _, value = line.partition(':')
            key = key.strip().lower()
            value = value.strip().strip('"').strip("'")
            if value:
                metadata[key] = value
    return metadata


def parse_frontmatter(text: str) -> Tuple[Dict[str, Any], str]:
    """解析 Markdown 文件开头的 YAML frontmatter。

    返回 (元数据字典, 正文文本)。
    若无 frontmatter，元数据字典为空，正文为原文。
    """
    # 匹配开头的 YAML frontmatter（以 --- 包裹）
    fm_match = re.match(r'^---\s*\n(.*?)\n---\s*\n', text, re.DOTALL)
    if not fm_match:
        return {}, text

    fm_text = fm_match.group(1)
    body = text[fm_match.end():]

    if _HAS_YAML:
        try:
            metadata = yaml.safe_load(fm_text) or {}
        except yaml.YAMLError:
            metadata = _parse_simple_frontmatter(fm_text)
    else:
        metadata = _parse_simple_frontmatter(fm_text)

    # 确保返回的是字典类型
    if not isinstance(metadata, dict):
        metadata = {}
    return metadata, body



# 全文提取

def extract_md_article(md_file_path: str) -> Optional[Dict[str, Any]]:
    """
    从 Markdown 论文文件中提取元数据和分节文本。

    参数:
        md_file_path: Markdown 文件路径

    返回:
        包含 DOI、标题、摘要和各节内容的字典；解析失败返回 None
    """
    try:
        with open(md_file_path, 'r', encoding='utf-8') as f:
            text = f.read()
    except (IOError, UnicodeDecodeError) as e:
        print(f"Error reading Markdown file {md_file_path}: {e}")
        return None

    metadata, body = parse_frontmatter(text)

    # 提取元数据（兼容多种 key 的大小写）
    doi = metadata.get('doi', metadata.get('DOI', 'N/A'))
    title = metadata.get('title', metadata.get('Title', 'N/A'))
    abstract = metadata.get('abstract', metadata.get('Abstract', 'N/A'))

    # ---- 按 Markdown 标题分节解析正文 ----
    sections: Dict[str, List[str]] = {}
    current_section = "Preamble"
    sections[current_section] = []
    if "[[PAGE_BREAK]]" in body:
        sections[current_section].append("[PAGE: 1]")

    # 用于跳过表格行的标记（表格行以 | 分隔，不属于普通段落）
    in_table = False
    separator_re = re.compile(r'^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$')

    current_page = 1
    for line in body.split('\n'):
        stripped = line.strip()

        if stripped == "[[PAGE_BREAK]]":
            current_page += 1
            sections.setdefault(current_section, []).append(f"[PAGE: {current_page}]")
            continue

        # 跳过空行
        if not stripped:
            in_table = False
            continue

        # 跳过 Markdown 表格分隔符行
        if separator_re.match(stripped):
            in_table = True
            continue

        # 跳过表格行（含 | 的非标题行）
        if in_table and '|' in stripped and not stripped.startswith('#'):
            continue
        # 退出表格区域后复位
        if in_table and '|' not in stripped:
            in_table = False

        # 匹配 Markdown 标题（# ~ ######）
        header_match = re.match(r'^#{1,6}\s+(.+)$', stripped)
        is_plain_header = bool(header_match is None and
                               (_PLAIN_HEADER_RE.match(stripped) or
                                _ROMAN_HEADER_RE.match(stripped)))
        if (header_match or is_plain_header) and '|' not in stripped:
            # 纯文本编号标题（如 "1. Introduction"、"2 Methods"、"I. INTRODUCTION"）
            # 取编号后的纯标题作为章节名
            if is_plain_header:
                section_title = re.sub(
                    r'^\d+(?:\.\d+)*\.?\s+', '', stripped.strip())
                section_title = re.sub(r'^[IVXLC]+\.\s+', '', section_title)
            else:
                section_title = clean(header_match.group(1))
            if section_title:
                current_section = section_title
                if current_section not in sections:
                    sections[current_section] = []
        else:
            # 非标题行作为段落内容
            para_text = clean(stripped)
            if para_text:
                sections.setdefault(current_section, []).append(para_text)

    # ---- 后处理：对无 YAML frontmatter 的文件，从正文推断元数据 ----

    # 若 Abstract 在正文中作为独立节出现而元数据中缺失，从正文提取
    # 保留段落间的换行，避免多段摘要被合并成单个超长行
    if abstract == 'N/A':
        for sec_name in list(sections.keys()):
            if sec_name.lower() == 'abstract':
                abstract = '\n'.join(sections.pop(sec_name))
                break

    # 若 title 缺失，取第一个实质性的 ## 标题作为论文标题
    if title == 'N/A':
        for sec_name in list(sections.keys()):
            # 跳过明显不是论文标题的短节名（作者名、机构名等）
            if sec_name.lower() in ('preamble', 'introduction', 'abstract',
                                      'references', 'acknowledgments',
                                      'a preprint', 'preprint'):
                continue
            # 取第一个有实际内容的节名作为标题
            title = sec_name
            # 将该节的内容合并到 Preamble（作为标题后的摘要信息）
            if sections.get(sec_name):
                sections.setdefault('Preamble', []).extend(sections.pop(sec_name))
            else:
                sections.pop(sec_name, None)
            break

    # 清理空的 Preamble 节
    if 'Preamble' in sections and not sections['Preamble']:
        del sections['Preamble']

    return {
        "doi": doi,
        "title": title,
        "abstract": abstract,
        "sections": sections,
    }


# 表格提取

def _csv_safe_cell(value) -> str:
    """把单元格规范为单行文本，防止破坏 CSV 记录结构。"""
    s = str(value)
    # 嵌入换行/回车会令单行 CSV 记录跨行，统一改为空格
    return s.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")


def _write_csv_rows(csv_file: str, rows) -> None:
    """稳健写 CSV：默认 QUOTE_MINIMAL；若单元格触发转义错误，回退全量引号。"""
    safe_rows = [[_csv_safe_cell(c) for c in row] for row in rows]
    try:
        with open(csv_file, "w", encoding="utf-8", newline="") as f:
            csv.writer(f).writerows(safe_rows)
        return
    except csv.Error:
        pass
    # 兜底：QUOTE_ALL 全量引号，任何单元格都不会再触发 “need to escape”
    with open(csv_file, "w", encoding="utf-8", newline="") as f:
        csv.writer(f, quoting=csv.QUOTE_ALL).writerows(safe_rows)


def extract_md_tables(
    md_path: str,
    output_dir: str,
) -> List[Tuple[str, str]]:
    """
    从 Markdown 文件中提取表格及其标题，保存为 CSV/TXT 文件。

    Markdown 表格格式：
        | Header1 | Header2 |
        |---------|---------|
        | cell1   | cell2   |

    标题通常为表格上方紧邻的非标题行文本。

    参数:
        md_path: Markdown 文件路径
        output_dir: 输出目录

    返回:
        列表，每项为 (caption_file_path, csv_file_path)
    """
    os.makedirs(output_dir, exist_ok=True)

    try:
        with open(md_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except (IOError, UnicodeDecodeError) as e:
        print(f"Error reading {md_path}: {e}")
        return []

    # 表格分隔符行正则
    separator_re = re.compile(r'^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$')

    tables: List[Tuple[str, List[List[str]]]] = []  # (caption, rows)
    current_caption: Optional[str] = None
    in_table = False
    table_rows: List[List[str]] = []

    for i, line in enumerate(lines):
        stripped = line.strip()

        # 检测表格分隔符行
        if separator_re.match(stripped):
            in_table = True
            # 若之前没有标题，向前查找
            if current_caption is None:
                for j in range(i - 1, -1, -1):
                    prev = lines[j].strip()
                    if prev and not prev.startswith('#') and '|' not in prev:
                        current_caption = prev
                        break
            continue

        # 表格行（包含 |）
        if in_table and '|' in stripped:
            cells = [clean(c) for c in stripped.split('|')]
            cells = [c for c in cells if c]  # 去掉首尾 | 产生的空字符串
            if cells:
                table_rows.append(cells)
            continue

        # 非表格内容：如果之前在表格中，保存当前表格
        if in_table and table_rows:
            tables.append((current_caption or f"Table {len(tables) + 1}", table_rows))
            current_caption = None
            in_table = False
            table_rows = []

        # 记录潜在标题（非标题、非表格的普通行）
        if stripped and not stripped.startswith('#') and '|' not in stripped:
            current_caption = stripped

    # 保存文件末尾的最后一个表格
    if in_table and table_rows:
        tables.append((current_caption or f"Table {len(tables) + 1}", table_rows))

    # === 保存文件 ===
    saved_files: List[Tuple[str, str]] = []
    for i, (caption, rows) in enumerate(tables, 1):
        if not rows:
            continue

        tag = f"table{i}"

        # 保存标题文本
        caption_file = os.path.join(output_dir, f"{tag}_caption.md")
        try:
            with open(caption_file, "w", encoding="utf-8") as f:
                f.write(clean(caption))
            print(f"   Saved caption: {caption_file}")
        except IOError as e:
            print(f"Error saving caption file {caption_file}: {e}")
            continue

        # 保存 CSV
        csv_file = os.path.join(output_dir, f"{tag}.csv")
        try:
            _write_csv_rows(csv_file, rows)
            print(f"   Saved table: {csv_file}")
        except (IOError, csv.Error) as e:
            print(f"Error saving CSV file {csv_file}: {e}")
            continue

        saved_files.append((caption_file, csv_file))

    if not saved_files:
        print(f"   No tables found in: {md_path}")
    return saved_files


# 批量处理入口

def process_md_directory(
    input_md_dir: str = "md_data",
    output_root_dir: str = "processed_articles",
    force: bool = False,
    dry_run: bool = False,
) -> None:
    """
    批量处理目录下所有 Markdown (.md) 文件：
    提取全文并保存为 fulltext.md，表格保存为 table*.csv + table*_caption.md。

    默认增量处理：若某个 md 文件对应的输出文件夹已存在 fulltext.md，
    则跳过该文件；使用 force=True 可强制全部重新处理。

    参数:
        input_md_dir: 存放 .md 文件的输入目录
        output_root_dir: 按 DOI 命名的输出根目录
        force: 强制重新处理所有文件（忽略已有输出）
        dry_run: 仅列出将处理的文件，不写入任何输出
    """
    os.makedirs(input_md_dir, exist_ok=True)

    md_files = sorted(f for f in os.listdir(input_md_dir) if f.endswith(".md"))

    if not md_files:
        print(f"No .md files found in '{input_md_dir}'. "
              f"Please place your Markdown files there.")
        return

    # 先解析并筛选待处理文件
    to_process = []
    skipped = []

    for md_filename in md_files:
        md_file_path = os.path.join(input_md_dir, md_filename)

        article_data = extract_md_article(md_file_path)
        if article_data is None:
            print(f"Failed to extract article data from '{md_filename}'. Skipping.")
            continue

        # 确定输出文件夹名（优先使用 DOI）
        # 命名与 download 的 _safe_name 一致：先替换 / → _，再去非词字符，
        # 避免同名 DOI 因规范化差异产生两个目录（如括号 (01) 的 DOI）
        def _foldername(s: str) -> str:
            return re.sub(r"[^\w\-.]", "", s.replace("/", "_").replace("\\", "_"))

        if article_data["doi"] != "N/A":
            doi_folder_name = _foldername(article_data["doi"])
        else:
            doi_folder_name = _foldername(os.path.splitext(md_filename)[0])

        article_output_dir = os.path.join(output_root_dir, doi_folder_name)

        if not force and os.path.exists(os.path.join(article_output_dir, "fulltext.md")):
            skipped.append(md_filename)
            continue

        to_process.append((md_filename, article_data, article_output_dir))

    print(f"Input:  {input_md_dir}  ({len(md_files)} .md files)")
    print(f"Output: {output_root_dir}")
    print(f"To process: {len(to_process)}  |  Already done (skip): {len(skipped)}")
    if skipped:
        for s in skipped:
            print(f"  SKIP: {s}")

    if dry_run:
        if to_process:
            print("Would process:")
            for md_filename, _, _ in to_process:
                print(f"  → {md_filename}")
        else:
            print("Nothing to process.")
        return

    os.makedirs(output_root_dir, exist_ok=True)

    if not to_process:
        print("Nothing to process.")
        return

    for md_filename, article_data, article_output_dir in to_process:
        print(f"\nProcessing '{md_filename}'...")
        md_file_path = os.path.join(input_md_dir, md_filename)
        os.makedirs(article_output_dir, exist_ok=True)

        # --- 保存全文文本 ---
        fulltext_path = os.path.join(article_output_dir, "fulltext.md")
        try:
            with open(fulltext_path, "w", encoding="utf-8") as f:
                f.write(f"Title: {article_data['title']}\n\n")
                f.write(f"DOI: {article_data['doi']}\n\n")
                f.write(f"Abstract:\n{article_data['abstract']}\n\n")
                for section, paras in article_data["sections"].items():
                    f.write(f"\n=== {section} ===\n")
                    for para in paras:
                        f.write(f"{para}\n\n")
            print(f"[OK] Full text saved to: {fulltext_path}")
        except IOError as e:
            print(f"Error saving full text: {e}")

        # --- 提取表格 ---
        table_info = extract_md_tables(md_file_path, article_output_dir)

        # 重命名为统一格式：table1.csv, table1_caption.md, ...
        for i, (caption_path, csv_path) in enumerate(table_info, 1):
            table_csv_final = os.path.join(article_output_dir,
                                           f"table{i}.csv")
            table_caption_final = os.path.join(article_output_dir,
                                               f"table{i}_caption.md")

            try:
                os.rename(csv_path, table_csv_final)
                os.rename(caption_path, table_caption_final)
                print(f"[OK] Table {i} saved as {table_csv_final} "
                      f"and caption as {table_caption_final}")
            except OSError as e:
                print(f"[FAIL] Failed to rename table files: {e}")

    print("\n[OK] Processing complete.")


# ============================================================
# XML 输入处理（兼容 pdf/txt→md 之外的原生 XML 全文）
# ============================================================
def process_xml_directory(
    input_xml_dir: str,
    output_root_dir: str,
    force: bool = False,
    dry_run: bool = False,
) -> None:
    """
    批量处理目录下所有 XML 论文全文（.xml / .xml.gz）。

    用 download/fulltext.xml_to_markdown 转 md，产出与 md 链完全一致的
    fulltext.md + table*.csv + table*_caption.md（并保留原始 source.xml）。

    参数:
        input_xml_dir: 存放 .xml/.xml.gz 的输入目录
        output_root_dir: 按 DOI/文件名命名的输出根目录
        force: 强制重新处理（默认跳过已有 fulltext.md）
        dry_run: 仅列出将处理的文件
    """
    os.makedirs(input_xml_dir, exist_ok=True)

    xml_files = sorted(
        f for f in os.listdir(input_xml_dir)
        if f.endswith(".xml") or f.endswith(".xml.gz"))
    if not xml_files:
        print(f"No XML files found in '{input_xml_dir}'.")
        return

    to_process, skipped = [], []
    for xml_name in xml_files:
        # 输出文件夹名：去扩展名、去非词字符（与 md 链 DOI 命名一致）
        stem = os.path.splitext(xml_name)[0]
        if stem.endswith(".xml"):
            stem = stem[:-4]
        folder_name = re.sub(r"[^\w\-.]", "", stem)
        out_dir = os.path.join(output_root_dir, folder_name)
        if not force and os.path.exists(os.path.join(out_dir, "fulltext.md")):
            skipped.append(xml_name)
            continue
        to_process.append((xml_name, out_dir))

    print(f"Input:  {input_xml_dir}  ({len(xml_files)} .xml files)")
    print(f"Output: {output_root_dir}")
    print(f"To process: {len(to_process)}  |  Already done (skip): {len(skipped)}")

    if dry_run:
        for xml_name, out_dir in to_process:
            print(f"  → {xml_name} → {out_dir}")
        return

    from litdiscovery.agent.filter_agent_pipeline.fulltext import xml_to_markdown

    os.makedirs(output_root_dir, exist_ok=True)
    if not to_process:
        print("Nothing to process.")
        return

    for xml_name, out_dir in to_process:
        print(f"\nProcessing '{xml_name}'...")
        os.makedirs(out_dir, exist_ok=True)
        xml_path = os.path.join(input_xml_dir, xml_name)

        # 读取（xml.gz 解压）
        try:
            if xml_name.endswith(".gz"):
                import gzip
                with gzip.open(xml_path, "rb") as f:
                    xml_text = f.read().decode("utf-8", errors="replace")
            else:
                with open(xml_path, "r", encoding="utf-8") as f:
                    xml_text = f.read()
        except (IOError, UnicodeDecodeError) as e:
            print(f"Error reading {xml_path}: {e}")
            continue

        md, status = xml_to_markdown(xml_text)

        # 保留原始 XML
        source_path = os.path.join(out_dir, "source.xml")
        try:
            with open(source_path, "w", encoding="utf-8") as f:
                f.write(xml_text)
            print(f"[OK] Source XML saved: {source_path}")
        except IOError as e:
            print(f"Error saving source XML: {e}")

        # 写 fulltext.md（与 md 链同构：Title/DOI/Abstract + 各节）
        fulltext_path = os.path.join(out_dir, "fulltext.md")
        try:
            with open(fulltext_path, "w", encoding="utf-8") as f:
                f.write(md)
            print(f"[OK] Full text saved to: {fulltext_path} (status={status})")
        except IOError as e:
            print(f"Error saving full text: {e}")

        # 表格提取（复用 md 链的 extract_md_tables）
        table_info = extract_md_tables(fulltext_path, out_dir)
        for i, (caption_path, csv_path) in enumerate(table_info, 1):
            table_csv_final = os.path.join(out_dir, f"table{i}.csv")
            table_caption_final = os.path.join(out_dir, f"table{i}_caption.md")
            try:
                os.rename(csv_path, table_csv_final)
                os.rename(caption_path, table_caption_final)
                print(f"[OK] Table {i} saved as {table_csv_final}")
            except OSError as e:
                print(f"[FAIL] Failed to rename table files: {e}")

    print("\n[OK] XML Processing complete.")
