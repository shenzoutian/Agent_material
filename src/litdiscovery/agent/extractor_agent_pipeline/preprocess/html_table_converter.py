"""
HTML 表格 → CSV 转换器。

将非 Markdown 来源论文中的 table_*.html 表格文件
转换为 table_*.csv 格式，统一后续处理流程。

注：Markdown 解析流程（md_parser）已直接输出 CSV，此模块
仅用于处理已有的 HTML 格式表格数据。
"""

import os
import re


def convert_html_tables_to_csv(root_folder: str) -> None:
    """
    递归遍历 root_folder，将所有 table_*.html 转换为 table_*.csv。

    参数:
        root_folder: 包含论文子文件夹的根目录
    """
    import pandas as pd

    for dirpath, _, filenames in os.walk(root_folder):
        for filename in filenames:
            if re.match(r'table_\d+\.html$', filename):
                html_path = os.path.join(dirpath, filename)
                table_num = re.findall(r'\d+', filename)[0]
                csv_filename = f"table_{table_num}.csv"
                csv_path = os.path.join(dirpath, csv_filename)

                try:
                    tables = pd.read_html(html_path)
                    if tables:
                        tables[0].to_csv(csv_path, index=False)
                        print(f"[OK] Converted {html_path} -> {csv_path}")
                    else:
                        print(f"[!] No tables found in: {html_path}")
                except Exception as e:
                    print(f"❌ Error reading {html_path}: {e}")
