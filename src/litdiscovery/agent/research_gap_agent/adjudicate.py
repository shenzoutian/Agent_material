"""
裁决层：批量把检测候选喂聚合 LLM，排除假阳性，输出结构化 verdict。

假阳性来源：单位/温度/掺杂差异、材料名称未对齐、摘要概念过泛。
每个候选带紧凑 evidence（doi + material_family + material_raw + 属性/值/温度 + ≤200 字片段 + source），
LLM 判定是否是真 gap。
"""

import json
from typing import List

from litdiscovery.llm_utils import robust_json_parse, invoke_messages


BATCH_SIZE = 20

_SYSTEM = """你是一个材料科学 research-gap 判定助手。给定一批由自动化检测器产生的候选 gap，
逐一判断是否是真 gap（值得研究/值得注意的空白），排除假阳性。

假阳性判别要点：
- 单位/温度/掺杂差异造成的"矛盾"不是真矛盾（值在不同条件下可比性需明确）
- 材料名称未对齐（如 ScAlN 与 Sc0.3Al0.7N 是同一材料）导致的"缺失"或"矛盾"应纠正而非判定为新发现
- 摘要概念过泛（如"film"、"deposition"）不构成有价值的 gap
- "材料已被研究但未报道某属性"仅在材料确实相关且该属性合理可测时接受

返回 JSON 数组，每项对应一个候选：
{"id": "候选id", "accept": true|false, "reason": "一句话理由",
 "refined_statement": "修正后的 gap 陈述（不接受则留空）",
 "evidence_doi": ["保留的 DOI 列表"], "confidence": "high|medium|low"}
只返回 JSON，不要其他内容。"""


def _fmt_candidate(c: dict) -> str:
    """把候选压缩为紧凑文本。"""
    lines = [f"[{c['id']}] type={c['type']} statement={c['statement']}"]
    for i, ev in enumerate(c.get("evidence", [])[:6]):
        lines.append(f"  ev{i}: doi={ev.get('doi')} detail={str(ev.get('detail'))[:120]}")
    return "\n".join(lines)


def adjudicate(candidates: List[dict], llm, *, batch_size=BATCH_SIZE) -> List[dict]:
    """分批裁决全部候选，返回合并后的 verdict 列表（含 reject 信息）。"""
    if not candidates:
        return []
    verdicts = []
    for i in range(0, len(candidates), batch_size):
        batch = candidates[i:i + batch_size]
        body = "\n\n".join(_fmt_candidate(c) for c in batch)
        user = f"候选 gap 列表：\n{body}"
        try:
            out = invoke_messages(llm, _SYSTEM, user)
            data = robust_json_parse(out.content)
            if not isinstance(data, list):
                data = [data]
        except Exception as e:
            print(f"[Gap] adjudicate batch failed: {e}")
            data = []
        by_id = {c["id"]: c for c in batch}
        for v in data:
            if not isinstance(v, dict):
                continue
            vid = v.get("id")
            if vid in by_id:
                verdicts.append({
                    "id": vid,
                    "accept": bool(v.get("accept")),
                    "reason": v.get("reason", ""),
                    "refined_statement": v.get("refined_statement", ""),
                    "evidence_doi": v.get("evidence_doi", []),
                    "confidence": v.get("confidence", "low"),
                    "candidate": by_id[vid],
                })
        # 未返回的候选视为 reject（保守）
        returned = {v["id"] for v in data if isinstance(v, dict) and v.get("id")}
        for c in batch:
            if c["id"] not in returned:
                verdicts.append({
                    "id": c["id"], "accept": False, "reason": "未获裁决（默认拒绝）",
                    "refined_statement": "", "evidence_doi": [],
                    "confidence": "low", "candidate": c,
                })
    return verdicts
