"""plan.v3.json 契约 + plan→runbook 翻译测试（planner 纯路由的执行契约）。

覆盖：validate_plan、resolve_params 默认回填、plan_to_runbook 按 AGENT_DIRECTORY
展开（{p:} 参数回填 + 模板保留）、v2 账本（state.new_ledger 等）读写，以及
plan v3 → runbook → run_pipeline dry-run 全链可解析（不调 LLM / 网络）。
"""
import json
from pathlib import Path

from litdiscovery.config import DOWNLOAD_N_AUTO_DEFAULT
from litdiscovery.agent.orchestrator.params import resolve_params
from litdiscovery.agent.orchestrator.plan import (
    new_plan,
    plan_to_runbook,
    validate_plan,
)


def _mk_plan(agents=None):
    return new_plan("ScAlN 压电薄膜", agents or [
        {"agent": "researcher_agent", "stage": "retrieve",
         "params": {"keyword_count": 5}},
        {"agent": "filter_agent", "stage": "fulltext",
         "params": {"download_n": 3}},
        {"agent": "extractor_agent", "stage": "extract",
         "params": {"domain": "piezoelectric"}},
    ])


# ============================================================
# validate_plan
# ============================================================

def test_validate_plan_ok():
    assert validate_plan(_mk_plan()) == []


def test_validate_plan_unknown_agent():
    plan = _mk_plan([{"agent": "no_such_agent", "stage": "x", "params": {}}])
    errors = validate_plan(plan)
    assert errors and "未知 agent" in errors[0]


def test_validate_plan_missing_params():
    plan = _mk_plan([{"agent": "researcher_agent", "stage": "retrieve"}])
    errors = validate_plan(plan)
    assert errors and "缺 params" in errors[0]


# ============================================================
# resolve_params（软设置默认回填）
# ============================================================

def test_resolve_params_overrides_default():
    got = resolve_params("researcher_agent", {"keyword_count": 5})
    assert got["keyword_count"] == 5
    # 雪球/取舍/定稿参数已随 P2 迁入 filter_agent，researcher 不再持有 download_n
    assert "download_n" not in got
    # 额外字段（未进 schema，如 seed_dois）原样保留
    got2 = resolve_params("researcher_agent", {"seed_dois": ["10.1/a"]})
    assert got2["seed_dois"] == ["10.1/a"]
    # filter_agent 全参数面：未给 download_n → 用默认 DOWNLOAD_N_AUTO_DEFAULT
    got3 = resolve_params("filter_agent", {})
    assert got3["download_n"] == DOWNLOAD_N_AUTO_DEFAULT


# ============================================================
# plan_to_runbook（v3 agents 链 → runbook steps）
# ============================================================

def test_plan_to_runbook_expands_agent_steps():
    rb = plan_to_runbook(_mk_plan())
    assert rb["requirement"] == "ScAlN 压电薄膜"
    kinds = [(s.get("tool") or s.get("kind")) for s in rb["steps"]]
    # researcher 并行来源均以独立确定性步骤表达，再统一收敛
    assert kinds == ["hyde", "generate_keywords", "search_papers",
                     "deep_research_papers", "search_memory_papers", "snowball_expand",
                     "write_doi_list",
                     "choose_papers", "finalize_batch", "fetch_fulltext", "preprocess",
                     "write_domain_registry", "extract_batch"]
    # {p:keyword_count} 回填为 plan 给定值（覆盖默认 7），且保持 int 类型
    gen = next(s for s in rb["steps"] if s.get("tool") == "generate_keywords")
    assert gen["args"]["count"] == 5
    assert isinstance(gen["args"]["count"], int)
    # {p:download_n} 回填 filter_agent 的 plan 给定值 3，且保持 int 类型
    fin = next(s for s in rb["steps"] if s.get("tool") == "finalize_batch")
    assert fin["args"]["download_n"] == 3
    assert isinstance(fin["args"]["download_n"], int)
    # 雪球在 researcher，用 search_results.json 作种子（随机抽参考文献）
    snow = next(s for s in rb["steps"] if s.get("tool") == "snowball_expand")
    assert snow["args"]["seeds_file"] == "search_results.json"
    assert "sample_per_paper" in snow["args"]
    # 收敛步：检索 + 雪球合并进 doi_list
    wdl = next(s for s in rb["steps"] if s.get("tool") == "write_doi_list")
    assert wdl["args"]["merge_source"] == "snowball_candidates.json"
    # filter 取舍作用于收敛后的 doi_list 全集
    cho = next(s for s in rb["steps"] if s.get("tool") == "choose_papers")
    assert cho["args"]["papers_file"] == "doi_list.json"
    # extractor 参数回填 + 动态域注册表步（registry 步骤串起 write→extract）
    ext = next(s for s in rb["steps"] if s.get("tool") == "extract_batch")
    assert ext["args"]["domain"] == "piezoelectric"
    reg = next(s for s in rb["steps"] if s.get("tool") == "write_domain_registry")
    assert reg["args"]["fallback_domain"] == "piezoelectric"
    assert reg["args"]["domain_registry"] == ""       # 默认空 → LLM 生成/回退
    # 运行时模板保留给 run_pipeline._resolve_templates 解析
    assert "{requirement}" in rb["steps"][1]["args"]["requirement"]
    assert "{hyde:terms}" in rb["steps"][1]["args"]["context"]       # generate_keywords 吃 HyDE 维度
    assert "{prev:generate_keywords}" in rb["steps"][2]["args"]["keywords"]  # search 用扩充关键词


def test_plan_to_runbook_default_params_fill():
    """未在 plan 里给定的参数 → 用 params 参考库默认值回填。"""
    plan = _mk_plan([
        {"agent": "researcher_agent", "stage": "retrieve",
         "params": {"keyword_count": 5}},
        {"agent": "filter_agent", "stage": "fulltext", "params": {}},
    ])
    rb = plan_to_runbook(plan)
    fin = next(s for s in rb["steps"] if s.get("tool") == "finalize_batch")
    assert fin["args"]["download_n"] == DOWNLOAD_N_AUTO_DEFAULT   # 默认回填


def test_plan_to_runbook_dry_run_via_pipeline(tmp_path):
    """plan v3 → runbook → run_pipeline dry-run 全链可解析（不调 LLM/网络）。"""
    from litdiscovery.agent.orchestrator.pipeline import run_pipeline
    rb = plan_to_runbook(_mk_plan())
    rb_path = tmp_path / "rb.json"
    rb_path.write_text(json.dumps(rb, ensure_ascii=False), encoding="utf-8")
    r = run_pipeline(str(rb_path), dry_run=True)
    assert r["executed"] == 0
    assert len(r["trace"]) == len(rb["steps"])
    assert r["trace"][0]["kind"] == "hyde"
    assert r["trace"][-1]["tool"] == "extract_batch"
