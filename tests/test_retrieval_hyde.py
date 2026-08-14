"""retrieval/hyde.py 的 HyDE 拆分测试（不发起真实 LLM/网络调用）。

HyDE 自 orchestrator/planner 迁出（Agent 化重构：HyDE 归检索能力而非路由）。
"""
from litdiscovery.agent.researcher_agent_pipeline.hyde import _extract_json, _extract_hyde_terms, hyde_expand


def _fake_expanded():
    return {
        "sub_problems": [
            {"question": "材料?", "search_terms": ["AlScN kt2", "piezo filter"]},
            {"question": "工艺?", "search_terms": ["AlN sputter"]},
        ],
        "overall_terms": ["RF acoustic filter"],
    }


def test_extract_hyde_terms_flattens_all():
    terms = _extract_hyde_terms(_fake_expanded())
    assert terms == ["AlScN kt2", "piezo filter", "AlN sputter", "RF acoustic filter"]
    # 空展开 → 空列表（不崩）
    assert _extract_hyde_terms({}) == []
    assert _extract_hyde_terms(None) == []


def test_extract_hyde_terms_strips_empty():
    exp = {"sub_problems": [{"search_terms": ["a", " ", ""]}], "overall_terms": ["", "b"]}
    assert _extract_hyde_terms(exp) == ["a", "b"]


def test_extract_json_dict_only():
    assert _extract_json('{"sub_problems": []}') == {"sub_problems": []}
    try:
        _extract_json("[1, 2]")  # list 结构应拒绝
        assert False, "should raise"
    except ValueError:
        pass


def test_hyde_expand_ok_with_fake_llm():
    class FakeLLM:
        def invoke(self, messages):
            return type("Msg", (), {"content": '{"sub_problems": [{"question": "q", '
                                              '"search_terms": ["a"]}], "overall_terms": ["b"]}'})()

    expanded = hyde_expand("需求", llm=FakeLLM())
    assert len(expanded["sub_problems"]) == 1
    assert expanded["sub_problems"][0]["search_terms"] == ["a"]
    assert expanded["overall_terms"] == ["b"]


def test_hyde_expand_fallback_on_failure():
    class BadLLM:
        def invoke(self, messages):
            raise RuntimeError("LLM 挂了")

    expanded = hyde_expand("需求", llm=BadLLM())
    assert expanded["sub_problems"] == [{"question": "需求", "search_terms": ["需求"]}]
    assert expanded["overall_terms"] == ["需求"]
