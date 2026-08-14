"""
litdiscovery/agent/agent_roles/tools.py —— executor 确定性工具集（runbook / plan 展开调用）。

每个阶段能力暴露为独立 @tool，由 executor（agent/orchestrator/pipeline.py）按
runbook steps / plan_to_runbook 展开的步骤模板逐工具确定性调用；
list_roles 仅用于 CLI 查看工具菜单。

设计原则：
- 工具间只传"路径 + 摘要"，不传大 JSON（单篇全文可 >5MB）——每个工具自己读文件、
  自己落盘，返回批次路径与计数，executor 只需记住路径链。
- 稳定注册表只包含已实现能力，未实现工具不得注册。
- 复合预设 extract_batch 保留"全批次一键提取"，与角色级工具混用。
"""

import json
from pathlib import Path

from langchain_core.tools import tool

from litdiscovery.config import (
    create_agent, FULLTEXT_CONCURRENCY, MIN_FULLTEXT_USABLE_RATE, QUALITY_FLOOR_DEFAULT,
)
from litdiscovery.paths import (
    resolve_batch, data_doi_dir, handoff_path, read_handoff, batch_of,
)
from litdiscovery.agent.agent_roles.registry import (
    format_role_menu,
)


# 内部小工具
def _read_json(path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _fmt(note: str, batch: Path) -> str:
    return f"{note}\n批次目录: {batch}"



# 元工具：list_roles（角色菜单，纯逻辑在 roles/registry.py）
@tool
def list_roles(query: str = "") -> str:
    """列出全部科研 agent 角色及各自可用的能力工具（角色菜单）。

    参数:
        query: 可选关键词筛选（如 'extract' / 'validate'），空串列出全部。
    返回:
        角色名 | 描述 | 可用工具列表。
    """
    return format_role_menu(query)



# 检索链（researcher_agent / filter_agent 角色）
@tool
def generate_keywords(requirement: str, count: int = 7, use_search: bool = True,
                      context: str = "") -> str:
    """[researcher_agent] 生成检索关键词。

    参数:
        requirement: 科研需求描述（必填）
        count: 关键词数量（默认 7）
        use_search: 是否联网前沿检索补充热点术语（默认 True）
        context: HyDE 维度词（逗号分隔），拼入提示词保证维度不丢
    返回: 逗号分隔的关键词列表。
    """
    from litdiscovery.agent.researcher_agent_pipeline.keywords import generate_keywords as _gen
    kws = _gen(requirement, count, use_search=use_search, context=context)
    return ", ".join(kws)


@tool
def search_papers(requirement: str, keywords: str, results_per_keyword: int = 20,
                  batch: str = "") -> str:
    """[researcher_agent] 按关键词检索论文（Apify MCP），结果写 <batch>/orders/search_results.json。

    参数:
        requirement: 科研需求（供检索上下文）
        keywords: 逗号分隔的关键词列表
        results_per_keyword: 每关键词结果上限（默认 20）
        batch: 批次目录（留空自动定位最新）
    返回: 检索结果路径与数量。
    """
    from litdiscovery.agent.researcher_agent_pipeline.search import search_papers_async
    b = resolve_batch(batch or None)
    kw_list = [k.strip() for k in keywords.split(",") if k.strip()]
    import asyncio
    query_stats = {}
    papers = asyncio.run(search_papers_async(kw_list, results_per_keyword,
                                             audit_sink=query_stats))
    out = handoff_path(b, "search_results.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(papers, ensure_ascii=False, indent=2), encoding="utf-8")
    audit = []
    for query in kw_list:
        hits = [p for p in papers if p.get("keyword") == query]
        stats = query_stats.get(query, {})
        audit.append({"query": query,
                      "sources": sorted({p.get("source") or "unknown" for p in hits}),
                      "requested": results_per_keyword,
                      "raw_hits": stats.get("raw_hits", len(hits)),
                      "deduplicated_hits": stats.get("deduplicated_hits", len(hits)),
                      "returned_unique": len(hits), "final_included": None})
    handoff_path(b, "query_audit.json").write_text(
        json.dumps({"schema_version": 1, "queries": audit}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    return _fmt(f"[Search] 检索到 {len(papers)} 篇论文 → {out.name}", b)


@tool
def deep_research_papers(requirement: str, batch: str = "",
                         model: str = "",
                         max_tool_calls: int = 0) -> str:
    """[researcher_agent] 使用 OpenAI Deep Research 联网检索 DOI（可选来源）。"""
    from litdiscovery.agent.researcher_agent_pipeline.deep_research import run_deep_research
    b = resolve_batch(batch or None)
    result = run_deep_research(
        requirement, model=model or None,
        max_tool_calls=max_tool_calls if max_tool_calls > 0 else None)
    out = handoff_path(b, "deep_research_results.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return _fmt(f"[DeepResearch] {result['status']}，归一化 {len(result['papers'])} 篇 → {out.name}", b)


@tool
def search_memory_papers(requirement: str, batch: str = "", limit: int = 100) -> str:
    """[researcher_agent] 检索历史批次、知识目录和结构化提取目录。"""
    from litdiscovery.agent.researcher_agent_pipeline.memory_search import search_memory_papers as _search
    b = resolve_batch(batch or None)
    papers = _search(requirement, limit=limit)
    out = handoff_path(b, "memory_papers.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(papers, ensure_ascii=False, indent=2), encoding="utf-8")
    return _fmt(f"[MemorySearch] 命中 {len(papers)} 篇 → {out.name}", b)


@tool
def choose_papers(requirement: str, papers_file: str, min_keep: int = 12,
                  quality_floor: float = QUALITY_FLOOR_DEFAULT) -> str:
    """[filter_agent] 按科研需求对候选文献从宽取舍（filter 固定链的取舍步骤）。

    参数:
        requirement: 科研需求（取舍依据）
        papers_file: 候选文献 JSON 文件路径（researcher 收敛的 doi_list.json 全集）
        min_keep: 保留下限（默认 12）
    返回: 取舍后保留路径与数量。
    """
    from litdiscovery.agent.filter_agent_pipeline.choose import select_papers, save_choose_results
    papers = _read_json(papers_file)
    if isinstance(papers, dict):
        papers = next((v for v in papers.values() if isinstance(v, list)), [])
    selected, reason = select_papers(requirement, papers, min_keep=max(min_keep, 12),
                                     quality_floor=quality_floor)
    b = batch_of(papers_file)
    save_choose_results(selected, reason, b, min_keep=min_keep, requirement=requirement)
    return _fmt(f"[Choose] 保留 {len(selected)} 篇（依据: {reason}）", b)


@tool
def snowball_expand(seeds_file: str, rounds: int = 1, oa_only: bool = False,
                    ref_limit: int = 0, max_candidates: int = 0,
                    sample_per_paper: int = 0) -> str:
    """[researcher_agent] 引用雪球扩展：对每篇种子随机抽取参考文献扩容。

    只取 references（不取 citations）；每篇种子从扩展出的参考文献中随机抽取
    sample_per_paper 条（随机扩容，非按引用量取 top），跨种子去重、总量封顶。

    参数:
        seeds_file: 种子文献 JSON 文件路径（researcher 链为 search_results.json）
        rounds: 雪球轮数（默认 1）
        oa_only: 只保留开放获取候选（默认 False）
        ref_limit: 每篇种子参考文献扩展上限（0=config 默认）
        max_candidates: 雪球候选总上限（0=config 默认）
        sample_per_paper: 每篇种子随机抽取的参考文献条数（0=config 默认）
    返回: 雪球候选路径与数量。
    """
    import random
    from litdiscovery.config import (SNOWBALL_MAX_CANDIDATES,
                                     SNOWBALL_REF_LIMIT, SNOWBALL_SAMPLE_PER_PAPER)
    from litdiscovery.agent.researcher_agent_pipeline import snowball
    seeds = _read_json(seeds_file)
    if isinstance(seeds, dict):
        seeds = next((v for v in seeds.values() if isinstance(v, list)), [])
    ref_limit = ref_limit if ref_limit and ref_limit > 0 else SNOWBALL_REF_LIMIT
    cap = max_candidates if max_candidates and max_candidates > 0 else SNOWBALL_MAX_CANDIDATES
    sample = sample_per_paper if sample_per_paper and sample_per_paper > 0 else SNOWBALL_SAMPLE_PER_PAPER
    cands = []
    seen = set()
    for s in seeds:
        doi = s.get("doi")
        if not doi:
            continue
        refs = snowball.fetch_neighbors(doi, kinds=("references",),
                                        oa_only=oa_only, limit_ref=ref_limit)
        # 随机抽取：每篇种子取 sample 条参考文献
        if len(refs) > sample:
            refs = random.sample(refs, sample)
        for cand in refs:
            k = snowball._norm_doi(cand.get("doi")) or (cand.get("title") or "").lower()
            if not k or k in seen:
                continue
            seen.add(k)
            cands.append(cand)
        if len(cands) >= cap:
            break
    cands = snowball.dedup_papers(cands)[:cap]
    b = batch_of(seeds_file)
    p = handoff_path(b, "snowball_candidates.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cands, ensure_ascii=False, indent=2), encoding="utf-8")
    return _fmt(f"[Snowball] 随机抽参考文献，扩展出 {len(cands)} 个候选 → {p.name}", b)


@tool
def finalize_batch(batch: str = "", download_n: int = 0) -> str:
    """[filter_agent] 定稿下载列表：以 filter 取舍结果为准，写入 orders/doi_list.txt。

    researcher 已把检索 + 雪球参考文献收敛进 doi_list.json，filter 的取舍
    （doi_choose_results.json）作用于全集——故下载列表直接采用取舍结果；
    取舍结果缺失时回退 doi_list.json（兼容旧链/未取舍场景）。download_n 封顶后
    写 orders/doi_list.txt + orders/doi_reach_results.json，供下载阶段使用。

    参数:
        batch: 批次目录
        download_n: 本轮保留篇数上限（0=全部）
    返回: 批次目录。
    """
    from litdiscovery.common.logging import save_results

    def _as_list(data):
        if isinstance(data, dict):
            return next((v for v in data.values() if isinstance(v, list)), [])
        return data if isinstance(data, list) else []

    b = resolve_batch(batch or None)
    all_papers = _as_list(_read_json(read_handoff(b, "doi_choose_results.json")))
    if not all_papers:
        all_papers = _as_list(_read_json(read_handoff(b, "doi_list.json")))
    if not all_papers:
        all_papers = _as_list(_read_json(read_handoff(b, "seed_papers.json")))  # 兼容旧检索链
    from litdiscovery.agent.filter_agent_pipeline.quality import quality_assessment
    all_papers.sort(key=lambda p: quality_assessment(p).get("fulltext_likelihood", 0), reverse=True)
    if download_n and download_n > 0:
        all_papers = all_papers[:download_n]
    kpath = read_handoff(b, "keywords.txt")
    kws = kpath.read_text(encoding="utf-8").splitlines() if kpath.exists() else []
    save_results(all_papers, kws, b)
    return _fmt(f"[Finalize] 定稿 {len(all_papers)} 篇 → doi_list.txt + doi_reach_results.json", b)


@tool
def write_doi_list(batch: str = "", source: str = "doi_reach_results.json",
                   merge_source: str = "", merge_sources: str = "") -> str:
    """[researcher_agent] 收敛检索 + 雪球产物为 doi_list.json（doi/title/year/abstract/venue/citation_count）。

    读取主源 source（默认 doi_reach_results.json，兼容位），可选合并 merge_source
    （如 snowball_candidates.json，researcher 雪球扩容产物），按规范化 DOI 去重后写
    <batch>/orders/doi_list.json。下游 filter/extractor 只消费此最小契约，避免跨 Agent
    传大 JSON；保留 venue/citation_count 供 filter 取舍参考。

    参数:
        batch: 批次目录
        source: 主源文献 JSON 文件名（默认 doi_reach_results.json）
        merge_source: 合并源 JSON 文件名（可空；如 snowball_candidates.json）
    返回: 收敛后条目数与路径。
    """
    from litdiscovery.agent.filter_agent_pipeline.quality import deduplicate_papers
    b = resolve_batch(batch or None)
    src = read_handoff(b, source)
    def _collect(path, out):
        p = read_handoff(b, path) if path else None
        if not p or not p.exists():
            return
        payload = _read_json(p)
        papers = payload.get("papers", []) if isinstance(payload, dict) else payload
        if isinstance(papers, dict):
            papers = next((v for v in papers.values() if isinstance(v, list)), [])
        for rec in papers:
            if not (rec.get("doi") or "").strip():
                continue
            out.append({
                "doi": rec.get("doi") or "",
                "title": rec.get("title") or "",
                "year": rec.get("year"),
                "abstract": rec.get("abstract") or "",
                "venue": rec.get("venue") or "",
                "citation_count": rec.get("citation_count", 0),
                "source": rec.get("source") or Path(path).stem,
                "source_batch": rec.get("source_batch") or "",
                "best_oa_location": rec.get("best_oa_location") or {},
                "oa_locations": rec.get("oa_locations") or rec.get("locations") or [],
                "pdf_url": rec.get("pdf_url") or "",
                "fulltext_url": rec.get("fulltext_url") or rec.get("oa_url") or "",
                "is_oa": bool(rec.get("is_oa") or rec.get("is_open_access")),
            })

    out = []
    sources = [source, merge_source]
    sources.extend(s.strip() for s in merge_sources.split(",") if s.strip())
    if not any(read_handoff(b, item).exists() for item in sources if item):
        return _fmt("[WriteDoiList] 所有检索源均不存在（跳过）", b)
    for item in dict.fromkeys(sources):
        _collect(item, out)
    out, duplicates = deduplicate_papers(out)
    dst = handoff_path(b, "doi_list.json")
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return _fmt(f"[WriteDoiList] 收敛 {len(out)} 篇，去重 {len(duplicates)} 条 → doi_list.json", b)


@tool
def fetch_fulltext(batch: str = "", pdf: bool = False) -> str:
    """[filter_agent] 获取论文源文件（Markdown → markdowns/，其他格式进对应目录）。

    status=too_small（仅有摘要/无正文，低于 MIN_FULLTEXT_BYTES）单独计数，不计入成功。
    按 FULLTEXT_CONCURRENCY 并发获取各 DOI（网络密集，串行会拖慢全批次）。

    参数:
        batch: 批次目录
        pdf: True 走 PDF 下载路径，False 走 --fulltext 全文路径
    返回: 批次目录（随后由 preprocess 统一生成 end_mds/）。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from litdiscovery.agent.filter_agent_pipeline.fulltext import fetch_fulltext_by_doi
    from litdiscovery.agent.filter_agent_pipeline.pdf_fetch import (
        load_doi_list, PDF_OUTPUT_SUBDIR, download_pdf_by_doi)
    from litdiscovery.agent.filter_agent_pipeline.acquisition import (
        DownloadStats, format_from_result)
    b = resolve_batch(batch or None)
    dois = [d.strip() for d in load_doi_list(read_handoff(b, "doi_list.txt"))
            if d.strip() and not d.strip().startswith("#")]
    end_mds = b / "end_mds"
    stats = DownloadStats(len(dois))

    def _fetch_one(doi: str):
        """单个 DOI 的全文获取（线程安全：只读写该 DOI 自己的文件）。"""
        r = fetch_fulltext_by_doi(doi, end_mds, format_root=b)
        if r.get("status") == "too_small":
            return r, "too_small", False
        if r.get("path"):
            return r, "ok", False
        pdf_success = False
        if pdf:
            pdf_success = bool(download_pdf_by_doi(doi, b / PDF_OUTPUT_SUBDIR))
        return r, "failed", pdf_success

    # 并发获取，按原 DOI 顺序汇总（保证 fulltext_attempts.json 顺序稳定）
    ordered = {}
    with ThreadPoolExecutor(max_workers=FULLTEXT_CONCURRENCY) as ex:
        futures = {ex.submit(_fetch_one, doi): doi for doi in dois}
        for fut in as_completed(futures):
            ordered[futures[fut]] = fut.result()

    ok = failed = too_small = 0
    attempts = []
    for doi in dois:
        r, status, pdf_success = ordered[doi]
        attempts.append(r)
        if status == "too_small":
            too_small += 1
            stats.record(too_small=True)
        elif status == "ok":
            ok += 1
            stats.record(True, format_from_result(r))
        else:  # failed
            failed += 1
            stats.record(False)
            if pdf_success:
                ok += 1
                stats.record(True, "pdf")
        print(stats.render())
    from litdiscovery.common.fs import write_json_atomic
    write_json_atomic(handoff_path(b, "fulltext_attempts.json"), attempts)
    return _fmt(f"[Fulltext] 成功 {ok}, 失败 {failed}, 仅有摘要/过小 {too_small}", b)


# ============================================================
# 预处理
# ============================================================

@tool
def preprocess(batch: str = "", pdf_only: bool = False) -> str:
    """[filter_agent] 把批次内各格式原文（pdfs/xmls/txts/texs）统一转 markdowns/ → end_mds/。

    参数:
        batch: 批次目录
        pdf_only: 只处理 PDF（默认 False 处理全部格式）
    返回: 批次目录（end_mds 就绪）。
    """
    from litdiscovery.agent.extractor_agent_pipeline.preprocess import run_to_markdown, _WorkerSupervisor
    b = resolve_batch(batch or None)
    if pdf_only:
        # 只转 pdfs/ → markdowns（子进程隔离，防 Docling/ONNX 的 C++ 级 OOM 硬杀进程）
        pdfs = b / "pdfs"
        if pdfs.is_dir():
            md = b / "markdowns"; md.mkdir(parents=True, exist_ok=True)
            supervisor = _WorkerSupervisor()
            for f in pdfs.iterdir():
                if f.suffix.lower() == ".pdf":
                    supervisor.convert(str(f), str(md / (f.stem + ".md")))
        return _fmt("[Preprocess] 仅 PDF 转换完成（markdowns/）", b)
    run_to_markdown(b)
    from litdiscovery.agent.filter_agent_pipeline.quality import audit_fulltext_corpus
    from litdiscovery.common.fs import write_json_atomic
    audit_path = handoff_path(b, "fulltext_quality.json")
    audit = audit_fulltext_corpus(b / "end_mds")
    write_json_atomic(audit_path, audit)
    if audit["usable_rate"] < 0.8:
        print(f"[WARN] 全文可用率仅 {audit['usable_rate']:.1%}，建议补充全文后再提取。")
    return _fmt("[Preprocess] 各格式原文已统一预处理 → end_mds/", b)


@tool
def review_run(batch: str = "") -> str:
    """[review_agent] 分析批次执行状态、失败步骤和日志，生成可执行反馈。"""
    from litdiscovery.paths import resolve_batch, handoff_path
    from litdiscovery.common.fs import write_json_atomic
    b = resolve_batch(batch or None)
    state_path = b / "run_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    steps = state.get("steps", {})
    failed = [{"step_id": k, "stage": v.get("stage", ""),
               "operation": v.get("operation", ""), "error": v.get("error", "")}
              for k, v in steps.items() if v.get("status") == "failed"]
    running = [k for k, v in steps.items() if v.get("status") == "running"]
    suggestions = []
    for item in failed:
        msg = item["error"].lower()
        if "timeout" in msg or "connection" in msg or "503" in msg:
            suggestions.append(f"重试网络步骤 {item['step_id']}（网络/服务暂时不可用）")
        elif "model" in msg or "docling" in msg:
            suggestions.append(f"检查模型依赖后重试 {item['step_id']}")
        else:
            suggestions.append(f"查看步骤参数与输入产物后重试 {item['step_id']}")
    report = {"batch": str(b), "failed": failed, "running": running,
              "failed_count": len(failed), "suggestions": suggestions}
    write_json_atomic(handoff_path(b, "review_report.json"), report)
    lines = [f"[Review] 失败 {len(failed)}，运行中 {len(running)}"]
    lines.extend(f"- {s}" for s in suggestions)
    if not failed and not running:
        lines.append("- 未发现未完成步骤，可继续执行后续计划或生成报告")
    return _fmt("\n".join(lines), b)


# ============================================================
# 提取链（extractor_agent 角色：动态属性域注册表 + 分类门 + 属性/工艺提取）
# ============================================================

def _folder_path(batch: str, folder: str) -> tuple:
    """解析批次目录 + 论文文件夹路径。返回 (batch_path, folder_path)。"""
    b = resolve_batch(batch or None)
    fp = Path(folder)
    if not fp.is_absolute():
        fp = b / "end_mds" / folder
    return b, fp


@tool
def classify_paper(batch: str, folder: str) -> str:
    """[extractor_agent] 分类门：判定论文属于 process/property/both/none。

    参数:
        batch: 批次目录
        folder: 论文文件夹名（end_mds/<folder>）
    返回: {route, property_domain, reason}。
    """
    from litdiscovery.llm_utils import read_fulltext_for_llm
    from litdiscovery.agent.extractor_agent_pipeline.extraction.process_extract import classify_paper_type
    b, fp = _folder_path(batch, folder)
    fulltext = read_fulltext_for_llm(fp / "fulltext.md")
    small_llm = create_agent("extractor_agent", max_tokens=256)
    result = classify_paper_type(fulltext, llm=small_llm)
    return json.dumps(result, ensure_ascii=False)


@tool
def extract_process(batch: str, folder: str) -> str:
    """[extractor_agent] 提取工艺步骤 + 材料优势 → <data_doi>/<folder>/process.json。

    参数:
        batch: 批次目录
        folder: 论文文件夹名
    返回: 提取的工艺 JSON 路径。
    """
    from litdiscovery.llm_utils import read_fulltext_for_llm
    from litdiscovery.agent.extractor_agent_pipeline.extraction.process_extract import extract_process_flow
    b, fp = _folder_path(batch, folder)
    fulltext = read_fulltext_for_llm(fp / "fulltext.md")
    llm = create_agent("extractor_agent")
    result = extract_process_flow(fulltext, llm=llm)
    out = data_doi_dir(b) / folder
    out.mkdir(parents=True, exist_ok=True)
    (out / "process.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    return _fmt(f"[Process] {len(result.get('process', {}).get('steps', []))} 步 → {out / 'process.json'}", b)


@tool
def extract_materials(batch: str, folder: str, domain: str = "thermoelectric") -> str:
    """[extractor_agent] 提取候选材料列表。

    参数:
        batch: 批次目录
        folder: 论文文件夹名
        domain: 属性域（thermoelectric/ferroelectric/piezoelectric/phasechange）
    返回: 候选材料 JSON 列表。
    """
    from litdiscovery.llm_utils import read_fulltext_for_llm
    from litdiscovery.agent.extractor_agent_pipeline.extraction.property_extract import extract_material_candidates
    b, fp = _folder_path(batch, folder)
    fulltext = read_fulltext_for_llm(fp / "fulltext.md")
    small_llm = create_agent("extractor_agent", max_tokens=256)
    mats = extract_material_candidates(fulltext, llm=small_llm, domain=domain)
    return json.dumps(mats, ensure_ascii=False)


@tool
def extract_property(batch: str, folder: str, domain: str = "thermoelectric",
                     material_names: str = "") -> str:
    """[extractor_agent] 提取属性性能 → <data_doi>/<folder>/performance.json。

    参数:
        batch: 批次目录
        folder: 论文文件夹名
        domain: 属性域
        material_names: 逗号分隔限定材料名（可选）
    返回: 提取路径。
    """
    from litdiscovery.llm_utils import read_fulltext_for_llm
    from litdiscovery.agent.extractor_agent_pipeline.extraction.property_extract import extract_properties
    b, fp = _folder_path(batch, folder)
    fulltext = read_fulltext_for_llm(fp / "fulltext.md")
    names = [n.strip() for n in material_names.split(",") if n.strip()] or None
    llm = create_agent("extractor_agent")
    result = extract_properties(fulltext, llm=llm, material_names=names, domain=domain)
    out = data_doi_dir(b) / folder
    out.mkdir(parents=True, exist_ok=True)
    (out / "performance.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
    return _fmt(f"[Property] {len(result.get('materials', []))} 材料 → {out / 'performance.json'}", b)


@tool
def extract_structure(batch: str, folder: str, domain: str = "thermoelectric",
                      material_names: str = "") -> str:
    """[extractor_agent] 提取结构信息 → <data_doi>/<folder>/structure.json。

    参数:
        batch: 批次目录
        folder: 论文文件夹名
        domain: 属性域
        material_names: 逗号分隔限定材料名（可选）
    返回: 提取路径。
    """
    from litdiscovery.llm_utils import read_fulltext_for_llm
    from litdiscovery.agent.extractor_agent_pipeline.extraction.property_extract import extract_structural_properties
    b, fp = _folder_path(batch, folder)
    fulltext = read_fulltext_for_llm(fp / "fulltext.md")
    names = [n.strip() for n in material_names.split(",") if n.strip()] or None
    llm = create_agent("extractor_agent")
    result = extract_structural_properties(fulltext, llm=llm, material_names=names, domain=domain)
    out = data_doi_dir(b) / folder
    out.mkdir(parents=True, exist_ok=True)
    (out / "structure.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    return _fmt(f"[Structure] {len(result.get('materials', []))} 材料 → {out / 'structure.json'}", b)


@tool
def extract_tables(batch: str, folder: str, domain: str = "thermoelectric") -> str:
    """[extractor_agent] 从论文表格提取数据 → <data_doi>/<folder>/tables_output.json。

    参数:
        batch: 批次目录
        folder: 论文文件夹名
        domain: 属性域
    返回: 提取路径。
    """
    import pandas as pd
    from litdiscovery.agent.extractor_agent_pipeline.extraction.property_extract import extract_from_tables
    b, fp = _folder_path(batch, folder)
    table_data = []
    i = 1
    while True:
        csv_path = fp / f"table{i}.csv"
        caption_path = fp / f"table{i}_caption.md"
        if not csv_path.exists() or not caption_path.exists():
            break
        try:
            df = pd.read_csv(csv_path)
            caption = caption_path.read_text(encoding="utf-8").strip()
            table_data.append({
                "filename": f"table{i}.csv", "caption": caption,
                "rows": df.to_dict(orient="records"), "row_count": len(df),
            })
        except Exception as e:
            print(f"[WARN] Failed reading {csv_path.name}: {e}")
        i += 1
    llm = create_agent("extractor_agent")
    result = extract_from_tables(table_data, llm=llm, domain=domain)
    out = data_doi_dir(b) / folder
    out.mkdir(parents=True, exist_ok=True)
    (out / "tables_output.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                            encoding="utf-8")
    return _fmt(f"[Tables] {len(table_data)} 张表 → {out / 'tables_output.json'}", b)


@tool
def judge_properties(batch: str, folder: str, domain: str = "thermoelectric") -> str:
    """[extractor_agent] LLM 裁判验证属性数值一致性，清洗 performance.json。

    参数:
        batch: 批次目录
        folder: 论文文件夹名
        domain: 属性域
    返回: 清洗后的 performance.json 路径。
    """
    from litdiscovery.llm_utils import read_fulltext_for_llm
    from litdiscovery.agent.extractor_agent_pipeline.extraction.judge import judge_verify_properties
    b, fp = _folder_path(batch, folder)
    fulltext = read_fulltext_for_llm(fp / "fulltext.md")
    ddir = data_doi_dir(b) / folder
    thermo = _read_json(ddir / "performance.json")
    struct = _read_json(ddir / "structure.json")
    tables = _read_json(ddir / "tables_output.json")
    llm_judge = create_agent("extractor_agent", temperature=0.0, max_tokens=2500)
    cleaned = judge_verify_properties(
        fulltext, thermo_json=thermo, structure_json=struct,
        table_json=tables, llm=llm_judge, folder_name=folder, domain=domain)
    (ddir / "performance.json").write_text(json.dumps(cleaned, ensure_ascii=False, indent=2),
                                           encoding="utf-8")
    return _fmt(f"[Judge] 验证完成 → {ddir / 'performance.json'}", b)


@tool
def write_extraction(batch: str, folder: str) -> str:
    """聚合该论文全部提取产物落盘（进程内已写，本工具标记该论文完成）。

    参数:
        batch: 批次目录
        folder: 论文文件夹名
    返回: 提取产物目录。
    """
    b, fp = _folder_path(batch, folder)
    ddir = data_doi_dir(b) / folder
    return _fmt(f"[Write] 提取产物 → {ddir}", b)


@tool
def write_domain_registry(batch: str = "", requirement: str = "",
                          domain_registry: str = "",
                          fallback_domain: str = "thermoelectric") -> str:
    """[extractor_agent] 解析属性域注册表并落盘 domain_registry.json（动态域）。

    域注册表来源优先级：
      ① planner 显式给定 domain_registry（JSON 字符串，含 label/material_keywords/properties）
        → 校验 + 生成 prompts；
      ② 未给定 → 依据 requirement 由 LLM 生成（registry_generator 子能力）→ 校验；
      ③ 生成失败 / 无 requirement → 回退静态四域（fallback_domain）。
    校验通过后写 <batch>/orders/domain_registry.json（完整域 dict，含生成的 prompts），
    供 extract_batch 运行时注入。

    参数:
        batch: 批次目录
        requirement: 科研需求（② LLM 生成注册表的依据，可空）
        domain_registry: 显式注册表 JSON 字符串（① 优先），空则走 ②/③
        fallback_domain: ③ 回退静态域（默认 thermoelectric）
    返回: domain_registry.json 路径与来源说明。
    """
    from litdiscovery.agent.extractor_agent_pipeline.extraction.domain_registry import (
        build_prompts_from_registry,
        generate_domain_registry,
        validate_domain_registry,
    )
    b = resolve_batch(batch or None)

    source = ""
    if domain_registry:
        try:
            given = json.loads(domain_registry) if isinstance(domain_registry, str) else domain_registry
        except Exception:
            given = None
        if isinstance(given, dict) and not validate_domain_registry(given):
            full = build_prompts_from_registry(given)
            source = "planner 显式给定"
    if not source:
        llm = create_agent("extractor_agent", temperature=0.2, max_tokens=4096)
        full = generate_domain_registry(requirement, llm=llm,
                                        fallback_domain=fallback_domain)
        source = "LLM 生成" if not full.get("_fallback") else "回退静态四域"
        full.pop("_fallback", None)

    dst = handoff_path(b, "domain_registry.json")
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(full, ensure_ascii=False, indent=2), encoding="utf-8")
    n_props = len(full.get("properties", {}))
    return _fmt(f"[DomainRegistry] 来源: {source}，属性 {n_props} 个 → {dst.name}", b)


@tool
def extract_batch(batch: str = "", domain: str = "thermoelectric", limit: int = 2000,
                  domain_registry_file: str = "", min_fulltext_usable_rate: float = MIN_FULLTEXT_USABLE_RATE,
                  allow_low_quality: bool = False) -> str:
    """[extractor_agent] 对全批次跑优化后的提取工作流（分类门 + 选择性并行）。

    参数:
        batch: 批次目录
        domain: 回退属性域（无 domain_registry.json 时用）
        limit: 最多处理新篇数
        domain_registry_file: 动态属性域注册表文件（write_domain_registry 产物，
              留空则自动探测 <batch>/domain_registry.json）
    返回: 处理统计。
    """
    from litdiscovery.agent.extractor_agent_pipeline.extraction.api import run_extract_batch
    b = resolve_batch(batch or None)
    reg = None
    if domain_registry_file:
        reg_path = read_handoff(b, domain_registry_file)
    else:
        reg_path = read_handoff(b, "domain_registry.json")
    if reg_path.exists():
        reg = _read_json(reg_path)
    result = run_extract_batch(b / "end_mds", domain=domain, limit=limit,
                               domain_registry=reg,
                               min_fulltext_usable_rate=min_fulltext_usable_rate,
                               allow_low_quality=allow_low_quality)
    return _fmt(f"[Extract] 完成 {result['completed']} 篇（失败 {result['failed']}）", b)


# ============================================================
# gap 链（gap_concept_extractor / 检测 / gap_adjudicator）
# ============================================================

@tool
def materialize_gap(batch: str = "", skip_llm: bool = False) -> str:
    """[gap_concept_extractor] 仅物化语料为三表（material_props/material_struct/paper_concepts）。

    参数:
        batch: 批次目录
        skip_llm: True 概念提取降级为规则（省 LLM 调用）
    返回: 物化产物路径。
    """
    from litdiscovery.agent.research_gap_agent.api import materialize_stage
    b = resolve_batch(batch or None)
    r = materialize_stage(batch=b, skip_llm=skip_llm)
    return _fmt(f"[Gap] 物化完成（papers={r['n_papers']}, props={r['n_props']}, "
                f"struct={r['n_struct']}, concepts={r['n_concepts']}）→ gap_output/", b)


@tool
def materialize_evidence(batch: str = "") -> str:
    """将 gap 物化表转换为带溯源状态的 Claim 存储。"""
    from litdiscovery.services import EvidenceService
    b = resolve_batch(batch or None)
    result = EvidenceService().materialize(b)
    return _fmt(f"[Evidence] {result['claims']} claims, traceability={result['traceability_rate']:.1%}", b)


@tool
def detect_gaps(batch: str = "") -> str:
    """检测 research-gap 候选（纯 pandas，无 LLM；读已物化三表）。

    参数:
        batch: 批次目录
    返回: 检测候选统计。
    """
    from litdiscovery.agent.research_gap_agent.api import detect_stage
    b = resolve_batch(batch or None)
    r = detect_stage(batch=b)
    return _fmt(f"[Detect] {r['n_detected']} 个候选（未裁决）", b)


@tool
def adjudicate_gaps(batch: str = "", candidates_file: str = "") -> str:
    """[gap_adjudicator] 批量裁决 gap 候选，排除假阳性（读检测候选）。

    参数:
        batch: 批次目录
        candidates_file: 兼容参数（候选统一读 gap_output/gap_candidates.json）
    返回: 裁决 verdict 路径。
    """
    from litdiscovery.agent.research_gap_agent.api import adjudicate_stage
    b = resolve_batch(batch or None)
    r = adjudicate_stage(batch=b)
    return _fmt(f"[Adjudicate] {r['accepted']}/{r['n_verdicts']} 个 gap 通过", b)


@tool
def write_gap_report(batch: str = "") -> str:
    """写出 research-gap 报告（research_gaps.json + .md；读已裁决 verdicts）。

    参数:
        batch: 批次目录
    返回: gap_output 路径。
    """
    from litdiscovery.agent.research_gap_agent.api import report_stage
    b = resolve_batch(batch or None)
    r = report_stage(batch=b)
    return _fmt(f"[GapReport] {r['accepted']} 个 gap → {b / 'gap_output'}", b)


# ============================================================
# 报告 / 验证 / 知识 / 占位
# ============================================================

@tool
def write_report(batch: str = "", sections: str = "") -> str:
    """[report_writer] 生成结构化调研报告（report.md + report.json）。

    参数:
        batch: 批次目录
        sections: 报告章节，逗号分隔；留空用默认章节
    返回: 报告路径。
    """
    from litdiscovery.agent.orchestrator.report import generate_report
    b = resolve_batch(batch or None)
    secs = [s.strip() for s in sections.split(",") if s.strip()] if sections else []
    generate_report(b, sections=secs)
    return _fmt(f"[Report] → {b / 'report.md'}", b)


@tool
def validate_formulas(formulas: str = "", batch: str = "") -> str:
    """[验证库] 用 MP/OQMD/AFLOW 交叉核对材料化学式，产出 validation/<formula>/comparison。

    参数:
        formulas: 逗号分隔化学式（留空则从 <batch>/gap_output/material_props.csv 提取）
        batch: 批次目录
    返回: 验证结果统计与路径。
    """
    from litdiscovery.agent.validate_agent.api import run_validate
    b = resolve_batch(batch or None) if batch else None
    r = run_validate(formulas, batch=b)
    return _fmt(f"[Validate] 验证 {r['n_validated']} 个公式（可用 {r['n_available']}）→ {r['summary_path']}",
                Path(r["summary_path"]) if r["summary_path"] else b or Path("."))


@tool
def index_knowledge(batch: str = "") -> str:
    """[knowledge_indexer] 把批次产物沉淀为知识库条目（knowledge/<batch>.jsonl）。

    参数:
        batch: 批次目录
    返回: 沉淀统计。
    """
    from litdiscovery.knowledge import index_batch
    b = resolve_batch(batch or None)
    stats = index_batch(b)
    return _fmt(f"[Knowledge] {stats['n_docs']} 条 → {stats['store']}", b)


@tool
def search_knowledge(query: str, k: int = 5) -> str:
    """[knowledge_indexer] 检索本地知识索引。"""
    from litdiscovery.knowledge import search
    return json.dumps(search(query, k=k), ensure_ascii=False)


@tool
def memory(query: str) -> str:
    """查询历史沉淀数据（litdiscovery.memory）：已检索/已提取过的文献与材料。

    在检索新论文前先调用本工具，避免重复检索已覆盖的文献。
    参数:
        query: 科研需求或关键词
    返回: 历史命中记录。
    """
    from litdiscovery.memory import ingest, search, summary
    records = ingest()
    hits = search(query, records=records)
    if not hits:
        return (summary(records)
                + "\n[Memory] 未命中历史相关文献，可正常检索。")
    lines = [summary(records), f"[Memory] 命中 {len(hits)} 条历史记录："]
    for h in hits[:15]:
        struct = "已提取" if h.get("has_structured") else "未提取"
        lines.append(
            f"  - {h.get('doi')} | {str(h.get('title'))[:50]} "
            f"({h.get('year') or '----'}) [{struct}] {h.get('batch')}")
    return "\n".join(lines)


# ============================================================
# 工具组装
# ============================================================

def build_tools() -> list:
    """返回 executor 可用工具列表（runbook / plan 展开调用）。"""
    all_tools = [
        list_roles, memory,
        generate_keywords, search_papers, deep_research_papers, search_memory_papers, choose_papers,
        snowball_expand, finalize_batch, write_doi_list, fetch_fulltext, preprocess,
        write_domain_registry, classify_paper, extract_process,
        extract_materials, extract_property, extract_structure,
        extract_tables, judge_properties, write_extraction, extract_batch,
        materialize_gap, materialize_evidence, detect_gaps, adjudicate_gaps, write_gap_report,
        write_report, validate_formulas, index_knowledge, search_knowledge, review_run,
    ]
    return all_tools
