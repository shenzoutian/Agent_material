"""
CSV 稀疏列清理工具。

删除论文表格 CSV 中有效数据极少（≤2 个非空值）的列，
减少噪声干扰。
"""

from pathlib import Path


def drop_sparse_columns(root_dir: str, min_non_null: int = 3) -> None:
    """
    遍历每个子文件夹中的所有 CSV，删除非空值 ≤ min_non_null 的列。

    参数:
        root_dir: 论文文件夹的根目录
        min_non_null: 保留列所需的最小非空行数（默认 3）
    """
    import pandas as pd

    root = Path(root_dir)

    for folder in root.iterdir():
        if not folder.is_dir():
            continue

        print(f"[>] Processing folder: {folder.name}")

        for csv_file in folder.glob("*.csv"):
            print(f"  [+] Reading: {csv_file.name}")

            try:
                df = pd.read_csv(csv_file)
                filtered_df = df.dropna(axis=1, thresh=min_non_null)
                filtered_df.to_csv(csv_file, index=False)
                removed = len(df.columns) - len(filtered_df.columns)
                print(f"  [OK] Cleaned and saved: {csv_file.name} (removed {removed} columns)")
            except Exception as e:
                print(f"  ❌ Failed to process {csv_file.name}: {e}")
