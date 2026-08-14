"""
litdiscovery/agent/filter_agent_pipeline/choose.py — filter_agent 文献取舍。

- select_papers：调用 filter_agent 角色依据科研需求对文献取舍（保留 ≥ min_keep 篇）
- save_choose_results / append_choose_summary：落盘取舍结果
"""

import json
import re

from langchain_core.messages import SystemMessage, HumanMessage

from litdiscovery.config import (
    create_agent, get_agent_role, DEEPSEEK_MODEL, QUALITY_FLOOR_DEFAULT,
)
from litdiscovery.common.json import iter_json_values as _iter_json_values
from litdiscovery.agent.filter_agent_pipeline.quality import (
    balanced_quality_fill, build_corpus_audit, deduplicate_papers, quality_assessment,
)


def _normalize_indices(nums, n: int) -> list:
    """把解析出的下标规整为合法、去重、保序的索引列表。"""
    out = []
    for x in nums:
        try:
            idx = int(x)
        except (TypeError, ValueError):
            continue
        if 0 <= idx < n and idx not in out:
            out.append(idx)
    return out


def _parse_choose_response(text: str, n: int):
    """解析 filter_agent 输出，返回 (kept_indices, reason)。"""
    s = (text or "").strip()
    s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
    s = re.sub(r"\n?```$", "", s).strip()
    reason = ""
    for cand in _iter_json_values(s):
        if isinstance(cand, dict):
            reason = reason or str(cand.get("reason") or "").strip()
            for key in ("kept_indices", "kept", "keep_indices", "indices", "keep"):
                idxs = cand.get(key)
                if isinstance(idxs, list):
                    nums = [x.get("index") if isinstance(x, dict) else x for x in idxs]
                    out = _normalize_indices(nums, n)
                    if out:
                        return out, reason
        elif isinstance(cand, list):
            if all(isinstance(x, (int, float)) or
                   (isinstance(x, str) and x.strip().lstrip("-").isdigit()) for x in cand):
                out = _normalize_indices(cand, n)
                if out:
                    return out, reason
            dicts = [x for x in cand if isinstance(x, dict)]
            if dicts and all("index" in x for x in dicts):
                out = _normalize_indices([x["index"] for x in dicts], n)
                if out:
                    return out, reason
    # 兜底：从文本里提取数字作为下标
    return _normalize_indices(re.findall(r"\d+", s), n), reason


def select_papers(requirement: str, papers: list, min_keep: int = 70,
                  max_abstract_len: int = 600, quality_floor: float = QUALITY_FLOOR_DEFAULT) -> tuple:
    """调用 filter_agent 角色，依据科研需求对检索文献进行取舍。

    返回:
        (selected, reason): 保留的文献列表 + 取舍理由
    """
    if not papers:
        return [], ""
    papers, duplicates = deduplicate_papers(papers)
    llm = create_agent("filter_agent")
    lines = [f"科研需求：{requirement}", "", f"共 {len(papers)} 篇候选文献（下标从 0 开始）：", ""]
    for i, p in enumerate(papers):
        title = (p.get("title") or "(无标题)")[:160]
        year = p.get("year") if p.get("year") is not None else "----"
        venue = (p.get("venue") or "")[:60]
        doi = p.get("doi") or ""
        cites = p.get("citation_count") if p.get("citation_count") is not None else "N/A"
        abstract = (p.get("abstract") or "").strip()
        if max_abstract_len and len(abstract) > max_abstract_len:
            abstract = abstract[:max_abstract_len] + " ……"
        lines.append(f"[{i}] {title} ({year})")
        lines.append(f"    期刊: {venue} | DOI: {doi} | 引用: {cites}")
        if abstract:
            lines.append(f"    摘要: {abstract}")

    print(f"[Choose] 调用 filter_agent（{DEEPSEEK_MODEL}）依据科研需求进行文献取舍 ...")
    messages = [
        SystemMessage(content=get_agent_role("filter_agent")),
        HumanMessage(content="\n".join(lines)),
    ]
    resp = llm.invoke(messages)
    text = getattr(resp, "content", str(resp))
    indices, reason = _parse_choose_response(text, len(papers))
    uncertain = set(_parse_uncertain_indices(text, len(papers)))
    indices = list(dict.fromkeys([*indices, *sorted(uncertain)]))

    seen, selected = set(), []
    for i in indices:
        p = papers[i]
        key = p.get("doi") or p.get("title")
        if key in seen:
            continue
        seen.add(key)
        selected.append(p)
        selected[-1] = {**selected[-1], "quality": quality_assessment(p, requirement),
                        "selection_reason": ("llm_uncertain" if i in uncertain else "llm_selected")}

    if not selected:
        print("[WARN] filter_agent 未能解析出有效保留下标，将保留全部文献。")
        return list(papers), reason

    # Recall floor: fill by metadata/relevance quality and source/year diversity.
    if len(selected) < min_keep:
        orig = len(selected)
        selected = balanced_quality_fill(selected, papers, requirement, min_keep, quality_floor)
        print(f"[WARN] filter_agent 仅保留 {orig} 篇（低于 {min_keep}），"
              f"已按质量与来源/年代多样性补齐至 {len(selected)} 篇。")
    for paper in selected:
        paper.setdefault("_retrieval_duplicates_removed", len(duplicates))
    return selected, reason


def _parse_uncertain_indices(text: str, n: int) -> list[int]:
    """Read optional uncertain_indices without changing the legacy parser contract."""
    try:
        cleaned = str(text).strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        payload = json.loads(cleaned)
        return _normalize_indices(payload.get("uncertain_indices", []), n)
    except (ValueError, TypeError, AttributeError):
        return []


def save_choose_results(selected: list, reason: str, log_dir, min_keep: int = 20,
                        requirement: str = ""):
    """保存 filter_agent 取舍结果：orders/doi_choose_results.json + 更新 orders/doi_list.txt。"""
    from litdiscovery.config import DOI_LIST_FILE
    from litdiscovery.paths import handoff_path

    choose_path = handoff_path(log_dir, "doi_choose_results.json")
    audit_path = handoff_path(log_dir, "corpus_quality.json")
    doi_list_path = handoff_path(log_dir, DOI_LIST_FILE)
    for p in (choose_path, doi_list_path):
        p.parent.mkdir(parents=True, exist_ok=True)
    choose_path.write_text(json.dumps(selected, ensure_ascii=False, indent=2), encoding="utf-8")
    audit = build_corpus_audit(selected, requirement)
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    uncertain_path = handoff_path(log_dir, "uncertain_review.json")
    uncertain_path.write_text(json.dumps({
        "schema_version": 1, "status": "pending_review",
        "papers": [p for p in selected if p.get("selection_reason") == "llm_uncertain"],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    query_audit_path = handoff_path(log_dir, "query_audit.json")
    if query_audit_path.exists():
        query_audit = json.loads(query_audit_path.read_text(encoding="utf-8"))
        for item in query_audit.get("queries", []):
            item["final_included"] = sum(item["query"] == p.get("keyword") for p in selected)
        query_audit_path.write_text(json.dumps(query_audit, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    from litdiscovery.paths import KNOWLEDGE_ROOT
    from litdiscovery.agent.filter_agent_pipeline.quality import update_cumulative_catalog
    update_cumulative_catalog(selected, KNOWLEDGE_ROOT / "paper_catalog.json",
                              batch=getattr(log_dir, "name", ""))

    dois = list(dict.fromkeys((p.get("doi") or "").strip() for p in selected))
    dois = [d for d in dois if d]
    doi_list_path.write_text("\n".join(dois) + ("\n" if dois else ""), encoding="utf-8")

    print("\n" + "=" * 66)
    print(f"[Choose] filter_agent 取舍完成：保留 {len(selected)} 篇（要求 ≥ {min_keep}），DOI {len(dois)} 个")
    if reason:
        print(f"[Choose] 取舍理由: {reason}")
    print(f"[Output] {choose_path}（取舍后保留的文献 JSON，含摘要）")
    print(f"[Output] {audit_path}（语料质量、来源和年代审计）")
    print(f"[Output] {doi_list_path}（已更新为取舍后的 DOI 列表，供下载阶段使用）")
    print("=" * 66)


def append_choose_summary(log_dir, requirement: str, selected: list,
                          reason: str, min_keep: int = 20):
    """在 result_log.txt 末尾追加 filter_agent 取舍结果摘要。"""
    log_path = log_dir / "result_log.txt"
    lines = [
        "",
        "=" * 66,
        "[filter_agent 取舍摘要]",
        f"[科研需求] {requirement}",
        f"[保留文献] {len(selected)} 篇（要求 ≥ {min_keep} 篇）",
        f"[取舍理由] {reason or '未提供'}",
    ]
    for i, p in enumerate(selected, 1):
        doi = p.get("doi") or "无DOI"
        title = (p.get("title") or "(无标题)")[:70]
        lines.append(f"    {i:>3}. {title}  {doi}")
    lines.append("=" * 66)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
