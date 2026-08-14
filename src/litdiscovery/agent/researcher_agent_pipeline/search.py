"""
litdiscovery/agent/researcher_agent_pipeline/search.py — 论文检索。

- search_papers_async：连接 Apify 托管的 Academic Paper Scraper MCP，按关键词检索论文（含摘要）；
  Apify 超配额 / 不可用时自动降级到 search_papers_http（OpenAlex/Crossref 免 key）
- search_papers_http：HTTP 检索替代后端（OpenAlex 主，Crossref 兜底），供配额降级或手动指定
- confirm_papers：检索结果交互确认
- _enrich_doi：手动种子 DOI → 元数据补全（OpenAlex 优先，Crossref 兜底）
"""

import os
import re
import sys
import json
import asyncio

from litdiscovery.common.net import _get
from litdiscovery.common.json import (
    iter_json_values as _iter_json_values,
    clean_text as _clean_text,
    reconstruct_abstract as _reconstruct_abstract,
)

MSG_NO_TOKEN = """\
[ERROR] 未配置 Apify API Key！
调用 Academic Paper Scraper 需要 Apify 凭据：
  1) 注册/登录 https://apify.com
  2) 在 Settings -> Integrations 获取 API Key（形如 apify_api_xxxx）
  3) 设置环境变量: set APIFY_API_KEY=apify_api_xxxx
或仅生成关键词（--keywords-only）跳过检索步骤。"""


def _pick_search_tool(tools):
    """从 MCP 服务器暴露的工具中挑选检索工具（优先名字含 academic/scraper/paper）。"""
    if not tools:
        raise RuntimeError("[MCP] 服务器未返回任何工具")
    ranked = []
    for t in tools:
        name = (t.name or "").lower()
        desc = (t.description or "").lower()
        score = 0
        if "academic" in name or "academic" in desc:
            score += 4
        if "scraper" in name or "scraper" in desc:
            score += 3
        if "paper" in name or "paper" in desc:
            score += 2
        if "search" in name or "search" in desc:
            score += 1
        ranked.append((score, t))
    ranked.sort(key=lambda x: x[0], reverse=True)
    best = ranked[0][1]
    if best.name != ranked[-1][1].name:
        print(f"[MCP] 自动选择工具: {best.name}（可用: {[t.name for t in tools]}）")
    return best


def _build_payload(tool, query: str, max_results: int) -> dict:
    """依据工具 input_schema 构造检索参数，兼容不同命名的 query / limit 字段。"""
    schema = tool.input_schema or {}
    props = schema.get("properties", {}) or {}
    required = set(schema.get("required", []) or [])
    payload = {}

    for mk in ("mode", "searchType", "type"):
        if mk in props:
            payload[mk] = "search"
            break

    qkey = next((k for k in ("query", "q", "searchTerm", "keywords") if k in props), None)
    if qkey:
        is_array = (props.get(qkey) or {}).get("type") == "array"
        payload[qkey] = [query] if is_array else query

    for lk in ("maxResults", "max_results", "limit", "maxItems"):
        if lk in props:
            payload[lk] = max_results
            break

    for field, val in (("includeAbstract", True), ("includeTldr", True),
                       ("includeCitationCounts", True), ("openAccessOnly", False)):
        if field in props:
            payload[field] = val

    # 补齐必填字段的合理默认值
    for r in required - set(payload):
        rp = props.get(r) or {}
        t = rp.get("type")
        if t == "array":
            payload[r] = []
        elif t == "integer":
            payload[r] = max_results
        elif t == "boolean":
            payload[r] = False
        elif t == "string":
            enum = rp.get("enum") or []
            payload[r] = enum[0] if enum else ""
    return payload


def _extract_items(result) -> list:
    """把 MCP 工具调用结果（structuredContent 或文本 JSON）解析为记录列表。"""
    items = []
    sc = getattr(result, "structured_content", None)
    if isinstance(sc, list):
        items.extend(x for x in sc if isinstance(x, dict))
    elif isinstance(sc, dict):
        if isinstance(sc.get("items"), list):
            items.extend(x for x in sc["items"] if isinstance(x, dict))
        elif sc.get("title") or sc.get("doi"):
            items.append(sc)

    text = "".join(getattr(b, "text", "") or "" for b in (result.content or [])
                   if getattr(b, "type", None) == "text")
    for cand in _iter_json_values(text):
        if isinstance(cand, list):
            items.extend(x for x in cand if isinstance(x, dict))
        elif isinstance(cand, dict):
            if isinstance(cand.get("items"), list):
                items.extend(x for x in cand["items"] if isinstance(x, dict))
            elif cand.get("title") or cand.get("doi"):
                items.append(cand)

    seen, out = set(), []
    for it in items:
        key = json.dumps(it, sort_keys=True, ensure_ascii=False)
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def _extract_run_info(result) -> dict:
    """从 Actor 调用结果（run summary）中提取 runId / status / datasetId。"""
    info = {"run_id": None, "status": None, "dataset_id": None}
    sc = getattr(result, "structured_content", None)
    if isinstance(sc, dict):
        info["run_id"] = sc.get("runId")
        info["status"] = sc.get("status")
        info["dataset_id"] = (
            (sc.get("storages") or {}).get("datasets") or {}).get("default", {}).get("id")

    text = "".join(getattr(b, "text", "") or "" for b in (result.content or [])
                   if getattr(b, "type", None) == "text")
    for cand in _iter_json_values(text):
        if not isinstance(cand, dict):
            continue
        info["run_id"] = info["run_id"] or cand.get("runId")
        info["status"] = info["status"] or cand.get("status")
        default_ds = (cand.get("storages") or {}).get("datasets", {}).get("default", {})
        info["dataset_id"] = info["dataset_id"] or default_ds.get("id")
    return info


async def _wait_for_run(session, get_run_tool, run_id, timeout_sec: int = 300) -> str:
    """轮询 get-actor-run 直到 actor 运行完成，返回最终 status。"""
    if not get_run_tool or not run_id:
        return "SUCCEEDED"
    elapsed = 0
    while elapsed < timeout_sec:
        try:
            r = await session.call_tool(
                get_run_tool.name, arguments={"runId": run_id, "waitSecs": 10}
            )
        except Exception as e:
            print(f"[WARN] get-actor-run 轮询失败: {type(e).__name__}: {e}")
            return "SUCCEEDED"
        status = _extract_run_info(r)["status"]
        if status and status not in ("RUNNING", "STARTING", "READY"):
            return status
        await asyncio.sleep(2)
        elapsed += 12
    return "TIMEOUT"


def _normalize_paper(rec: dict, keyword: str) -> dict:
    """把检索记录规整为统一字段结构。"""
    title = (rec.get("title") or "").strip()
    doi = (rec.get("doi") or "").strip()
    if not title and not doi:
        return None
    authors = rec.get("authors") or []
    if isinstance(authors, str):
        authors = [a.strip() for a in authors.split(",") if a.strip()]
    ext = rec.get("external_urls") or {}
    url = ""
    if isinstance(ext, dict):
        url = ext.get("doi_url") or ext.get("url") or ""
    url = url or (rec.get("url") or "")
    if not url and doi:
        url = f"https://doi.org/{doi}"

    # 摘要：Academic Paper Scraper 在 includeAbstract/includeTldr 时返回 abstract/tldr，
    # 摘要缺失时回退到 TLDR 一句话概括
    abstract = rec.get("abstract") or rec.get("abstract_text") or ""
    if isinstance(abstract, str):
        abstract = abstract.strip()
    tldr = rec.get("tldr") or ""
    if isinstance(tldr, dict):
        tldr = tldr.get("text") or ""
    if isinstance(tldr, str):
        tldr = tldr.strip()
    if not abstract and tldr:
        abstract = tldr

    return {
        "keyword": keyword,
        "title": title,
        "authors": authors,
        "year": rec.get("year"),
        "venue": rec.get("venue") or rec.get("journal") or "",
        "doi": doi,
        "source": rec.get("source") or "",
        "url": url,
        "citation_count": rec.get("citation_count"),
        "abstract": abstract,
    }


_QUOTA_HINTS = ("quota", "limit", "rate", "429", "payment", "billing",
                "credit", "insufficient")


def _is_quota_error(exc) -> bool:
    """判断异常是否与 API 限额/额度相关（触发时降级到 HTTP 检索）。"""
    msg = f"{type(exc).__name__}: {exc}".lower()
    return any(h in msg for h in _QUOTA_HINTS)


def _fallback_http(keywords: list, max_results: int, audit_sink: dict | None = None) -> list:
    """Apify 不可用/超配额时的 HTTP 降级检索。"""
    print("\n[Fallback] Academic Paper Scraper 不可用，切换到 OpenAlex/Crossref HTTP 检索 ...")
    return search_papers_http(keywords, max_results, audit_sink=audit_sink)


async def search_papers_async(keywords: list, max_results: int, tool_name: str = None,
                              audit_sink: dict | None = None) -> list:
    """连接 Academic Paper Scraper MCP 端点，对每个关键词检索论文并去重。

    Apify 请求超配额 / 不可用时自动降级到 OpenAlex/Crossref HTTP 检索（免 key）。
    配额信号可能是抛出的异常，也可能是服务器 is_error 结果文本（如 Monthly
    usage hard limit exceeded）——两者都触发降级，不再让流水线空转。

    注意：配额信号用元组返回（而非抛异常），避免穿过 MCP 的 streamable_http_client
    上下文管理器时被包装成 ExceptionGroup 导致降级崩溃。
    """

    async def _search() -> tuple:
        """在 MCP 会话内检索，返回 (papers, quota)。quota=True 表示需 HTTP 降级。"""
        papers, seen = [], set()
        async with streamable_http_client(url, http_client=http_client) as (read, write):
            async with ClientSession(read, write) as session:
                try:
                    await session.initialize()
                    tools_result = await session.list_tools()
                    tools = tools_result.tools
                except Exception as e:
                    if _is_quota_error(e):
                        print(f"[WARN] Apify 连接被拒（疑似配额耗尽）: {e}")
                        return [], True
                    raise
                tool = next((t for t in tools if t.name == tool_name), None) if tool_name else None
                tool = tool or _pick_search_tool(tools)
                # 配套工具：Actor 运行查询（轮询用）+ 数据集取数（取论文条目用）
                get_run_tool = next((t for t in tools if t.name == "get-actor-run"), None)
                get_items_tool = next((t for t in tools if t.name == "get-dataset-items"), None)
                print(f"[MCP] 已连接 Academic Paper Scraper，使用工具: {tool.name}")

                for kw in keywords:
                    print(f"[Search] 关键词 [{kw}] -> 检索 {max_results} 篇 ...", flush=True)
                    try:
                        result = await session.call_tool(
                            tool.name, arguments=_build_payload(tool, kw, max_results)
                        )
                    except Exception as e:
                        if _is_quota_error(e):
                            print(f"[WARN] 检索被拒（疑似配额耗尽）: {e}")
                            return papers, True
                        print(f"[WARN] 检索失败 ({kw}): {type(e).__name__}: {e}")
                        continue
                    if getattr(result, "is_error", False):
                        text = "".join(getattr(b, "text", "") or "" for b in (result.content or []))
                        print(f"[WARN] 服务器返回错误 ({kw}): {text[:200]}")
                        if _is_quota_error(text):
                            print("[WARN] 服务器错误疑似配额耗尽，触发 HTTP 降级。")
                            return papers, True
                        continue

                    # 两步式：actor 返回 run 摘要，需轮询运行完成后用 get-dataset-items 取数
                    info = _extract_run_info(result)
                    if info.get("run_id") and info.get("dataset_id"):
                        status = await _wait_for_run(session, get_run_tool, info["run_id"])
                        if status not in ("SUCCEEDED", "TIMEOUT"):
                            print(f"[WARN] 运行未成功 ({kw}): status={status}")
                            continue
                        try:
                            items_result = await session.call_tool(
                                get_items_tool.name,
                                arguments={"datasetId": info["dataset_id"], "limit": max_results},
                            )
                        except Exception as e:
                            print(f"[WARN] 获取数据集失败 ({kw}): {type(e).__name__}: {e}")
                            continue
                    else:
                        items_result = result

                    raw_items = _extract_items(items_result)
                    before = len(papers)
                    for rec in raw_items:
                        paper = _normalize_paper(rec, kw)
                        if not paper:
                            continue
                        key = paper["doi"] or paper["title"]
                        if key and key in seen:
                            continue
                        seen.add(key)
                        papers.append(paper)
                    if audit_sink is not None:
                        audit_sink[kw] = {"raw_hits": len(raw_items),
                                          "deduplicated_hits": len(papers) - before,
                                          "source": "apify"}
                return papers, False

    from litdiscovery.config import APIFY_API_KEY, DEFAULT_APIFY_MCP_URL

    token = (os.environ.get("APIFY_API_KEY") or APIFY_API_KEY or "").strip()
    if not token:
        print("[WARN] 未配置 APIFY_API_KEY，降级到 OpenAlex/Crossref HTTP 检索。")
        return _fallback_http(keywords, max_results, audit_sink)

    url = (os.environ.get("APIFY_MCP_URL") or DEFAULT_APIFY_MCP_URL).strip()
    headers = {"Authorization": f"Bearer {token}"}
    import httpx2
    from mcp import ClientSession
    from mcp.client.streamable_http import (
        create_mcp_http_client,
        streamable_http_client,
    )
    timeout = httpx2.Timeout(connect=30.0, read=600.0, write=120.0, pool=60.0)
    http_client = create_mcp_http_client(headers=headers, timeout=timeout)

    try:
        papers, quota = await _search()
    finally:
        await http_client.aclose()
    if quota:
        return _fallback_http(keywords, max_results, audit_sink)
    return papers


def _search_openalex_query(query: str, max_results: int, recent_years: int = 0) -> list:
    """OpenAlex 关键词检索（免费无 key，带摘要）。返回统一 paper 字段列表。"""
    params = {
        "search": query,
        "per-page": min(max(max_results, 10), 100),
        "select": "title,publication_year,primary_location,locations,open_access,cited_by_count,doi,abstract_inverted_index",
    }
    if recent_years:
        from datetime import datetime
        cutoff = datetime.now().year - recent_years
        params["filter"] = f"from_publication_date:{cutoff}-01-01"
    resp = _get("https://api.openalex.org/works", params=params)
    resp.raise_for_status()
    out = []
    for w in resp.json().get("results", []):
        title = _clean_text(w.get("title"), 200)
        if not title:
            continue
        loc = w.get("primary_location") or {}
        venue = _clean_text((loc.get("source") or {}).get("display_name"), 80)
        year = w.get("publication_year")
        doi = (w.get("doi") or "").replace("https://doi.org/", "")
        out.append({
            "keyword": query,
            "title": title,
            "authors": [],
            "year": year,
            "venue": venue,
            "doi": doi,
            "source": "openalex",
            "url": f"https://doi.org/{doi}" if doi else "",
            "citation_count": w.get("cited_by_count"),
            "abstract": _clean_text(_reconstruct_abstract(w.get("abstract_inverted_index")), 600),
            "is_oa": bool((w.get("open_access") or {}).get("is_oa")),
            "best_oa_location": loc,
            "oa_locations": w.get("locations") or [],
        })
    return out


def _search_crossref_query(query: str, max_results: int) -> list:
    """Crossref 关键词检索（免费，polite pool）。返回统一 paper 字段列表。"""
    params = {
        "query": query,
        "rows": min(max(max_results, 10), 100),
        "select": "DOI,title,container-title,issued,author,abstract,is-referenced-by-count",
    }
    resp = _get("https://api.crossref.org/works", params=params)
    resp.raise_for_status()
    out = []
    for it in resp.json().get("message", {}).get("items", []):
        title = _clean_text((it.get("title") or [""])[0], 200)
        if not title:
            continue
        doi = it.get("DOI") or ""
        issued = (it.get("issued") or {}).get("date-parts", [[None]])[0][0]
        out.append({
            "keyword": query,
            "title": title,
            "authors": [],
            "year": issued,
            "venue": _clean_text((it.get("container-title") or [""])[0], 80),
            "doi": doi,
            "source": "crossref",
            "url": f"https://doi.org/{doi}" if doi else "",
            "citation_count": it.get("is-referenced-by-count"),
            "abstract": _clean_text(it.get("abstract") or "", 600),
        })
    return out


def search_papers_http(keywords: list, max_results: int, recent_years: int = 0,
                       audit_sink: dict | None = None) -> list:
    """HTTP 检索替代后端：OpenAlex 主，Crossref 兜底（均免 key）。

    返回与 search_papers_async 相同的统一 paper 字段列表，供 Apify 限额时降级使用。
    """
    papers, seen, used = [], set(), None
    for kw in keywords:
        print(f"[Search] 关键词 [{kw}] -> OpenAlex 检索 {max_results} 篇 ...", flush=True)
        try:
            recs = _search_openalex_query(kw, max_results, recent_years)
            used = "openalex"
        except Exception as e:
            print(f"[WARN] OpenAlex 检索失败 ({kw}): {type(e).__name__}: {e}，回退 Crossref")
            try:
                recs = _search_crossref_query(kw, max_results)
                used = "crossref"
            except Exception as e2:
                print(f"[WARN] Crossref 检索也失败 ({kw}): {type(e2).__name__}: {e2}")
                continue
        before = len(papers)
        for rec in recs:
            key = rec["doi"] or rec["title"]
            if key and key in seen:
                continue
            seen.add(key)
            papers.append(rec)
        if audit_sink is not None:
            audit_sink[kw] = {"raw_hits": len(recs),
                              "deduplicated_hits": len(papers) - before,
                              "source": used or "unknown"}
    print(f"[Search] HTTP 检索完成: 来自 {used or '无可用源'}，共 {len(papers)} 篇")
    return papers


def confirm_papers(papers: list) -> list:
    """交互确认检索结果：按编号删除、'+ DOI' 手动添加、回车确认。"""
    while True:
        print("\n" + "=" * 66)
        print(f"[结果确认] 共 {len(papers)} 篇论文")
        print("-" * 66)
        for i, p in enumerate(papers, 1):
            year = p["year"] if p["year"] is not None else "----"
            doi = p["doi"] or "无DOI"
            title = (p["title"] or "(无标题)")[:78]
            print(f"  {i:>3}. {title} ({year})  {doi}")
        print("-" * 66)
        inp = input("输入要删除的编号(逗号分隔)；或输入 '+ DOI' 手动添加；直接回车确认: ").strip()
        if not inp:
            return papers
        low = inp.lower()
        if low in ("exit", "quit", "q"):
            sys.exit(0)
        if inp.startswith("+"):
            doi = inp[1:].strip()
            if doi:
                papers.append({
                    "keyword": "", "title": f"(手动添加) {doi}", "authors": [],
                    "year": None, "venue": "", "doi": doi,
                    "source": "manual", "url": f"https://doi.org/{doi}",
                    "citation_count": None, "abstract": "",
                })
                print(f"  [+] 已添加: {doi}")
            continue
        nums = {int(t) for t in re.split(r"[,，;；\s]+", inp) if t.strip().isdigit()}
        if nums:
            for idx in sorted((n - 1 for n in nums if 1 <= n <= len(papers)), reverse=True):
                removed = papers.pop(idx)
                print(f"  [-] 已删除: {(removed['title'] or '')[:60]}")


def _enrich_doi(doi: str) -> dict:
    """手动种子 DOI → 元数据补全（OpenAlex 优先，Crossref 兜底，再兜底最小记录）。"""
    doi = doi.strip()
    if not doi:
        return None
    # OpenAlex
    try:
        resp = _get(
            f"https://api.openalex.org/works/https://doi.org/{doi}",
            params={"select": "title,publication_year,primary_location,cited_by_count,"
                              "locations,open_access,doi,abstract_inverted_index"},
        )
        if resp.status_code == 200:
            w = resp.json()
            if isinstance(w, dict) and w.get("title"):
                loc = w.get("primary_location") or {}
                return {
                    "keyword": "",
                    "title": _clean_text(w.get("title"), 200),
                    "authors": [],
                    "year": w.get("publication_year"),
                    "venue": _clean_text((loc.get("source") or {}).get("display_name"), 80),
                    "doi": (w.get("doi") or "").replace("https://doi.org/", ""),
                    "source": "manual_seed",
                    "url": f"https://doi.org/{doi}",
                    "citation_count": w.get("cited_by_count"),
                    "abstract": _clean_text(_reconstruct_abstract(w.get("abstract_inverted_index")), 600),
                    "is_oa": bool((w.get("open_access") or {}).get("is_oa")),
                    "best_oa_location": loc,
                    "oa_locations": w.get("locations") or [],
                }
    except Exception:
        pass
    # Crossref 兜底
    try:
        resp = _get(f"https://api.crossref.org/works/{doi}")
        if resp.status_code == 200:
            m = resp.json().get("message", {})
            title = (m.get("title") or [""])[0]
            if title:
                issued = (m.get("issued") or {}).get("date-parts", [[None]])[0][0]
                return {
                    "keyword": "", "title": title[:200], "authors": [],
                    "year": issued,
                    "venue": (m.get("container-title") or [""])[0][:80],
                    "doi": doi, "source": "manual_seed",
                    "url": f"https://doi.org/{doi}",
                    "citation_count": m.get("is-referenced-by-count"),
                    "abstract": _clean_text(m.get("abstract") or "", 600),
                }
    except Exception:
        pass
    # 最小记录兜底
    return {
        "keyword": "", "title": f"(手动添加) {doi}", "authors": [], "year": None,
        "venue": "", "doi": doi, "source": "manual_seed",
        "url": f"https://doi.org/{doi}", "citation_count": None, "abstract": "",
    }
