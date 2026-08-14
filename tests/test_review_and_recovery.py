import json
from pathlib import Path

from litdiscovery.agent.orchestrator import planner
from litdiscovery.agent.extractor_agent_pipeline import preprocess


def test_planner_reuses_existing_plan_for_run(tmp_path, monkeypatch):
    plan = {
        "plan_version": 3,
        "requirement": "phase change materials",
        "confirmed": True,
        "batch": str(tmp_path),
        "agents": [{"agent": "review_agent", "stage": "review", "params": {}}],
    }
    (tmp_path / "plan.v3.json").write_text(json.dumps(plan), encoding="utf-8")
    monkeypatch.setattr(planner, "generate_plan", lambda *a, **k: (_ for _ in ()).throw(AssertionError("LLM called")))
    result = planner.run_planner("run", batch=str(tmp_path))
    assert result["resumed"] is True
    assert result["plan_path"] == str(tmp_path / "plan.v3.json")


def test_planner_natural_language_resume_finds_latest_incomplete_batch(tmp_path, monkeypatch):
    batches = tmp_path / "batches"
    target = batches / "相变存储材料_2026_08_12_160149"
    accidental = batches / "热电续研_2026_08_12_170606"
    for batch, requirement, status in (
        (target, "相变存储材料的结构与性能", "running"),
        (accidental, "继续上一步工作", "running"),
    ):
        batch.mkdir(parents=True)
        plan = {
            "plan_version": 3, "requirement": requirement, "confirmed": True,
            "batch": str(batch),
            "agents": [{"agent": "review_agent", "stage": "review", "params": {}}],
        }
        (batch / "plan.v3.json").write_text(json.dumps(plan), encoding="utf-8")
        (batch / "run_state.json").write_text(
            json.dumps({"steps": {"fetch": {"status": status}}}), encoding="utf-8")

    monkeypatch.setattr("litdiscovery.paths.BATCHES_ROOT", batches)
    monkeypatch.setattr(planner, "generate_plan", lambda *a, **k: (_ for _ in ()).throw(AssertionError("LLM called")))

    result = planner.run_planner("进行上一步工作")

    assert result["resumed"] is True
    assert Path(result["batch"]) == target
    assert result["plan"]["requirement"] == "相变存储材料的结构与性能"


def test_review_run_reports_failed_steps(tmp_path):
    from litdiscovery.agent.agent_roles.tools import review_run

    (tmp_path / "run_state.json").write_text(json.dumps({
        "steps": {"s1": {"status": "failed", "stage": "retrieve",
                           "operation": "search", "error": "HTTP 503 timeout"},
                  "s2": {"status": "succeeded"}}
    }), encoding="utf-8")
    result = review_run.invoke({"batch": str(tmp_path)})
    report = json.loads((tmp_path / "orders" / "review_report.json").read_text(encoding="utf-8"))
    assert report["failed_count"] == 1
    assert "重试网络步骤 s1" in result


def test_run_to_markdown_uses_single_pdf_worker(tmp_path, monkeypatch):
    # PDF 转换走持久 worker 子进程（隔离 C++ 级 OOM），worker 只初始化一次（模型
    # 只加载一次），多篇 PDF 复用同一 worker，而非每篇重建转换器。
    pdfs = tmp_path / "pdfs"
    pdfs.mkdir()
    (pdfs / "a.pdf").write_bytes(b"pdf")
    (pdfs / "b.pdf").write_bytes(b"pdf")
    calls = []
    converted = []

    class FakeWorker:
        def __init__(self):
            calls.append("init")

        def convert(self, src, out):
            Path(out).write_text("# converted", encoding="utf-8")
            converted.append(src)
            return True, None, False

    monkeypatch.setattr(preprocess, "_WorkerSupervisor", FakeWorker)
    monkeypatch.setattr(preprocess, "run_preprocess", lambda *a, **k: None)
    preprocess.run_to_markdown(tmp_path)
    assert calls == ["init"]
    assert len(converted) == 2
