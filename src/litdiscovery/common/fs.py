"""
common/fs.py —— 文件系统共享工具。
确保 DOI ↔ 目录名的映射在所有环节一致（这是 end_mds/ 与 data_doi/ 对齐的契约）。
"""

import os
import re
import json
import shutil
import hashlib
from pathlib import Path

_DOI_CLEAN_RE = re.compile(r"[^\w\-.]")


def safe_folder_name(doi: str) -> str:
    """DOI → 安全目录名（全项目唯一规约）。

    - 将 "/"、"\\" 替换为 "_"（如 10.1016/j.matdes.2015.12.174 → 10.1016_j.matdes.2015.12.174）；
    - 再删除所有非「单词字符 / 连字符 / 点」的字符（去括号等，如含 (01) 的 DOI 也会被规范化）。
    与 md_parser 的命名一致，保证 download 建目录与预处理生成的 folder 名对齐。
    """
    return _DOI_CLEAN_RE.sub("", doi.replace("/", "_").replace("\\", "_"))


def write_text_atomic(path: str | Path, content: str, encoding: str = "utf-8") -> None:
    """原子写文本：先写临时文件再 os.replace，避免半写状态。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding=encoding)
    os.replace(tmp, path)


def write_json_atomic(path: str | Path, data, ensure_ascii: bool = False) -> None:
    """原子写 JSON。"""
    write_text_atomic(path, json.dumps(data, ensure_ascii=ensure_ascii, indent=2))


def rewrite_paths_in_text(text: str, mapping: dict) -> str:
    """按旧→新路径映射改写文本中的绝对旧路径（字符串替换）。"""
    for old, new in mapping.items():
        text = text.replace(old, new)
    return text


def rewrite_paths_in_json(data, mapping: dict):
    """递归改写 JSON 结构中所有字符串值里的旧路径（就地返回新结构）。"""
    if isinstance(data, dict):
        return {k: rewrite_paths_in_json(v, mapping) for k, v in data.items()}
    if isinstance(data, list):
        return [rewrite_paths_in_json(v, mapping) for v in data]
    if isinstance(data, str):
        return rewrite_paths_in_text(data, mapping)
    return data


def rewrite_paths_in_md(path: str | Path, mapping: dict) -> None:
    """改写 md 文件中所有旧绝对路径，覆盖写回。"""
    p = Path(path)
    if not p.exists():
        return
    text = p.read_text(encoding="utf-8")
    p.write_text(rewrite_paths_in_text(text, mapping), encoding="utf-8")


def dir_sha256_summary(path: str | Path) -> str:
    """目录内容摘要：对目录下所有文件名 + 大小 + mtime 做 sha256（用于迁移回滚比对）。"""
    p = Path(path)
    if not p.is_dir():
        return ""
    lines = []
    for root, dirs, files in os.walk(p):
        for name in sorted(files):
            fp = os.path.join(root, name)
            try:
                st = os.stat(fp)
                lines.append(f"{os.path.relpath(fp, p)}:{st.st_size}:{int(st.st_mtime)}")
            except OSError:
                continue
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def copy_tree(src: str | Path, dst: str | Path, ignore: tuple = ()) -> None:
    """复制目录树（忽略指定文件）。"""
    shutil.copytree(src, dst, dirs_exist_ok=True, ignore=shutil.ignore_patterns(*ignore))
