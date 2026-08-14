"""
litdiscovery/agent/researcher_agent_pipeline/keywords.py — 关键词生成 + 联网前沿检索。

- generate_keywords / parse_keyword_list（researcher_agent 生成检索关键词）
- frontier_search / build_frontier_context（OpenAlex / Semantic Scholar / Tavily 联网前沿检索）
- confirm_keywords（关键词交互确认）
"""

import os
import re
import sys
from datetime import datetime

from langchain_core.messages import SystemMessage, HumanMessage

from litdiscovery.config import (
    create_agent,
    get_agent_role,
    SEARCH_ENABLED,
    SEARCH_QUERY_LIMIT,
    SEARCH_RESULTS_PER_QUERY,
    SEARCH_RECENT_YEARS,
    SEARCH_MAX_TOTAL,
    SEMANTIC_SCHOLAR_API_KEY,
    TAVILY_API_KEY,
    TAVILY_SEARCH_URL,
)
from litdiscovery.common.net import _get
from litdiscovery.common.json import (
    iter_json_values as _iter_json_values,
    clean_text as _clean_text,
    reconstruct_abstract as _reconstruct_abstract,
)


def parse_keyword_list(text: str) -> list:
    """从 LLM 输出中解析出关键词 JSON 数组，兼容代码块标记与额外文字。"""
    s = (text or "").strip()
    s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
    s = re.sub(r"\n?```$", "", s).strip()

    for cand in _iter_json_values(s):
        if isinstance(cand, list):
            kws = [str(x).strip() for x in cand if str(x).strip()]
            if kws:
                return kws
    # 兜底：按逗号/换行拆分
    kws = [x.strip().strip('"\'') for x in re.split(r"[,，\n]+", s) if x.strip().strip('"\'')]
    return kws


def generate_keywords(requirement: str, count: int, use_search: bool = True,
                      context: str = "") -> list:
    """调用 researcher_agent 生成 count 个候选关键词。

    若 use_search 为 True 且配置允许，先生成前沿动态参考（联网检索近期相关研究），
    并注入提示词，让关键词能够有效延伸并跟进前沿领域。
    context: HyDE 拆分出的维度词（逗号分隔），拼进提示词保证维度不丢；
             返回为空时回退到 context 拆分出的词，避免下游检索空转。
    """
    frontier = build_frontier_context(requirement) if use_search else ""
    hyde_hint = f"（HyDE 维度参考：{context}）\n" if context else ""
    llm = create_agent("researcher_agent")
    messages = [
        SystemMessage(content=get_agent_role("researcher_agent")),
        HumanMessage(
            content=(frontier + f"科研需求：{requirement}\n{hyde_hint}"
                     f"请生成 {count} 个用于检索学术论文的关键词。只输出 JSON 数组。")
            if frontier else
            f"科研需求：{requirement}\n{hyde_hint}"
            f"请生成 {count} 个用于检索学术论文的关键词。只输出 JSON 数组。"
        ),
    ]
    resp = llm.invoke(messages)
    text = getattr(resp, "content", str(resp))
    keywords = parse_keyword_list(text)
    if not keywords:
        print(f"[WARN] researcher_agent 返回无法解析的内容：\n{text}")
        keywords = [k.strip() for k in re.split(r"[,，\n]+", context) if k.strip()]
    return keywords


# ============================================================
# researcher_agent 联网搜索：检索"前沿动态"辅助关键词延伸与跟进
# ============================================================
def _search_openalex(query: str, limit: int = 6, recent_years: int = 4) -> list:
    """OpenAlex 检索（免费、无需 key，含摘要）。"""
    params = {
        "search": query,
        "per-page": min(limit * 3, 50),
        "sort": "publication_date:desc",
        "select": "title,publication_date,primary_location,cited_by_count,doi,abstract_inverted_index",
    }
    resp = _get("https://api.openalex.org/works", params=params)
    resp.raise_for_status()
    out = []
    cutoff = datetime.now().year - recent_years
    for w in resp.json().get("results", []):
        date = (w.get("publication_date") or "")[:4]
        if date:
            try:
                year = int(date)
            except ValueError:
                year = None
        else:
            year = None
        if year is not None and year < cutoff:
            continue
        loc = w.get("primary_location") or {}
        venue = _clean_text((loc.get("source") or {}).get("display_name"), 80)
        out.append({
            "title": _clean_text(w.get("title"), 200),
            "year": year,
            "venue": venue,
            "doi": (w.get("doi") or "").replace("https://doi.org/", ""),
            "citations": w.get("cited_by_count"),
            "abstract": _clean_text(_reconstruct_abstract(w.get("abstract_inverted_index")), 600),
        })
        if len(out) >= limit:
            break
    return out


def _search_semantic_scholar(query: str, limit: int = 6, recent_years: int = 4) -> list:
    """Semantic Scholar 检索（无 key 时限流严重，失败返回空列表）。"""
    token = (os.environ.get("SEMANTIC_SCHOLAR_API_KEY") or SEMANTIC_SCHOLAR_API_KEY or "").strip()
    headers = {"x-api-key": token} if token else {}
    params = {
        "query": query,
        "limit": min(limit * 3, 100),
        "fields": "title,year,venue,abstract,citationCount,externalIds",
        "sort": "pubDate:desc",
    }
    resp = _get("https://api.semanticscholar.org/graph/v1/paper/search",
                params=params, headers=headers)
    if resp.status_code == 429:
        # 暂时性限流：等待后重试一次，仍失败则优雅跳过
        print("[WARN] Semantic Scholar 被限流(429)，等待 5s 后重试一次...")
        import time
        time.sleep(5)
        resp = _get("https://api.semanticscholar.org/graph/v1/paper/search",
                    params=params, headers=headers)
        if resp.status_code == 429:
            print("[WARN] Semantic Scholar 仍被限流(429)，跳过该源。")
            return []
    resp.raise_for_status()
    cutoff = datetime.now().year - recent_years
    out = []
    for it in resp.json().get("data", []):
        year = it.get("year")
        if year and year < cutoff:
            continue
        ext = it.get("externalIds") or {}
        out.append({
            "title": _clean_text(it.get("title"), 200),
            "year": year,
            "venue": _clean_text(it.get("venue"), 80),
            "doi": ext.get("DOI") or "",
            "citations": it.get("citationCount"),
            "abstract": _clean_text(it.get("abstract"), 600),
        })
        if len(out) >= limit:
            break
    return out


def _search_tavily(query: str, limit: int = 6, recent_years: int = 4) -> list:
    """Tavily 通用网页搜索（需自行配置 key，未配置则跳过）。"""
    token = (os.environ.get("TAVILY_API_KEY") or TAVILY_API_KEY or "").strip()
    if not token:
        return []
    cutoff = datetime.now().year - recent_years
    payload = {"api_key": token, "query": query, "max_results": min(limit * 3, 20),
               "search_depth": "basic", "include_raw_content": False}
    resp = _get(TAVILY_SEARCH_URL, params=payload, timeout=30)
    if resp.status_code in (401, 403):
        # key 失效 / 无权限：永久性错误，提示更换 key，不抛异常（避免每次运行白耗请求并报错）
        print("[WARN] Tavily API key 失效或被拒绝(401/403)，跳过该源。"
              "请检查/更新 TAVILY_API_KEY（config.py 或环境变量）。")
        return []
    resp.raise_for_status()
    out = []
    for r_ in resp.json().get("results", []):
        title = _clean_text(r_.get("title"), 200)
        if not title:
            continue
        out.append({
            "title": title,
            "year": None,
            "venue": _clean_text(r_.get("domain"), 60),
            "doi": "",
            "citations": None,
            "abstract": _clean_text(r_.get("content"), 600),
        })
        if len(out) >= limit:
            break
    return out


_CN_MATERIALS = {
    "铝": "Aluminum", "氮化铝": "Aluminum nitride", "氮化镓": "gallium nitride",
    "氧化锌": "zinc oxide", "铌酸锂": "lithium niobate", "钛酸钡": "barium titanate",
    "钛酸锶": "strontium titanate", "碳化硅": "silicon carbide", "钙钛矿": "perovskite",
    "氧化物": "oxide", "金属": "metal", "合金": "alloy", "金刚石": "diamond",
    "氮化硼": "boron nitride", "二硫化钼": "MoS2", "碳纳米管": "carbon nanotube",
    "石墨烯": "graphene", "锗": "germanium", "硅": "silicon", "砷化镓": "gallium arsenide",
}
_CN_TERMS = {
    "掺杂": "doping", "薄膜": "thin film", "滤波器": "filter", "谐振器": "resonator",
    "声表面波": "surface acoustic wave", "体声波": "bulk acoustic wave",
    "压电": "piezoelectric", "铁电": "ferroelectric", "沉积": "deposition",
    "溅射": "sputtering", "配方": "composition", "制备": "fabrication",
    "工艺": "process", "材料": "material", "器件": "device", "性能": "performance",
    "生长": "growth", "外延": "epitaxial", "微机电": "MEMS",
    "声学": "acoustic", "射频": "RF", "氮化铝": "AlN",
    "钪": "Scandium", "掺杂剂": "dopant",
}


def _english_queries(requirement: str) -> list:
    """从中文科研需求中提取英文检索式（材料体系 + 关键术语），供学术源使用。"""
    if not re.search(r"[一-鿿]", requirement):
        return []
    tokens = []
    for cn, en in _CN_TERMS.items():
        if cn in requirement:
            tokens.append(en)
    material = next((v for k, v in _CN_MATERIALS.items() if k in requirement), None)
    if material:
        tokens.insert(0, material)
    if not tokens:
        return []
    joined = " ".join(tokens[:4])
    return [joined, joined + " acoustic filter"]


def _derive_search_queries(requirement: str, query_limit: int) -> list:
    """生成用于前沿检索的英文查询（LLM 优先，规则兜底）。

    LLM 生成的查询更贴合科研需求（保留 ScAlN 等专有名词），
    失败时回退到关键词表翻译。返回去重后的查询列表。
    """
    try:
        llm = create_agent("researcher_agent")
        messages = [
            SystemMessage(content="""你是一名科研检索专家。请把下面的科研需求改写为 1~7 个用于学术数据库检索的英文查询式。
要求：每个查询式简短、具体，保留材料体系与专有名词（如 ScAlN、AlN、FBAR），用于查找近期相关研究。
只输出一个 JSON 字符串数组，不要任何解释或代码块标记。"""),
            HumanMessage(content=f"科研需求：{requirement}"),
        ]
        resp = llm.invoke(messages)
        text = getattr(resp, "content", str(resp))
        qs = parse_keyword_list(text)
        if qs:
            return qs[:query_limit]
    except Exception as e:
        print(f"  [WARN] 用 LLM 派生检索查询失败: {type(e).__name__}: {e}")
    return _english_queries(requirement)[:query_limit] or [requirement]


def frontier_search(requirement: str, query_limit: int = None, per_query: int = None,
                    recent_years: int = None, max_total: int = None) -> list:
    """对科研需求做联网前沿检索，返回汇总后的文献条目列表。

    依次调用多个源（OpenAlex / arXiv / Semantic Scholar / Tavily），
    各源独立容错；剔除空标题与重复条目，按"年份+引用"排序后截断。
    """
    q_limit = query_limit if query_limit is not None else SEARCH_QUERY_LIMIT
    per = per_query if per_query is not None else SEARCH_RESULTS_PER_QUERY
    years = recent_years if recent_years is not None else SEARCH_RECENT_YEARS
    total = max_total if max_total is not None else SEARCH_MAX_TOTAL

    # OpenAlex 免费无需 key、覆盖 arXiv 内容且相关性高，作为主源；
    # Semantic Scholar / Tavily 需 key 或易限流，作为可选增强源
    sources = [("OpenAlex", _search_openalex),
               ("SemanticScholar", _search_semantic_scholar),
               ("Tavily", _search_tavily)]

    queries = _derive_search_queries(requirement, q_limit)

    items, seen, src_stats = [], set(), {}
    disabled = set()   # 被限流 / 多次失败即禁用该源，避免无谓重试拖慢流程
    fail_count = {}
    for qi, q in enumerate(queries, 1):
        print(f"[Frontier] 查询 {qi}/{len(queries)}: {q}")
        for name, fn in sources:
            if name in disabled:
                continue
            try:
                res = fn(q, limit=per, recent_years=years)
            except Exception as e:
                print(f"  [WARN] {name} 检索失败: {type(e).__name__}: {e}")
                src_stats[name] = src_stats.get(name, 0)
                fail_count[name] = fail_count.get(name, 0) + 1
                if fail_count[name] >= 2:
                    disabled.add(name)
                continue
            if not res:
                fail_count[name] = fail_count.get(name, 0) + 1
                if fail_count[name] >= 2:
                    disabled.add(name)
                continue
            fail_count[name] = 0
            added = 0
            for it in res:
                key = (it.get("doi") or it.get("title") or "").strip()
                if not key or key in seen:
                    continue
                seen.add(key)
                items.append(it)
                added += 1
            src_stats[name] = src_stats.get(name, 0) + added
    if items:
        print(f"[Frontier] 检索到 {len(items)} 条前沿文献: "
              + " ".join(f"{k}={v}" for k, v in src_stats.items()))
    return items


def build_frontier_context(requirement: str, max_total: int = None) -> str:
    """联网检索前沿动态并格式化为提示词片段；任何失败都返回空串，不影响关键词生成。"""
    if not SEARCH_ENABLED:
        return ""
    try:
        items = frontier_search(requirement, max_total=max_total)
    except Exception as e:
        print(f"[WARN] 联网前沿检索失败: {type(e).__name__}: {e}")
        return ""
    if not items:
        print("[Frontier] 未检索到有效前沿文献，将直接基于需求生成关键词。")
        return ""

    total = max_total if max_total is not None else SEARCH_MAX_TOTAL
    items.sort(key=lambda it: (it.get("year") is None,
                               -(it.get("year") or 0),
                               -(it.get("citations") or 0)))
    items = items[:total]

    lines = ["【前沿动态参考】（联网检索到的近期相关研究，供延伸与跟进关键词使用）", ""]
    for i, it in enumerate(items, 1):
        year = it.get("year") if it.get("year") is not None else "----"
        venue = (it.get("venue") or "")[:40]
        title = (it.get("title") or "")
        abs_ = (it.get("abstract") or "").strip()
        lines.append(f"{i}. {title} ({year}, {venue})")
        if abs_:
            lines.append(f"   摘要: {abs_[:260]}")
    lines.append("")
    return "\n".join(lines)


# 步骤 3（确认步骤 A）：删除 / 添加关键词，回车确认
def confirm_keywords(keywords: list) -> list:
    """交互确认关键词：按编号删除、'+ 关键词' 添加、回车确认。"""
    while True:
        print("\n" + "=" * 66)
        print(f"[关键词确认] 共 {len(keywords)} 个关键词")
        print("-" * 66)
        for i, kw in enumerate(keywords, 1):
            print(f"  {i:>3}. {kw}")
        print("-" * 66)
        inp = input("输入要删除的编号(逗号分隔)；或输入 '+ 新增关键词' 添加；直接回车确认: ").strip()
        if not inp:
            return keywords
        low = inp.lower()
        if low in ("exit", "quit", "q"):
            sys.exit(0)
        if inp.startswith("+"):
            added = [a.strip().strip('"') for a in re.split(r"[,，;；]+", inp[1:]) if a.strip().strip('"')]
            keywords.extend(added)
            for a in added:
                print(f"  [+] 已添加: {a}")
            continue
        nums = {int(t) for t in re.split(r"[,，;；\s]+", inp) if t.strip().isdigit()}
        if nums:
            for idx in sorted((n - 1 for n in nums if 1 <= n <= len(keywords)), reverse=True):
                removed = keywords.pop(idx)
                print(f"  [-] 已删除: {removed}")
