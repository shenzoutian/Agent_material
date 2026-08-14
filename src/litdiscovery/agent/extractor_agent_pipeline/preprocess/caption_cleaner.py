"""
表格标题清洗工具。

论文 Markdown 解析时，表格数据行的文本可能污染标题（caption）。
本模块通过精确字符串匹配，从标题中移除误混入的单元格内容。
"""

import os
import csv
import re
import unicodedata
from typing import Optional


def clean(text: Optional[str]) -> str:
    """清洗并规范化文本。"""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text.strip())
    return re.sub(r"\s+", " ", text)


def clean_table_captions(output_root_dir: str) -> None:
    """
    遍历每个 DOI 子文件夹，读取 table*.csv 并清洗 table*_caption.md。

    方法：从标题中精确移除出现在 CSV 单元格中的文本片段（无启发式规则）。

    参数:
        output_root_dir: 已处理论文的根目录
    """
    for doi_folder in os.listdir(output_root_dir):
        folder_path = os.path.join(output_root_dir, doi_folder)
        if not os.path.isdir(folder_path):
            continue

        print(f"\n[>] Processing folder: {doi_folder}")

        for file in os.listdir(folder_path):
            if not file.endswith(".csv"):
                continue

            table_csv_path = os.path.join(folder_path, file)
            table_id = os.path.splitext(file)[0]
            caption_path = os.path.join(folder_path, f"{table_id}_caption.md")

            if not os.path.exists(caption_path):
                print(f"[!] Caption missing for {table_id}")
                continue

            # 读取 CSV 内容
            with open(table_csv_path, "r", encoding="utf-8") as f:
                reader = csv.reader(f)
                rows = list(reader)

            # 读取原始标题
            with open(caption_path, "r", encoding="utf-8") as f:
                caption = f.read()

            original_caption = caption

            # 精确字符串匹配删除
            for row in rows:
                for cell in row:
                    cell = cell.strip()
                    if cell and cell in caption:
                        caption = caption.replace(cell, "")

            cleaned_caption = re.sub(r"\s+", " ", caption).strip()

            # 覆盖写入
            with open(caption_path, "w", encoding="utf-8") as f:
                f.write(cleaned_caption)

            removed = len(original_caption) - len(cleaned_caption)
            print(f"[OK] Cleaned: {file} — removed {removed} characters")
