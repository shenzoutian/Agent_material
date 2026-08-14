"""Planner-owned batch naming tests."""

from pathlib import Path


def test_run_planner_uses_generated_batch_name(tmp_path, monkeypatch):
    from litdiscovery.agent.orchestrator import planner
    from litdiscovery.common import logging as logging_mod

    monkeypatch.setattr(logging_mod, "BATCHES_ROOT", tmp_path / "batches")
    monkeypatch.setattr(logging_mod, "SESSIONS_ROOT", tmp_path / "sessions")
    monkeypatch.setattr(planner, "redirect_to_session", lambda *a, **k: None,
                        raising=False)
    monkeypatch.setattr(planner, "generate_plan", lambda *a, **k: {
        "plan_version": 3,
        "requirement": "研究面向低温应用的新型声学滤波器并提取材料性能",
        "batch_name": "新型滤波器",
        "confirmed": True,
        "domain": "piezoelectric",
        "batch": "",
        "agents": [{"agent": "researcher_agent", "stage": "retrieve", "params": {}}],
    })

    result = planner.run_planner("研究面向低温应用的新型声学滤波器并提取材料性能")
    name = Path(result["batch"]).name
    assert name.startswith("新型滤波器_")
    assert (Path(result["batch"]) / "plan.v3.json").exists()
    assert result["plan"]["batch_name"] == "新型滤波器"
