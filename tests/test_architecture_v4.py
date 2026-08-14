"""Contracts and repositories introduced by the modular runtime."""

import json

from litdiscovery.contracts import Claim, EvidenceLocator
from litdiscovery.contracts.plans import StepSpec
from litdiscovery.knowledge import index_batch
from litdiscovery.common.logging import append_execution_record
from litdiscovery.repositories import ClaimRepository
from litdiscovery.runtime import RunStateStore, StepStatus


def test_stable_step_id_ignores_position():
    first = StepSpec(stage="retrieve", tool="search", args={"q": "x"}).with_stable_id()
    second = StepSpec(stage="retrieve", tool="search", args={"q": "x"}).with_stable_id()
    changed = StepSpec(stage="retrieve", tool="search", args={"q": "y"}).with_stable_id()
    assert first.step_id == second.step_id
    assert first.step_id != changed.step_id


def test_run_state_records_failure_and_retry(tmp_path):
    store = RunStateStore(tmp_path)
    state = store.load(requirement="r")
    store.begin(state, "s1", "retrieve", "search")
    store.fail(state, "s1", ValueError("bad"))
    loaded = store.load()
    assert loaded.steps["s1"].status == StepStatus.FAILED
    assert loaded.steps["s1"].attempts == 1
    store.begin(loaded, "s1", "retrieve", "search")
    store.succeed(loaded, "s1", "ok")
    assert store.load().steps["s1"].attempts == 2


def test_claim_repository_roundtrip(tmp_path):
    repo = ClaimRepository(tmp_path)
    claim = Claim(claim_id="c1", subject="AlN", predicate="d33", value=5,
                  evidence=[EvidenceLocator(doi="10.1/x", quote="reported d33=5")])
    repo.save_claims([claim])
    assert repo.load_claims()[0].evidence[0].quote == "reported d33=5"


def test_knowledge_index_is_idempotent(tmp_path):
    batch = tmp_path / "batch"
    orders = batch / "orders"
    orders.mkdir(parents=True)
    (orders / "doi_reach_results.json").write_text(json.dumps([
        {"doi": "10.1/x", "title": "Phase change memory", "abstract": "GST switching"}
    ]), encoding="utf-8")
    store = tmp_path / "knowledge"
    index_batch(batch, store=store)
    index_batch(batch, store=store)
    assert len((store / "batch.jsonl").read_text(encoding="utf-8").splitlines()) == 1


def test_execution_event_schema_redacts_secrets(tmp_path, monkeypatch):
    from litdiscovery.common import logging as clog
    monkeypatch.setattr(clog, "SESSIONS_ROOT", tmp_path / "sessions")
    append_execution_record(tmp_path / "batch", {
        "step_id": "retrieve.search.1", "tool": "search",
        "args": {"api_key": "do-not-log"}, "status": "failed", "error": "timeout",
    })
    event = json.loads((tmp_path / "sessions" / "batch" / "execution.jsonl").read_text(encoding="utf-8"))
    assert event["schema_version"] == 1
    assert event["status"] == "failed"
    assert event["args"]["api_key"] == "***"
    assert event["error"] == "timeout"
