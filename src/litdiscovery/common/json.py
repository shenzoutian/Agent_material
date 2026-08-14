"""
common/json.py —— LLM 输出容错 JSON 提取 / 摘要重建 / 字段清洗。

"""

import json


def iter_json_values(text: str):
    """从文本中迭代提取合法的 JSON 值（先整段，再逐个括号片段）。"""
    try:
        yield json.loads(text)
        return
    except Exception:
        pass
    i, n = 0, len(text)
    while i < n:
        if text[i] in "[{":
            depth, j, in_str, esc = 0, i, False, False
            while j < n:
                c = text[j]
                if in_str:
                    if esc:
                        esc = False
                    elif c == "\\":
                        esc = True
                    elif c == '"':
                        in_str = False
                else:
                    if c == '"':
                        in_str = True
                    elif c in "[{":
                        depth += 1
                    elif c in "]}":
                        depth -= 1
                        if depth == 0:
                            break
                j += 1
            if depth == 0:
                frag = text[i:j + 1]
                try:
                    val = json.loads(frag)
                    if isinstance(val, (list, dict)):
                        yield val
                except Exception:
                    pass
            i = j + 1
        else:
            i += 1


def reconstruct_abstract(abstract_inverted_index) -> str:
    """把 OpenAlex 的 abstract_inverted_index 还原为摘要文本。"""
    if not isinstance(abstract_inverted_index, dict):
        return ""
    pos = {}
    for word, idxs in abstract_inverted_index.items():
        for i in idxs:
            pos[i] = word
    if not pos:
        return ""
    return " ".join(pos[i] for i in sorted(pos))


def clean_text(value, max_len: int = 500) -> str:
    """把任意字段清理为截断后的纯文本。"""
    if value is None:
        return ""
    if isinstance(value, list):
        parts = []
        for v in value:
            if isinstance(v, dict):
                parts.append(v.get("display_name") or v.get("name") or "")
            elif isinstance(v, str):
                parts.append(v)
        value = ", ".join(p for p in parts if p)
    elif isinstance(value, dict):
        value = value.get("display_name") or value.get("title") or value.get("text") or ""
    s = str(value).replace("\n", " ").replace("\r", " ").strip()
    return s[:max_len]
