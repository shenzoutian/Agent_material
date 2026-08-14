"""
Token 统计工具。

统计各论文 fulltext.md 的 token 数量并写入 token_count.txt，
供下游提取 Agent 估算 max_tokens 使用。
"""

import os


# ============================================================
# Token 统计
# ============================================================

_ENCODING = None  # tiktoken 编码器，首次调用时初始化


def count_tokens(text: str) -> int:
    """计算文本的 token 数量。"""
    global _ENCODING
    if _ENCODING is None:
        import tiktoken
        _ENCODING = tiktoken.get_encoding("cl100k_base")
    return len(_ENCODING.encode(text))


# ============================================================
# 批量处理
# ============================================================

def count_total_tokens(output_root_dir: str) -> float:
    """
    遍历每个 DOI 文件夹，统计 fulltext.md 的 token 数，
    写入各文件夹的 token_count.txt，并返回全部论文的总 token 数。

    参数:
        output_root_dir: 已处理论文的根目录

    返回:
        总 token 数（百万单位）
    """
    total_token_count = 0

    for doi_folder in os.listdir(output_root_dir):
        folder_path = os.path.join(output_root_dir, doi_folder)
        if not os.path.isdir(folder_path):
            continue

        fulltext_path = os.path.join(folder_path, "fulltext.md")
        if not os.path.exists(fulltext_path):
            print(f"[!] fulltext.md missing in {doi_folder}")
            continue

        with open(fulltext_path, "r", encoding="utf-8") as f:
            text = f.read()

        token_count = count_tokens(text)

        # 写入 token_count.txt，供下游提取 Agent 动态设置 max_tokens
        token_count_path = os.path.join(folder_path, "token_count.txt")
        with open(token_count_path, "w", encoding="utf-8") as f:
            f.write(str(token_count))

        print(f"[OK] {doi_folder}: {token_count} tokens")
        total_token_count += token_count

    total_million = total_token_count / 1_000_000
    print(f"Total token count across all folders: {total_million:.2f} million")
    return total_million
