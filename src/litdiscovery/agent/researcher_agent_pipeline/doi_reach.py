"""
litdiscovery/agent/researcher_agent_pipeline/doi_reach.py —— doi_reach 检索编排库（关键词/种子 + 雪球 + 取舍 + 定稿）。

流程:
    1. 种子收集（三种来源）:
       - keywords（默认）: 前沿检索 → researcher_agent 生成关键词 → 确认 → Apify 检索 → 确认
       - manual: --seed-dois 或交互输入 DOI → OpenAlex/Crossref 补全元数据
       - both: 上述两者合并
       --no-keywords: 跳过关键词+Apify，仅用手动种子（省 Apify 调用）
    2. filter_agent 依据科研需求取舍 → 保留 --seed-keep（默认 12）篇高质量种子
    3. 【雪球】对每篇种子扩展 references + citations（OpenAlex 主源 / S2 兜底），
       去重 + 可选 --oa-only 过滤 + 排序（--rank-by-llm 可选 LLM 相关性排序）
    4. 合并种子 + 雪球候选
    5. 【提问】交互确定下载数量 download_n（--auto 用 --download-n 或默认）
    6. 截取前 download_n 篇 → 写 doi_list.txt + seed_papers.json + snowball_candidates.json

run_doi_reach(args) 接收统一 CLI `litdiscovery retrieve` 解析好的参数
（argparse.Namespace；缺省字段自动回填默认值），返回批次目录。交互确认步骤
（种子 DOI / 下载数量）在非 --auto 模式下由本模块 input() 提问。
"""

import asyncio
import json
import sys
from pathlib import Path

from litdiscovery.config import (
    create_agent,
    DEFAULT_KEYWORDS,
    DEFAULT_RESULTS_PER_KEYWORD,
    SEED_KEEP_DEFAULT,
    DOWNLOAD_N_AUTO_DEFAULT,
    SNOWBALL_MAX_CANDIDATES,
    SNOWBALL_OPTION_LOW,
    SNOWBALL_OPTION_MID,
)
from litdiscovery.common.logging import (
    create_log_dir,
    session_dir_for_batch,
    redirect_to_session,
    save_results,
    append_log_summary,
)
from litdiscovery.paths import handoff_path
from litdiscovery.agent.researcher_agent_pipeline.keywords import generate_keywords, confirm_keywords
from litdiscovery.agent.researcher_agent_pipeline.search import search_papers_async, confirm_papers, _enrich_doi
from litdiscovery.agent.filter_agent_pipeline.choose import select_papers, save_choose_results, append_choose_summary
from litdiscovery.agent.researcher_agent_pipeline import snowball


# 统一 CLI 未显式声明的字段 → 默认值（保证 args 缺省字段不中断）
_DEFAULTS = {
    "requirement": None,
    "keywords": DEFAULT_KEYWORDS,
    "results": DEFAULT_RESULTS_PER_KEYWORD,
    "keywords_only": False,
    "auto": False,
    "tool": None,
    "no_choose": False,
    "no_search": False,
    "seed_dois": None,
    "no_keywords": False,
    "seed_keep": SEED_KEEP_DEFAULT,
    "snowball_rounds": 1,
    "download_n": None,
    "no_snowball": False,
    "oa_only": False,
    "rank_by_llm": False,
    "log_dir": None,
    "session_log": None,
}


def _parse_seed_dois(text: str) -> list:
    """解析逗号/换行分隔的 DOI 列表。"""
    import re as _re
    return [d.strip() for d in _re.split(r"[,，;\n]+", text or "") if d.strip()]


def ask_seed_dois_interactive() -> list:
    """交互输入手动种子 DOI（可空）。"""
    inp = input("\n是否手动提供种子论文? (输入DOI,逗号分隔; 直接回车跳过): ").strip()
    return _parse_seed_dois(inp)


def ask_download_n(total: int) -> int:
    """交互提问下载数量（少/中/多三档 + 数字输入）。返回 1..total。"""
    low_n = SNOWBALL_OPTION_LOW
    mid_n = min(SNOWBALL_OPTION_MID, total)
    print("\n" + "=" * 66)
    print(f"[扩展文献] 当前候选 {total} 篇（种子 + 雪球扩展）")
    print(f"  建议档位:")
    print(f"    少   -> 前 {low_n} 篇（最相关）")
    print(f"    中   -> 前 {mid_n} 篇")
    print(f"    多   -> 全部 {total} 篇")
    print("=" * 66)
    while True:
        inp = input("请选择下载数量（输入 少/中/多 或具体数字; 回车=中）: ").strip()
        if not inp:
            return mid_n
        low = inp.lower()
        if low in ("q", "quit", "exit"):
            sys.exit(0)
        if low in ("少", "s", "low"):
            return min(low_n, total)
        if low in ("中", "m", "mid", "middle"):
            return mid_n
        if low in ("多", "all", "l", "large", "全部"):
            return total
        if inp.isdigit():
            n = int(inp)
            return min(max(n, 1), total)
        print("请输入 少/中/多、数字或直接回车。")


def _save_seed_papers(seeds: list, log_dir: Path):
    path = handoff_path(log_dir, "seed_papers.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(seeds, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[Seed] 种子论文已保存: {path} ({len(seeds)} 篇)")


def _save_snowball(cands: list, log_dir: Path):
    path = handoff_path(log_dir, "snowball_candidates.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cands, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[Snowball] 雪球候选已保存: {path} ({len(cands)} 篇)")


def run_doi_reach(args) -> Path:
    """执行 doi_reach 检索编排，返回批次目录（产物：orders/doi_list.txt 等）。

    args 为 argparse.Namespace（统一 CLI retrieve 解析）；缺省字段回填默认值。
    """
    for k, v in _DEFAULTS.items():
        if not hasattr(args, k):
            setattr(args, k, v)

    requirement = (args.requirement or "").strip() or input("请输入科研需求: ").strip()
    if not requirement:
        print("[ERROR] 需求为空，退出。")
        sys.exit(1)

    # 批次目录（产物：doi_list/results/end_mds）
    log_dir = Path(args.log_dir) if args.log_dir else create_log_dir(requirement)
    log_dir.mkdir(parents=True, exist_ok=True)

    # 会话日志目录（每批一会话：会话名 = 批次名，终端全程记录）
    session_log = Path(args.session_log) if args.session_log else session_dir_for_batch(log_dir)
    session_log.mkdir(parents=True, exist_ok=True)
    redirect_to_session(session_log)
    print("=" * 66)
    print(f"[Log] 本次运行批次目录（产物）: {log_dir}")
    print(f"[Log] 本次运行会话日志目录: {session_log}")
    print(f"[Log] 完整过程将记录到: {log_dir / 'result_log.txt'}")
    print("=" * 66)

    keywords = []
    seed_papers = []          # 手动种子（元数据已补全）
    keyword_papers = []       # 关键词检索论文

    # ---- 1. 种子收集 ----
    manual_dois = _parse_seed_dois(args.seed_dois)
    if not args.no_keywords and not manual_dois and not args.auto:
        manual_dois = ask_seed_dois_interactive()

    if manual_dois:
        print(f"[Seed] 手动种子 {len(manual_dois)} 个 DOI，正在补全元数据 ...")
        for d in manual_dois:
            p = _enrich_doi(d)
            if p:
                seed_papers.append(p)
                print(f"  [Seed] {(p['title'] or '')[:70]} ({p.get('year')}) {p.get('doi')}")

    if not args.no_keywords:
        print(f"\n[1/5] 调用 researcher_agent 生成 {args.keywords} 个关键词 ...")
        print(f"      需求: {requirement}")
        if args.no_search:
            print("      （--no-search，跳过联网前沿检索，直接生成）")
        else:
            print("      （联网检索近期前沿动态，辅助关键词延伸与跟进）")
        keywords = generate_keywords(requirement, args.keywords, use_search=not args.no_search)
        if not keywords:
            print("[ERROR] researcher_agent 未能生成有效关键词，退出。")
            sys.exit(1)

        if not args.auto:
            keywords = confirm_keywords(keywords)
        else:
            for i, kw in enumerate(keywords, 1):
                print(f"  {i:>3}. {kw}")

        if args.keywords_only:
            from litdiscovery.config import KEYWORDS_FILE
            kpath = handoff_path(log_dir, KEYWORDS_FILE)
            kpath.parent.mkdir(parents=True, exist_ok=True)
            kpath.write_text("\n".join(keywords) + "\n", encoding="utf-8")
            print(f"\n[OK] 关键词已保存到 {kpath}，跳过检索。")
            append_log_summary(session_log, requirement, keywords, [], status="仅生成关键词")
            return log_dir

        print(f"\n[2/5] 调用 Academic Paper Scraper 检索 {len(keywords)} 个关键词，"
              f"每个 {args.results} 篇（含摘要）...")
        try:
            keyword_papers = asyncio.run(search_papers_async(keywords, args.results, tool_name=args.tool))
        except RuntimeError as e:
            print(e)
            sys.exit(1)
        except Exception as e:
            print(f"[ERROR] 检索过程异常: {type(e).__name__}: {e}")
            sys.exit(1)

        if not args.auto:
            keyword_papers = confirm_papers(keyword_papers)
        else:
            print(f"  （--auto，保留全部 {len(keyword_papers)} 篇）")

    all_seed = snowball.dedup_papers(seed_papers + keyword_papers)
    if not all_seed:
        print("[ERROR] 没有种子论文。请提供 --seed-dois 或关键词检索，退出。")
        sys.exit(1)
    print(f"\n[Seed] 种子论文合计 {len(all_seed)} 篇（手动 {len(seed_papers)} + 关键词 {len(keyword_papers)}）")

    # ---- 2. filter_agent 取舍 → 扩量种子（保留 ≥ 关键词数×3，增强语料） ----
    seed_keep = max(args.seed_keep, len(keywords) * 3)
    if seed_keep != args.seed_keep:
        print(f"[Choose] 关键词 {len(keywords)} 个 ×3 = {len(keywords) * 3} > 默认 {args.seed_keep}，"
              f"本次保留 ≥ {seed_keep} 篇种子（语料扩量）")
    if not args.no_choose:
        print(f"\n[3/5] 调用 filter_agent 依据科研需求取舍种子（保留 ≥ {seed_keep} 篇）")
        selected, reason = select_papers(requirement, all_seed, seed_keep)
        seeds = selected[:seed_keep]
        save_choose_results(seeds, reason, log_dir, seed_keep, requirement=requirement)
        append_choose_summary(session_log, requirement, seeds, reason, seed_keep)
    else:
        seeds = all_seed[:seed_keep]
        print(f"\n[3/5] 已跳过 filter_agent 取舍（--no-choose），取前 {len(seeds)} 篇作种子。")
    _save_seed_papers(seeds, log_dir)

    # ---- 3. 雪球扩展 ----
    snowball_cands = []
    if not args.no_snowball:
        print(f"\n[4/5] 雪球扩展：对 {len(seeds)} 篇种子扩展 references+citations"
              f"（{args.snowball_rounds} 轮，OA-only={args.oa_only}）...")
        seen = set()
        for s in seeds:
            k = snowball._norm_doi(s.get("doi")) or (s.get("title") or "").lower()
            seen.add(k)
        frontier = seeds
        for r in range(args.snowball_rounds):
            new_round = []
            for seed in frontier:
                doi = seed.get("doi")
                if not doi:
                    continue
                for cand in snowball.fetch_neighbors(doi, oa_only=args.oa_only):
                    k = snowball._norm_doi(cand.get("doi")) or (cand.get("title") or "").lower()
                    if not k or k in seen:
                        continue
                    seen.add(k)
                    new_round.append(cand)
            print(f"  [Snowball] 第 {r + 1} 轮新增 {len(new_round)} 篇候选")
            snowball_cands.extend(new_round)
            if not new_round:
                break
            frontier = new_round

        if len(snowball_cands) > SNOWBALL_MAX_CANDIDATES:
            snowball_cands = snowball_cands[:SNOWBALL_MAX_CANDIDATES]
            print(f"  [Snowball] 候选超上限，截断至 {SNOWBALL_MAX_CANDIDATES}")
        snowball_cands = snowball.dedup_papers(snowball_cands)

        if snowball_cands:
            if args.rank_by_llm:
                print(f"  [Snowball] LLM 相关性排序 {len(snowball_cands)} 条候选 ...")
                llm = create_agent("filter_agent", temperature=0.2, max_tokens=2048)
                snowball_cands = snowball.rank_by_llm(requirement, snowball_cands, llm)
            else:
                snowball_cands = snowball.rank_candidates(snowball_cands)
        _save_snowball(snowball_cands, log_dir)
    else:
        print("\n[4/5] 已跳过雪球扩展（--no-snowball）。")

    # ---- 4. 合并种子 + 雪球候选（种子优先） ----
    seen_final = set()
    for s in seeds:
        seen_final.add(snowball._norm_doi(s.get("doi")) or (s.get("title") or "").lower())
    rest = [p for p in snowball_cands
            if (snowball._norm_doi(p.get("doi")) or (p.get("title") or "").lower()) not in seen_final]
    all_papers = seeds + rest
    print(f"\n[合并] 最终候选 {len(all_papers)} 篇（种子 {len(seeds)} + 雪球 {len(rest)}）")

    # ---- 5. 提问下载数量 ----
    if args.download_n is not None:
        download_n = min(max(args.download_n, 1), len(all_papers))
    elif not args.auto:
        download_n = ask_download_n(len(all_papers))
    else:
        download_n = min(DOWNLOAD_N_AUTO_DEFAULT, len(all_papers))
    print(f"[Download] 本轮下载 {download_n} 篇")

    final_papers = all_papers[:download_n]

    # ---- 6. 保存 ----
    print("\n[5/5] 保存结果 ...")
    save_results(final_papers, keywords, log_dir)
    append_log_summary(session_log, requirement, keywords, final_papers)

    return log_dir
