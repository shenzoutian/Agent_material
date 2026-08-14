"""robust_agent 异常分类 / 决策 / 记录 的单元测试。"""

import json

import pytest
import requests

from litdiscovery.agent.robust_agent import (
    Decision,
    FailureClass,
    FailureInfo,
    handle_exception,
    mark_success,
)
from litdiscovery.agent.robust_agent import (
    fallback_handler_agent,
    primary_handler_agent,
)
from litdiscovery.runtime import RunStateStore
from litdiscovery.runtime.state import StepStatus


@pytest.fixture(autouse=True)
def _reset_budget():
    """每个测试重置进程级重试预算，避免用例间串扰。"""
    fallback_handler_agent.budget = fallback_handler_agent.RetryBudget()
    yield


# ---------- primary_handler：规则分类 ----------

def test_classify_resource_exhaustion():
    assert (primary_handler_agent._rule_classify(MemoryError("x"))
            is FailureClass.RESOURCE_EXHAUSTED)
    assert (primary_handler_agent._rule_classify(
        RuntimeError("[ONNXRuntimeError] bad allocation"))
        is FailureClass.RESOURCE_EXHAUSTED)


def test_classify_network_timeout_and_rate_limit():
    assert (primary_handler_agent._rule_classify(requests.exceptions.Timeout("t"))
            is FailureClass.NETWORK_TIMEOUT)

    class _HttpErr(Exception):
        def __init__(self):
            self.response = type("R", (), {"status_code": 429})()

    assert (primary_handler_agent._rule_classify(_HttpErr())
            is FailureClass.RATE_LIMITED)


def test_classify_model_inference_not_generic_runtime():
    class ONNXRuntimeError(Exception):
        pass

    assert (primary_handler_agent._rule_classify(ONNXRuntimeError("conv"))
            is FailureClass.MODEL_INFERENCE)
    # 通用 RuntimeError 太宽泛，应归 UNKNOWN 而非误判为模型推理
    assert (primary_handler_agent._rule_classify(RuntimeError("boom"))
            is FailureClass.UNKNOWN)


def test_locate_sets_retryable_and_location():
    info = primary_handler_agent.locate_and_classify(
        requests.exceptions.Timeout("t"), stage="dl", operation="x")
    assert info.failure_class is FailureClass.NETWORK_TIMEOUT
    assert info.retryable is True
    assert info.location


# ---------- fallback_handler：决策 ----------

def test_decide_oom_degrades():
    info = FailureInfo(RuntimeError("bad allocation"),
                       failure_class=FailureClass.RESOURCE_EXHAUSTED)
    assert fallback_handler_agent.decide(info) is Decision.DEGRADE


def test_decide_not_found_skips():
    info = FailureInfo(Exception(), failure_class=FailureClass.NOT_FOUND)
    assert fallback_handler_agent.decide(info) is Decision.SKIP


def test_decide_unknown_aborts():
    info = FailureInfo(Exception(), failure_class=FailureClass.UNKNOWN)
    assert fallback_handler_agent.decide(info) is Decision.ABORT


def test_retry_budget_exhausts_to_skip():
    info = FailureInfo(Exception(), stage="s", operation="op",
                       failure_class=FailureClass.NETWORK_TIMEOUT)
    budget = fallback_handler_agent.RETRY_BUDGET
    decisions = [fallback_handler_agent.decide(info) for _ in range(budget + 1)]
    assert decisions[:budget] == [Decision.RETRY] * budget
    assert decisions[budget] is Decision.SKIP


def test_mark_success_resets_budget():
    info = FailureInfo(Exception(), stage="s", operation="op",
                       failure_class=FailureClass.NETWORK_TIMEOUT)
    assert fallback_handler_agent.decide(info) is Decision.RETRY
    mark_success(stage="s", operation="op")
    assert fallback_handler_agent.decide(info) is Decision.RETRY


# ---------- response_robust：记录 ----------

def test_handle_exception_records_event(tmp_path):
    decision = handle_exception(RuntimeError("bad allocation"), stage="convert",
                                operation="a.pdf", batch_root=str(tmp_path))
    assert decision is Decision.DEGRADE
    events = tmp_path / "robust_events.jsonl"
    assert events.exists()
    record = json.loads(events.read_text(encoding="utf-8").strip().splitlines()[0])
    assert record["failure_class"] == "resource_exhausted"
    assert record["decision"] == "degrade"


# ---------- RunStateStore.skip ----------

def test_run_state_skip(tmp_path):
    store = RunStateStore(tmp_path)
    state = store.load()
    store.begin(state, "step1", "fulltext", "tool")
    store.skip(state, "step1", "degrade")
    assert state.steps["step1"].status is StepStatus.SKIPPED
