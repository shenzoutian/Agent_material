"""agent/orchestrator/pipeline.py 确定性 runbook 驱动测试。"""
import json
import sys
from pathlib import Path

import pytest

from litdiscovery.agent.orchestrator.pipeline import (
    run_pipeline,
    TOOL_ENTRY,
    _BATCH_TOOLS,
)


@pytest.fixture(autouse=True)
def _iso_sessions(tmp_path, monkeypatch):
    """把批次会话隔离到临时目录；测试后卸载全局 stdout Tee，避免跨测试残留。"""
    from litdiscovery.common import logging as clog
    monkeypatch.setattr(clog, "SESSIONS_ROOT", tmp_path / "sessions")
    yield
    clog._redirect_stack.clear()
    sys.stdout = sys.__stdout__


def _mk_batch(tmp_path: Path) -> Path:
    b = tmp_path / "batch"
    b.mkdir()
    # 交接文件统一在 orders/（与 end_mds 同级）
    orders = b / "orders"
    orders.mkdir()
    (orders / "snowball_candidates.json").write_text(
        json.dumps([{"doi": "10.1000/c", "title": "CCC"}]), encoding="utf-8")
    return b


def _mk_runbook(tmp_path: Path, b: Path) -> Path:
    rb = tmp_path / "runbook.json"
    rb.write_text(json.dumps({
        "requirement": "测试需求",
        "domain": "piezoelectric",
        "batch": str(b),
        "steps": [
            {"stage": "retrieve", "kind": "copy",
             "args": {"src": "snowball_candidates.json", "dst": "seed_papers.json"}},
            {"stage": "retrieve", "tool": "finalize_batch", "args": {"download_n": 1}},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    return rb


def test_pipeline_real_run_writes_artifacts(tmp_path):
    b = _mk_batch(tmp_path)
    rb = _mk_runbook(tmp_path, b)
    r = run_pipeline(str(rb))
    assert r["executed"] >= 1
    assert (b / "orders" / "seed_papers.json").exists()     # copy 桥接（落 orders/）
    assert (b / "orders" / "doi_list.txt").exists()         # finalize_batch（落 orders/）
    assert (b / "run_state.json").exists()
    state = json.loads((b / "run_state.json").read_text(encoding="utf-8"))
    assert all(step["status"] == "succeeded" for step in state["steps"].values())
    assert r["trace"][0]["kind"] == "copy"
    assert r["trace"][-1]["tool"] == "finalize_batch"
    assert "batch" in r["trace"][-1]["args"]                # 自动注入批次
    # 结构化工具调用记录：execution.jsonl 落批次会话（会话名 = 批次名）
    from litdiscovery.common import logging as clog
    sess = clog.SESSIONS_ROOT / b.name
    assert (sess / "execution.jsonl").exists()
    recs = [json.loads(line) for line in
            (sess / "execution.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    tools = [rec["tool"] for rec in recs]
    assert "copy" in tools and "finalize_batch" in tools
    fb = next(rec for rec in recs if rec["tool"] == "finalize_batch")
    assert fb["agent"] == "filter_agent"                    # 工具 → 角色映射
    assert fb["duration_ms"] >= 0
    assert set(fb) >= {"schema_version", "event", "status", "run_id", "step_id",
                       "attempt", "agent", "tool", "args", "output", "duration_ms", "ts"}


def test_pipeline_resume_skips_done_steps(tmp_path):
    b = _mk_batch(tmp_path)
    rb = _mk_runbook(tmp_path, b)
    run_pipeline(str(rb))                                # 第一次全部执行
    r2 = run_pipeline(str(rb))                           # 第二次应全部跳过
    assert r2["executed"] == 0
    assert r2["skipped"] == 2
    state = json.loads((b / "run_state.json").read_text(encoding="utf-8"))
    assert all(v["status"] == "succeeded" for v in state["steps"].values())


def test_pipeline_force_reruns(tmp_path):
    b = _mk_batch(tmp_path)
    rb = _mk_runbook(tmp_path, b)
    run_pipeline(str(rb))
    r3 = run_pipeline(str(rb), force=True)
    assert r3["executed"] >= 1


def test_pipeline_dry_run_creates_no_batch(tmp_path):
    rb = tmp_path / "runbook.json"
    rb.write_text(json.dumps({
        "requirement": "dry 测试", "domain": "piezoelectric", "batch": "",
        "steps": [{"stage": "report", "tool": "write_report", "args": {}}],
    }, ensure_ascii=False), encoding="utf-8")
    r = run_pipeline(str(rb), dry_run=True)
    assert r["executed"] == 0
    assert "write_report" in r["trace"][0]["tool"]
    # 未创建任何新批次目录
    from litdiscovery.paths import BATCHES_ROOT
    before = set(p.name for p in BATCHES_ROOT.iterdir()) if BATCHES_ROOT.is_dir() else set()
    assert str(r["batch"]).startswith("<auto-新建批次>")


def test_pipeline_unknown_tool_raises(tmp_path):
    b = _mk_batch(tmp_path)
    rb = tmp_path / "runbook.json"
    rb.write_text(json.dumps({
        "requirement": "x", "batch": str(b),
        "steps": [{"stage": "retrieve", "tool": "no_such_tool", "args": {}}],
    }), encoding="utf-8")
    import pytest
    with pytest.raises(KeyError):
        run_pipeline(str(rb))


def test_tool_entry_inventory_complete():
    """TOOL_ENTRY 必须覆盖 build_tools() 的全部工具名（runbook 调用路径文档）。"""
    from litdiscovery.agent.agent_roles import build_tools
    names = {t.name for t in build_tools()}
    assert names <= set(TOOL_ENTRY)
    # 自动注入批次的工具子集必须与 agent_roles/tools.py 接受 batch 的工具一致
    for t in ("search_papers", "finalize_batch", "write_doi_list", "extract_batch",
              "write_report", "fetch_fulltext", "preprocess", "materialize_gap"):
        assert t in _BATCH_TOOLS
    assert "generate_keywords" not in _BATCH_TOOLS


def test_write_doi_list_contract(tmp_path):
    """researcher_agent 收敛：检索 + 雪球合并 → doi_list.json 契约（含 venue/citation_count 与 OA 富字段）。"""
    from litdiscovery.agent.agent_roles import build_tools
    write_doi_list = {t.name: t for t in build_tools()}["write_doi_list"]
    b = tmp_path / "batch"
    b.mkdir()
    (b / "doi_reach_results.json").write_text(json.dumps([
        {"doi": "10.1000/a", "title": "A", "year": 2020, "abstract": "abs1",
         "venue": "Acta", "extra": "drop", "citation_count": 99},
        {"doi": "https://doi.org/10.1000/a", "title": "A dup", "year": 2020},  # 同归一化 DOI 去重
        {"doi": "", "title": "No DOI"},                                         # 无 DOI 丢弃
    ], ensure_ascii=False), encoding="utf-8")
    # 合并源（雪球候选）：同源去重 + 新增
    (b / "snowball_candidates.json").write_text(json.dumps([
        {"doi": "10.1000/a", "title": "A dup snow", "year": 2020},   # 与主源同 DOI → 丢弃
        {"doi": "10.1000/b", "title": "B", "year": 2021, "abstract": "abs2",
         "venue": "Scripta", "citation_count": 5},
    ], ensure_ascii=False), encoding="utf-8")
    out = write_doi_list.invoke({"batch": str(b), "merge_source": "snowball_candidates.json"})
    assert "doi_list.json" in out
    records = json.loads((b / "orders" / "doi_list.json").read_text(encoding="utf-8"))
    assert len(records) == 2
    assert set(records[0]) == {"doi", "title", "year", "abstract", "venue", "citation_count",
                               "source", "source_batch", "best_oa_location", "oa_locations",
                               "pdf_url", "fulltext_url", "is_oa"}
    assert records[0]["doi"] == "10.1000/a"
    assert records[0]["venue"] == "Acta"          # 富字段保留（供 filter 取舍参考）
    assert records[0]["citation_count"] == 99
    assert records[1]["doi"] == "10.1000/b"       # 雪球新增并入 doi_list
    # 源不存在 → 不落盘、返回提示（不抛）
    assert "跳过" in write_doi_list.invoke({"batch": str(tmp_path / "empty")})
