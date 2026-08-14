"""
litdiscovery/agent/researcher_agent_pipeline/snowball.py — 引用雪球扩展。

把少而精的种子论文扩展为其 references（参考文献）+ citations（引用它的论文）邻居，
得到高度同主题的候选论文池。OpenAlex 为主源（免费无 key），Semantic Scholar 兜底。

候选 schema 与检索结果一致：title / year / venue / doi / citation_count / abstract /
is_open_access / source。
"""

import math
import os
from datetime import datetime

from litdiscovery.config import (
    SNOWBALL_REF_LIMIT,
    SNOWBALL_CIT_LIMIT,
    SEMANTIC_SCHOLAR_API_KEY,
)
from litdiscovery.common.net import _get
from litdiscovery.common.json import clean_text as _clean_text, reconstruct_abstract as _reconstruct_abstract


def _norm_doi(doi: str) -> str:
    """DOI 归一化：去 https://doi.org/ 前缀并小写，用于去重键。"""
    d = (doi or "").strip()
    for prefix in ("https://doi.org/", "http://dx.doi.org/", "https://dx.doi.org/"):
        if d.startswith(prefix):
            d = d[len(prefix):]
    return d.lower()


def _to_record(w: dict) -> dict:
    """OpenAlex work → 统一论文记录。无标题返回 None。"""
    title = _clean_text(w.get("display_name"), 200)
    if not title:
        return None
    loc = w.get("primary_location") or {}
    oa = (w.get("open_access") or {}).get("is_oa", False)
    return {
        "keyword": "",
        "title": title,
        "authors": [],
        "year": w.get("publication_year"),
        "venue": _clean_text((loc.get("source") or {}).get("display_name"), 80),
        "doi": (w.get("doi") or "").replace("https://doi.org/", ""),
        "source": "snowball_openalex",
        "url": w.get("doi") or "",
        "citation_count": w.get("cited_by_count"),
        "abstract": _clean_text(_reconstruct_abstract(w.get("abstract_inverted_index")), 600),
        "is_open_access": oa,
        "is_oa": oa,
        "best_oa_location": loc,
        "oa_locations": w.get("locations") or [],
    }


_SELECT = ("id,display_name,publication_year,doi,cited_by_count,"
           "abstract_inverted_index,primary_location,locations,open_access")


def _openalex_work(doi: str):
    """按 DOI 查 OpenAlex work。返回 (work_dict, openalex_id)。"""
    url = f"https://api.openalex.org/works/https://doi.org/{_norm_doi(doi)}"
    try:
        resp = _get(url, params={"select": "id,referenced_works,cited_by_api_url,title"})
        if resp.status_code == 200:
            w = resp.json()
            if isinstance(w, dict) and w.get("id"):
                return w, w["id"]
    except Exception as e:
        print(f"      [Snowball/OpenAlex] 查 work 失败 {doi}: {type(e).__name__}: {e}")
    return None, None


def _openalex_neighbors(doi: str, kinds=("references", "citations"),
                        oa_only: bool = False,
                        limit_ref: int = None, limit_cit: int = None) -> list:
    """OpenAlex：references（批量按 id 抓取）+ citations（cursor 分页）。"""
    limit_ref = limit_ref if limit_ref is not None else SNOWBALL_REF_LIMIT
    limit_cit = limit_cit if limit_cit is not None else SNOWBALL_CIT_LIMIT
    out = []
    w, aid = _openalex_work(doi)
    if not w or not aid:
        return out

    # ---- references ----
    if "references" in kinds:
        refs = w.get("referenced_works") or []
        if refs:
            ids = [r.rsplit("/", 1)[-1] for r in refs if r]
            got = 0
            for i in range(0, len(ids), 50):
                if got >= limit_ref:
                    break
                chunk = ids[i:i + 50]
                try:
                    resp = _get("https://api.openalex.org/works",
                                params={"filter": "ids.openalex:" + "|".join(chunk),
                                        "per-page": 200, "select": _SELECT})
                    if resp.status_code == 200:
                        for item in resp.json().get("results", []):
                            rec = _to_record(item)
                            if rec and (not oa_only or rec.get("is_open_access")):
                                out.append(rec)
                                got += 1
                except Exception as e:
                    print(f"      [Snowball/OpenAlex] references 失败: {type(e).__name__}: {e}")
                    break
    # ---- citations ----
    if "citations" in kinds:
        cursor, fetched = "*", 0
        while fetched < limit_cit:
            try:
                resp = _get("https://api.openalex.org/works",
                            params={"filter": f"cites:{aid}", "per-page": 100,
                                    "cursor": cursor, "select": _SELECT})
            except Exception as e:
                print(f"      [Snowball/OpenAlex] citations 失败: {type(e).__name__}: {e}")
                break
            if resp.status_code != 200:
                break
            data = resp.json()
            results = data.get("results", [])
            if not results:
                break
            for item in results:
                rec = _to_record(item)
                if rec and (not oa_only or rec.get("is_open_access")):
                    out.append(rec)
                    fetched += 1
                    if fetched >= limit_cit:
                        break
            cursor = (data.get("meta") or {}).get("next_cursor")
            if not cursor:
                break
    return out


def _s2_neighbors(doi: str, kinds=("references", "citations"),
                  oa_only: bool = False, limit: int = 100) -> list:
    """Semantic Scholar：references（citedPaper）/ citations（citingPaper）。限流失败返回空。"""
    token = (os.environ.get("SEMANTIC_SCHOLAR_API_KEY") or SEMANTIC_SCHOLAR_API_KEY or "").strip()
    headers = {"x-api-key": token} if token else {}
    fields = "title,year,venue,abstract,externalIds,citationCount,isOpenAccess"
    out = []
    for kind in kinds:
        if kind == "references":
            url = f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}/references"
            wrap = "citedPaper"
        elif kind == "citations":
            url = f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}/citations"
            wrap = "citingPaper"
        else:
            continue
        try:
            resp = _get(url, params={"fields": fields, "limit": limit}, headers=headers)
            if resp.status_code != 200:
                continue
            data = resp.json().get("data", [])
            for item in data:
                p = item.get(wrap) or {}
                if oa_only and not p.get("isOpenAccess"):
                    continue
                title = _clean_text(p.get("title"), 200)
                if not title:
                    continue
                ext = p.get("externalIds") or {}
                doi2 = ext.get("DOI") or ""
                out.append({
                    "keyword": "",
                    "title": title,
                    "authors": [],
                    "year": p.get("year"),
                    "venue": _clean_text(p.get("venue"), 80),
                    "doi": doi2,
                    "source": "snowball_s2",
                    "url": f"https://doi.org/{doi2}" if doi2 else "",
                    "citation_count": p.get("citationCount"),
                    "abstract": _clean_text(p.get("abstract"), 600),
                    "is_open_access": bool(p.get("isOpenAccess")),
                })
        except Exception:
            continue
    return out


def fetch_neighbors(doi: str, kinds=("references", "citations"), oa_only: bool = False,
                    limit_ref: int = None, limit_cit: int = None) -> list:
    """返回某篇种子论文的邻居候选。

    顺序：OpenAlex 主源 → 为空则 Semantic Scholar 兜底。
    返回记录列表（含 is_open_access / source 标记），调用方负责去重。
    """
    cands = _openalex_neighbors(doi, kinds=kinds, oa_only=oa_only,
                                limit_ref=limit_ref, limit_cit=limit_cit)
    if not cands:
        cands = _s2_neighbors(doi, kinds=kinds, oa_only=oa_only)
    return cands


def dedup_papers(candidates: list, seen_keys=None) -> list:
    """按 DOI 归一化去重；无 DOI 按标题小写。seen_keys 为已有键集合（str）。"""
    seen = set(seen_keys or ())
    out = []
    for p in candidates:
        doi = _norm_doi(p.get("doi"))
        key = doi or (p.get("title") or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def rank_candidates(candidates: list) -> list:
    """启发式排序：引用对数 + 近5年加成。返回按得分降序的列表（不改入参）。"""
    now_year = datetime.now().year

    def _score(p):
        s = 0.0
        cit = p.get("citation_count")
        if cit:
            try:
                s += 1.0 * math.log(int(cit) + 1)
            except (TypeError, ValueError):
                pass
        year = p.get("year")
        if year:
            try:
                if now_year - int(year) <= 5:
                    s += 0.5
            except (TypeError, ValueError):
                pass
        return s

    return sorted(candidates, key=_score, reverse=True)


def rank_by_llm(requirement: str, candidates: list, llm) -> list:
    """单次批量 LLM 调用，给每条候选 0.0~1.0 相关性分，按 (0.7*relevance + 0.3*引用分) 排序。

    失败时回退到启发式排序。返回带 relevance 字段的排序列表。
    """
    if not candidates:
        return candidates
    from langchain_core.messages import SystemMessage, HumanMessage
    lines = [f"科研需求：{requirement}", f"共 {len(candidates)} 条候选（下标从 0 开始）：", ""]
    for i, p in enumerate(candidates):
        title = (p.get("title") or "")[:120]
        year = p.get("year") or "----"
        doi = p.get("doi") or ""
        lines.append(f"[{i}] {title} ({year}) {doi}")
    body = "\n".join(lines)
    system = ("你是材料科学文献筛选专家。给定科研需求与候选文献标题列表，"
              "为每条候选给出 0.0~1.0 的相关性评分（1=高度相关，0=不相关）。"
              '只输出 JSON 对象：{"relevance": {下标: 分数}, "reason": "一句话"}，不要其他内容。')

    rel = {}
    try:
        out = llm.invoke([SystemMessage(content=system), HumanMessage(content=body)])
        text = getattr(out, "content", str(out))
        from litdiscovery.common.json import iter_json_values as _iter_json_values
        for cand in _iter_json_values(text):
            if isinstance(cand, dict) and "relevance" in cand:
                rel = cand.get("relevance") or {}
                break
        for i, p in enumerate(candidates):
            v = rel.get(str(i), rel.get(i, 0.5))
            try:
                p["relevance"] = min(1.0, max(0.0, float(v)))
            except (TypeError, ValueError):
                p["relevance"] = 0.5
    except Exception as e:
        print(f"[Snowball] LLM 相关性排序失败: {type(e).__name__}: {e}")
        for p in candidates:
            p["relevance"] = 0.5

    def _score(p):
        r = p.get("relevance", 0.5)
        cit = p.get("citation_count")
        c = math.log(int(cit) + 1) if cit else 0.0
        return 0.7 * r + 0.3 * min(c / 5.0, 1.0)

    return sorted(candidates, key=_score, reverse=True)
