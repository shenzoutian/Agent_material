"""
litdiscovery.cli.main —— 统一命令行入口（`litdiscovery`），唯一的程序执行入口。

子命令（均以文本输入驱动）：
    run       全流水线（planner 纯路由 + executor 执行；--requirement 缺省交互输入）
    roles     列角色 + 角色级工具菜单
    pipeline  确定性 JSON 流水线（runbook 驱动，绕过 planner LLM）
    retrieve  只检索（关键词/种子 + 雪球 + 取舍 + 定稿）
    download  PDF 下载 / 全文获取（--fulltext 模式）
    preprocess  PDF/XML→markdown + 数据预处理
    extract   只提取（跑 stages.extraction.api.run_extract_batch）
    tables    表格解析脚手架
    gap       只 gap（跑 stages.gap.api.run_gap）
    report    只报告
    validate  验证库对照
    knowledge 知识库 {index,search}
    memory    长期记忆查询
    tools     列 planner 工具

所有子命令内部走 stages api（run_*() 函数）。
"""

import argparse
import json
import sys
from pathlib import Path

from litdiscovery.config import MIN_FULLTEXT_USABLE_RATE


def _print_report_paths():
    """打印最新生成调研报告的批次路径（若存在）。"""
    from litdiscovery.paths import BATCHES_ROOT, batch_sort_key
    if not BATCHES_ROOT.is_dir():
        return
    batches = [d for d in BATCHES_ROOT.iterdir()
               if d.is_dir() and (d / "report.md").exists()]
    if not batches:
        print("\n[Info] 未找到已生成的报告（report.md 仅在 report 阶段完成后落盘）")
        return
    latest = max(batches, key=batch_sort_key)
    print("\n" + "=" * 66)
    print("[报告位置] 最新调研报告：")
    print(f"  Markdown: {latest / 'report.md'}")
    print(f"  JSON   : {latest / 'report.json'}")
    print("=" * 66)


def _cmd_run(args):
    """全流水线：planner（LLM 路由）→ plan.v3.json → executor 确定性执行。"""
    from litdiscovery.agent.agent_roles import build_tools
    from litdiscovery.services import WorkflowService

    if args.list_tools:
        for t in build_tools():
            print(f"- {t.name}: {(t.description or '').splitlines()[0]}")
        return
    if args.list_agents:
        from litdiscovery.agent.orchestrator.agent_directory import render_directory
        print(render_directory())
        return

    requirement = (args.requirement or "").strip() or input("请输入科研需求: ").strip()
    if not requirement:
        print("[ERROR] 需求为空，退出。")
        sys.exit(1)

    docs = tuple(d.strip() for d in (args.docs or "").split(",") if d.strip())
    print("=" * 66)
    print(f"[Research] 需求: {requirement}")
    if docs:
        print(f"[Research] 手动文档 ({len(docs)} 个): 跳过检索，直接提取")
    print("[Research] 调用 planner 生成 plan.v3.json ...")
    print("=" * 66)

    constraints = {
        "seed_dois": args.seed_dois or "",
        "download_n": args.download_n if args.download_n > 0 else None,
        "limit": args.limit,
        "docs": docs,
    }
    workflow = WorkflowService()
    result = workflow.plan(requirement, batch=args.batch or "",
                           constraints=constraints, confirm=not args.auto)

    print("\n" + "=" * 66)
    print("[Planner] plan.v3.json（agent 链）:")
    for i, ag in enumerate(result["plan"].get("agents", []), 1):
        print(f"  {i}. {ag['agent']} [{ag.get('stage', '')}]")
        print(f"       params: {json.dumps(ag.get('params', {}), ensure_ascii=False)}")
    print(f"[Planner] 批次: {result['batch']}")

    if args.dry_run:
        from litdiscovery.agent.orchestrator.plan import plan_to_runbook
        rb = plan_to_runbook(result["plan"])
        print("\n[Research] dry-run：翻译后的 runbook 步骤：")
        for i, s in enumerate(rb["steps"], 1):
            print(f"  {i}. {s.get('tool') or s.get('kind')}({json.dumps(s['args'], ensure_ascii=False)})")
        print("[Research] dry-run：未执行任何工具")
        return

    print("\n[Research] 交 executor 执行 ...")
    exec_result = workflow.execute(result["plan_path"], batch=result["batch"])
    print("\n" + "=" * 66)
    print(f"[Executor] 执行完成：{exec_result['executed']} 步执行 / {exec_result['skipped']} 步跳过（共 {exec_result['steps']}）")
    print(f"[Executor] 批次: {exec_result['batch']}")
    print("=" * 66)
    _print_report_paths()


def _cmd_roles(args):
    from litdiscovery.agent.agent_roles import list_roles
    print(list_roles(args.query or ""))


def _cmd_tools(args):
    from litdiscovery.agent.agent_roles import build_tools
    for t in build_tools():
        print(f"- {t.name}: {(t.description or '').splitlines()[0]}")


def _cmd_retrieve(args):
    from litdiscovery.agent.researcher_agent_pipeline.doi_reach import run_doi_reach
    run_doi_reach(args)


def _cmd_download(args):
    from litdiscovery.agent.filter_agent_pipeline.download import run_download
    run_download(args)


def _cmd_preprocess(args):
    from litdiscovery.paths import resolve_batch
    from litdiscovery.agent.extractor_agent_pipeline.preprocess import run_to_markdown
    b = resolve_batch(args.batch or None)
    run_to_markdown(b)
    from litdiscovery.agent.filter_agent_pipeline.quality import audit_fulltext_corpus
    from litdiscovery.common.fs import write_json_atomic
    write_json_atomic(b / "orders" / "fulltext_quality.json", audit_fulltext_corpus(b / "end_mds"))


def _cmd_extract(args):
    from litdiscovery.paths import resolve_batch
    from litdiscovery.agent.extractor_agent_pipeline.extraction.api import run_extract_batch
    b = resolve_batch(args.batch or None)
    result = run_extract_batch(b / "end_mds", domain=args.domain, limit=args.limit,
                               min_fulltext_usable_rate=args.min_fulltext_usable_rate,
                               allow_low_quality=args.allow_low_quality)
    print(f"[Extract] {result}")


def _cmd_tables(args):
    from litdiscovery.agent.extractor_agent_pipeline.tables.api import run_table_parse
    run_table_parse(args)


def _cmd_gap(args):
    from litdiscovery.paths import resolve_batch
    from litdiscovery.agent.research_gap_agent.api import run_gap
    b = resolve_batch(args.batch or None)
    run_gap(b, skip_llm=args.skip_llm)


def _cmd_evidence(args):
    from litdiscovery.paths import resolve_batch
    from litdiscovery.services import EvidenceService
    b = resolve_batch(args.batch or None)
    print(json.dumps(EvidenceService().materialize(b), ensure_ascii=False, indent=2))


def _cmd_report(args):
    from litdiscovery.paths import resolve_batch
    from litdiscovery.agent.orchestrator.report import generate_report
    b = resolve_batch(args.batch or None)
    generate_report(b, sections=[s for s in (args.sections or "").split(",") if s] or None)


def _cmd_validate(args):
    from litdiscovery.agent.validate_agent.api import run_validate, load_formulas_from_csv
    if args.material_props:
        formulas = load_formulas_from_csv(args.material_props)
        print(f"[Validate] 从 CSV 提取 {len(formulas)} 个材料家族")
    else:
        formulas = args.formula or ""
    run_validate(formulas, batch=args.batch or None, out_root=args.out)


def _cmd_knowledge(args):
    from litdiscovery.paths import resolve_batch
    if args.action == "index":
        from litdiscovery.knowledge import index_batch
        b = resolve_batch(args.batch or None)
        stats = index_batch(b)
        print(f"[Knowledge] {stats}")
    else:
        from litdiscovery.knowledge import search
        print(json.dumps(search(args.query), ensure_ascii=False, indent=2))


def _cmd_memory(args):
    from litdiscovery.memory import ingest, search, summary
    records = ingest()
    if args.query:
        hits = search(args.query, records=records)
        print(summary(records))
        for h in hits[:20]:
            print(f"  - {h.get('doi')} | {str(h.get('title'))[:60]} ({h.get('year') or '----'}) {h.get('batch')}")
    else:
        print(summary(records))


def _cmd_pipeline(args):
    from litdiscovery.agent.orchestrator.pipeline import main as pipeline_main
    argv = [args.runbook]
    if args.batch:
        argv += ["--batch", args.batch]
    if args.dry_run:
        argv.append("--dry-run")
    if args.force:
        argv.append("--force")
    if args.step:
        argv += ["--step", args.step]
    pipeline_main(argv)


def _cmd_evaluate(args):
    from litdiscovery.evaluation import evaluate_extraction, evaluate_retrieval
    gold = json.loads(Path(args.gold).read_text(encoding="utf-8"))
    predictions = json.loads(Path(args.predictions).read_text(encoding="utf-8"))
    result = (evaluate_retrieval(gold, predictions, k=args.k)
              if args.task == "retrieval" else evaluate_extraction(gold, predictions))
    print(json.dumps(result, ensure_ascii=False, indent=2))


def build_parser():
    parser = argparse.ArgumentParser(
        prog="litdiscovery",
        description="科研文献信息提取驱动科研发现 agent（统一入口）")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("run", help="全流水线（planner 纯路由 + executor 执行）")
    p.add_argument("--requirement", default=None, help="科研需求描述（缺省交互输入）")
    p.add_argument("--auto", action="store_true", help="跳过 plan 确认直接执行")
    p.add_argument("--batch")
    p.add_argument("--seed-dois")
    p.add_argument("--download-n", type=int, default=0)
    p.add_argument("--limit", type=int, default=2000)
    p.add_argument("--docs")
    p.add_argument("--list-tools", action="store_true", help="列出全部工具并退出")
    p.add_argument("--list-agents", action="store_true", help="列出子 Agent 目录并退出")
    p.add_argument("--dry-run", action="store_true", help="只生成 plan 并打印执行链")

    p = sub.add_parser("roles", help="列角色 + 工具菜单")
    p.add_argument("--query", default="")

    p = sub.add_parser("tools", help="列 planner 工具")

    p = sub.add_parser("retrieve", help="只检索（关键词/种子 + 雪球 + 取舍 + 定稿）")
    p.add_argument("--requirement", default=None, help="科研需求描述（缺省交互输入）")
    p.add_argument("--keywords", type=int, default=7, help="生成的关键词数量（默认 7）")
    p.add_argument("--results", type=int, default=20, help="每个关键词检索的论文数量（默认 20）")
    p.add_argument("--keywords-only", action="store_true", help="只生成并确认关键词，跳过检索")
    p.add_argument("--auto", action="store_true", help="跳过全部交互确认（脚本化运行）")
    p.add_argument("--tool", help="手动指定 MCP 工具名")
    p.add_argument("--no-choose", action="store_true", help="跳过 filter_agent 种子取舍")
    p.add_argument("--no-search", action="store_true", help="跳过联网前沿检索")
    p.add_argument("--seed-dois", help="手动种子 DOI 列表（逗号分隔），与关键词流程可叠加")
    p.add_argument("--no-keywords", action="store_true", help="跳过关键词生成+检索，仅用手动种子")
    p.add_argument("--seed-keep", type=int, default=12, help="filter_agent 保留的高质量种子数")
    p.add_argument("--snowball-rounds", type=int, default=1, help="雪球扩展轮数（默认 1）")
    p.add_argument("--download-n", type=int, default=None, help="本轮下载论文数量")
    p.add_argument("--no-snowball", action="store_true", help="跳过雪球扩展，仅保留种子")
    p.add_argument("--oa-only", action="store_true", help="雪球候选只保留开放获取论文")
    p.add_argument("--rank-by-llm", action="store_true", help="用 LLM 对雪球候选做相关性排序")
    p.add_argument("--log-dir", help="指定批次目录（产物；缺省自动建）")
    p.add_argument("--session-log", help="指定统一日志会话目录")

    p = sub.add_parser("download", help="PDF 下载 / 全文获取（--fulltext 模式）")
    p.add_argument("--doi", help="下载单篇论文（指定 DOI）")
    p.add_argument("--doi-file", help="DOI 列表文件路径")
    p.add_argument("--doi-dir", help="指定批次文件夹（读取其中的 doi_list.txt）")
    p.add_argument("--output", help="PDF 输出目录（默认: 来源目录下的 pdfs/）")
    p.add_argument("--no-skip", action="store_true", help="不跳过已存在的文件（覆盖下载）")
    p.add_argument("--fulltext", action="store_true", help="全文获取模式，输出到 end_mds/")
    p.add_argument("--fulltext-out", help="全文输出目录（默认: 来源目录下的 end_mds/）")

    p = sub.add_parser("preprocess", help="PDF/XML→markdown + 预处理")
    p.add_argument("--batch")

    p = sub.add_parser("extract", help="只提取")
    p.add_argument("--batch")
    p.add_argument("--domain", default="thermoelectric")
    p.add_argument("--limit", type=int, default=2000)
    p.add_argument("--min-fulltext-usable-rate", type=float, default=MIN_FULLTEXT_USABLE_RATE)
    p.add_argument("--allow-low-quality", action="store_true",
                   help="显式绕过全文质量门（仅调试或小样本诊断）")

    p = sub.add_parser("tables", help="表格解析脚手架")
    p.add_argument("--batch")
    p.add_argument("--base-dir", help="批量目录（每个含 fulltext.md 的子文件夹处理一次）")
    p.add_argument("--folder", help="单个论文文件夹（优先于 --base-dir）")
    p.add_argument("--domain", default=None, help="属性域，映射域 schema")
    p.add_argument("--llm-roles", action="store_true", help="用 LLM 批量分类表头")
    p.add_argument("--no-csv", action="store_true", help="不写 table{i}.csv")
    p.add_argument("--no-rules", action="store_true", help="不写 tables_rules.json")
    p.add_argument("--verbose", action="store_true", help="打印每张表的表头分类")

    p = sub.add_parser("gap", help="只 gap")
    p.add_argument("--batch")
    p.add_argument("--skip-llm", action="store_true",
                   help="跳过 gap 链的 LLM 调用（概念提取降级为规则 + 不裁决）")

    p = sub.add_parser("evidence", help="将物化结果转换为可审计 Claim")
    p.add_argument("--batch")

    p = sub.add_parser("report", help="只报告")
    p.add_argument("--batch")
    p.add_argument("--sections", default="")

    p = sub.add_parser("validate", help="验证库对照")
    p.add_argument("--formula", default="", help="查询的材料化学式，如 AlN")
    p.add_argument("--material-props", default=None, help="material_props.csv 路径，批量提取材料家族")
    p.add_argument("--out", default=None, help="输出目录（默认 artifacts/validation/）")
    p.add_argument("--batch")

    p = sub.add_parser("knowledge", help="知识库")
    p.add_argument("action", choices=["index", "search"])
    p.add_argument("--batch")
    p.add_argument("--query", default="")

    p = sub.add_parser("memory", help="长期记忆查询")
    p.add_argument("--query", default="")

    p = sub.add_parser("pipeline", help="确定性 JSON 流水线（runbook 驱动，绕过 planner LLM）")
    p.add_argument("runbook", help="runbook JSON 路径（自建，参考 pipeline.py docstring 结构）")
    p.add_argument("--batch", default="")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--step", default="")

    p = sub.add_parser("evaluate", help="用专家 gold set 评测检索或抽取")
    p.add_argument("--task", choices=["retrieval", "extraction"], required=True)
    p.add_argument("--gold", required=True, help="专家标注 JSON")
    p.add_argument("--predictions", required=True, help="系统预测 JSON")
    p.add_argument("--k", type=int, default=50, help="检索 Recall/Precision@K")

    return parser


def main(argv=None):
    from litdiscovery.common.logging import reconfigure_utf8
    reconfigure_utf8()
    parser = build_parser()
    args = parser.parse_args(argv)
    cmds = {
        "run": _cmd_run, "roles": _cmd_roles, "tools": _cmd_tools,
        "retrieve": _cmd_retrieve, "download": _cmd_download,
        "preprocess": _cmd_preprocess, "extract": _cmd_extract,
        "tables": _cmd_tables, "gap": _cmd_gap, "evidence": _cmd_evidence,
        "report": _cmd_report, "validate": _cmd_validate,
        "knowledge": _cmd_knowledge, "memory": _cmd_memory,
        "pipeline": _cmd_pipeline, "evaluate": _cmd_evaluate,
    }
    if not args.command:
        print("可执行子命令：" + ", ".join(cmds))
        command = input("请输入要执行的子命令（回车默认 run）: ").strip() or "run"
        if command not in cmds:
            print(f"[ERROR] 未知子命令: {command}（可用: {', '.join(cmds)}）")
            sys.exit(1)
        args = parser.parse_args([command])  # 重新解析以填充该子命令的参数默认值
    cmds[args.command](args)


if __name__ == "__main__":
    # src 布局下直接运行本文件：把项目 src/ 加入 sys.path（未安装时也能执行）
    _SRC = Path(__file__).resolve().parents[2]
    if str(_SRC) not in sys.path:
        sys.path.insert(0, str(_SRC))
    main()
