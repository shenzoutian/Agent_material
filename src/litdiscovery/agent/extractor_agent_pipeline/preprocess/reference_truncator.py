"""
fulltext.md 清理工具：
1. 章节删除：删除 Introduction 章节；并在 References 处直接截断——
   References 及其后所有内容全部删除，以减少下游 LLM 提取时的 token 消耗。
2. 图片垃圾清理：删除从图片（坐标轴、面板）错误提取产生的垃圾内容

"""

import os
import re


def _normalize_header(line: str) -> str:
    """归一化章节标题行：去掉 === 分隔符、markdown 井号、前导编号与尾部冒号，返回小写纯标题。"""
    s = line.strip().lower()
    s = re.sub(r'^={3,}\s*', '', s)          # 去掉前导 ===
    s = re.sub(r'\s*={3,}$', '', s)          # 去掉尾部 ===
    s = s.lstrip('#').strip()                # 去掉 markdown 井号
    s = re.sub(r'^\d+(\.\d+)*\.?\s+', '', s)  # 去掉前导阿拉伯数字编号 "1. " / "1.1 " / "2 "
    s = re.sub(r'^[ivxlc]+\.\s+', '', s)      # 去掉前导罗马数字编号 "I. " / "II. "
    s = re.sub(r'^\s*[-–—]\s*', '', s)        # 去掉前导破折号 "- "（如 "- a)"）
    s = re.sub(r':+\s*$', '', s).strip()      # 去掉尾部冒号 "References:" -> "references"
    return s.strip()


def _is_introduction_header(line: str) -> bool:
    """检查文本行是否为 Introduction 章节标题。"""
    return _normalize_header(line) in ("introduction", "引言", "简介")


def _is_references_header(line: str) -> bool:
    """检查文本行是否为 References / Bibliography 章节标题（含单数 Reference）。"""
    return _normalize_header(line) in ("references", "reference", "bibliography", "参考文献")


def _is_section_header(line: str) -> bool:
    """检查文本行是否为任何章节标题（=== X === / ===== X 或 markdown # X 格式）。"""
    s = line.strip()
    return bool(re.match(r'^=+\s+\S', s)) or bool(re.match(r'^#{1,6}\s+\S', s))


def _drop_sections(lines) -> list:
    """删除 Introduction 章节；并在 References 处直接截断至文件末尾。

    - Introduction：从标题行开始删除，直到遇到下一个章节标题为止（不含）；
      若位于文末则删除至文件末尾。
    - References：一旦命中即视为文末，References 及其后所有内容（含
      Appendix/致谢等）全部截断删除，不恢复后续内容。
    """
    kept = []
    dropping = False

    for line in lines:
        if _is_references_header(line):
            # References 及其后所有内容直接截断到文件末尾
            break
        if _is_introduction_header(line):
            dropping = True
            continue
        if dropping:
            if _is_section_header(line):  # 遇到下一个章节标题时停止删除
                dropping = False
                kept.append(line)
            continue
        kept.append(line)

    return kept


def remove_introduction_and_references(root_folder: str) -> None:
    """
    删除所有 fulltext.md 中的 Introduction 章节；并在 References 处截断
    （References 及其后所有内容全部删除），覆盖写回。

    参数:
        root_folder: 包含论文子文件夹的根目录
    """
    for dirpath, _, filenames in os.walk(root_folder):
        if "fulltext.md" not in filenames:
            continue

        file_path = os.path.join(dirpath, "fulltext.md")

        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        kept = _drop_sections(lines)

        with open(file_path, "w", encoding="utf-8") as f:
            f.writelines(kept)

        print(f"[OK] Removed Introduction + truncated at References: {file_path}")

# 图片垃圾清理

# 孤立数字行：单个或多个带符号/小数的数字，如 "80"、"-291 -200"
_NUM_LINE_RE = re.compile(
    r'^\s*[-+]?\d+(?:\.\d+)?'
    r'(?:\s*[,;]\s*[-+]?\d+(?:\.\d+)?|\s+[-+]?\d+(?:\.\d+)?)*\s*$'
)
# 坐标轴标签行：短文本 + 末尾括号单位，如 "X [mm]"、"VelocityX [m/s]:"、"Area (μm2)"
# 文本部分允许常见单位字符（μ、°、²、Ω 等），并按字母数 ≥3 区分真实轴标签与图片图例短词
_AXIS_LINE_RE = re.compile(
    r'^\s*[A-Za-z][\w μ°²Ω−–\-–.]{0,29}\s*[\[(]\s*[^\[\]()]{1,12}\s*[)\]]\s*:?\s*$'
)
_AXIS_MIN_ALPHA = 3
# 图片面板标记行：(a) (b) (A) (1) ...
_PAREN_LINE_RE = re.compile(r'^\s*\([a-z0-9]\)\s*$', re.IGNORECASE)

# 轴标签行的最大长度（避免误删表格标题等长行）
_AXIS_MAX_LEN = 40


def _is_image_garbage_line(line: str) -> bool:
    """判断一行是否为图片提取产生的垃圾行。"""
    s = line.strip()
    if not s:
        return False
    if _NUM_LINE_RE.match(s):
        return True
    if _PAREN_LINE_RE.match(s):
        return True
    if len(s) <= _AXIS_MAX_LEN and _AXIS_LINE_RE.match(s):
        # 轴标签行要求含至少 3 个字母，避免把图例短词误当轴标签
        alpha_count = sum(1 for ch in s if ch.isalpha())
        if alpha_count >= _AXIS_MIN_ALPHA:
            return True
    return False


_HTML_COMMENT_RE = re.compile(r'<!--.*?-->', flags=re.DOTALL)


def _clean_line_html_comments(line: str) -> str:
    """删除单行内的 HTML 注释，返回清理后的行。

    注意: 注释内容本身可能含 "->" 文本（如 "<!-- formula-not-decoded -->"），
    因此此处逐行、无跨行匹配，且不改变行内其余文本。
    """
    return _HTML_COMMENT_RE.sub('', line)


def _drop_image_artifacts(text: str) -> str:
    """从文本中删除图片提取产生的孤立垃圾行与行内 HTML 注释。

    - 独占一行的 HTML 注释占位符（如 "<!-- image -->"）整行删除；
    - 行内的 HTML 注释（如嵌在正文段落中的 "<!-- formula-not-decoded -->"）
      仅清空其注释标记，保留该行其余文本；
    - 坐标轴刻度数字、坐标轴标签行、图片面板标记行整行删除。
    """
    kept_lines = []
    for line in text.split('\n'):
        if not line.strip():
            kept_lines.append(line)
            continue
        # 独占一行的注释占位符整行删除
        if _HTML_COMMENT_RE.match(line.strip()):
            continue
        # 行内注释：仅清除注释标记
        cleaned = _clean_line_html_comments(line)
        # 行内注释清理后可能是孤立垃圾行（如 "80 <!-- image -->"），再判一次
        if cleaned != line and not cleaned.strip():
            continue
        if _is_image_garbage_line(cleaned):
            continue
        kept_lines.append(cleaned)
    return '\n'.join(kept_lines)


def remove_image_artifacts(root_folder: str) -> None:
    """
    删除所有 fulltext.md 中从图片提取产生的垃圾内容，覆盖写回。

    包括 HTML 注释占位符、坐标轴刻度数字、坐标轴标签行、面板标记行。

    参数:
        root_folder: 包含论文子文件夹的根目录
    """
    for dirpath, _, filenames in os.walk(root_folder):
        if "fulltext.md" not in filenames:
            continue

        file_path = os.path.join(dirpath, "fulltext.md")

        with open(file_path, "r", encoding="utf-8") as f:
            text = f.read()

        cleaned = _drop_image_artifacts(text)

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(cleaned)

        print(f"[OK] Removed image artifacts: {file_path}")
