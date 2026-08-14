import json

import pytest

from litdiscovery.agent.agent_roles.prompts import PROPERTY_DOMAINS
from litdiscovery.agent.filter_agent_pipeline.choose import _parse_uncertain_indices
from litdiscovery.agent.filter_agent_pipeline.quality import build_corpus_audit, deduplicate_papers
from litdiscovery.agent.extractor_agent_pipeline.extraction.api import run_extract_batch
from litdiscovery.agent.extractor_agent_pipeline.extraction.evidence_passages import (
    select_evidence_passages, select_task_passages,
)
from litdiscovery.agent.extractor_agent_pipeline.extraction.judge import judge_verify_properties
from litdiscovery.agent.extractor_agent_pipeline.extraction.phasechange_normalize import (
    normalize_phasechange_output, parse_composition,
)
from litdiscovery.agent.extractor_agent_pipeline.tables.evidence import build_table_evidence


def test_no_doi_near_duplicate_titles_are_merged():
    papers = [
        {"title": "Phase change memory materials and device applications", "doi": "", "abstract": "a"},
        {"title": "Phase-change memory materials and device applications", "doi": "", "abstract": "longer"},
    ]
    unique, duplicates = deduplicate_papers(papers)
    assert len(unique) == 1
    assert duplicates[0]["reason"] == "duplicate"


def test_uncertain_indices_are_parsed_for_review():
    assert _parse_uncertain_indices('{"kept_indices":[0],"uncertain_indices":[2]}', 3) == [2]


def test_corpus_audit_exposes_acceptance_checks():
    audit = build_corpus_audit([{"doi": "10.1/a", "title": "GST memory", "abstract": "x"}]
                               , "GST")
    assert "acceptance_checks" in audit
    assert "abstract_usable_rate_above_90pct" in audit["unmet_checks"]


def test_extract_hard_gate_rejects_low_fulltext_rate(tmp_path):
    end_mds = tmp_path / "batch" / "end_mds"
    end_mds.mkdir(parents=True)
    orders = end_mds.parent / "orders"
    orders.mkdir()
    (orders / "fulltext_quality.json").write_text(json.dumps({"usable_rate": 0.5}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="低于抽取门槛"):
        run_extract_batch(end_mds, limit=1)


def test_phasechange_evidence_pass_and_normalization():
    text = "=== Results ===\n\nGST crystallized at the DSC peak of 160 C at 10 K/min.\n\nNo data."
    excerpt = select_evidence_passages(text, PROPERTY_DOMAINS["phasechange"])
    assert "[SECTION: Results]" in excerpt and "160 C" in excerpt
    payload = {"materials": [{"name": "GST", "crystallization_temperature": [{
        "T_x_value": 160, "pulse_type": "set", "phase_state": "non-crystalline",
        "value_origin": "experiment", "crystallization_definition": "Peak",
    }]}]}
    normalized = normalize_phasechange_output(payload)
    material = normalized["materials"][0]
    entry = material["crystallization_temperature"][0]
    assert parse_composition("Ge2Sb2Te5")["alias"] == "GST"
    assert material["composition"]["alias"] == "GST"
    assert entry["pulse_type"] == "SET"
    assert entry["phase_state"] == "amorphous"
    assert entry["value_origin"] == "unknown"
    assert entry["crystallization_definition"] == "peak"


def test_task_passages_prioritize_relevant_sections():
    text = """=== Methods ===

Samples were sputter deposited at 300 C and annealed for 10 min.

=== Results and Discussion ===

XRD confirms the cubic structure and ZT reaches 1.2 at 700 K.

=== References ===

Sputtering references and unrelated ZT values.
"""
    process = select_task_passages(text, "thermoelectric", "process")
    property_context = select_task_passages(text, "thermoelectric", "property")
    structure = select_task_passages(text, "thermoelectric", "structure")

    assert "[SECTION: Methods]" in process
    assert "[SECTION: Results and Discussion]" in property_context
    assert "[SECTION: Results and Discussion]" in structure


def test_parallel_extraction_runs_independent_tasks(monkeypatch, tmp_path):
    import time
    from litdiscovery.agent.extractor_agent_pipeline.extraction import graph

    def delayed(key):
        def run(state, cfg):
            time.sleep(0.08)
            return {key: {"materials": []}}
        return run

    monkeypatch.setattr(graph, "extract_property_node", delayed("thermo"))
    monkeypatch.setattr(graph, "extract_structure_node", delayed("structure"))
    monkeypatch.setattr(graph, "extract_table_json_node", lambda state, cfg: {
        "table_json_output": {"materials": []}, "table_evidence": {"records": []}})
    monkeypatch.setattr(graph, "process_extract_node", delayed("process"))

    state = {"route": "both", "folder": tmp_path, "fulltext": "", "llm": object(),
             "domain": "thermoelectric", "material_names": [], "table_data": []}
    started = time.monotonic()
    result = graph.parallel_extract_node(state, graph.RuntimeCfg())

    assert time.monotonic() - started < 0.14
    assert result["thermo"] == {"materials": []}
    assert result["structure"] == {"materials": []}
    assert result["process"] == {"materials": []}


def test_extraction_graph_compiles_with_parallel_path(tmp_path):
    from litdiscovery.agent.extractor_agent_pipeline.extraction.graph import RuntimeCfg, build_graph

    assert build_graph(RuntimeCfg(session_log=tmp_path)).get_graph().nodes


def test_limit_zero_means_no_batch_limit(tmp_path, monkeypatch):
    end_mds = tmp_path / "batch" / "end_mds"
    for name in ("paper-a", "paper-b"):
        (end_mds / name).mkdir(parents=True)
    invoked = []

    class App:
        def invoke(self, state):
            invoked.append(state["folder"].name)

    from litdiscovery.agent.extractor_agent_pipeline.extraction import api
    monkeypatch.setattr(api, "build_graph", lambda cfg: App())
    monkeypatch.setattr(api.time, "sleep", lambda seconds: None)

    result = run_extract_batch(
        end_mds, limit=0, min_fulltext_usable_rate=0,
        session_log=tmp_path / "session")

    assert result["completed"] == 2
    assert invoked == ["paper-a", "paper-b"]


def test_deterministic_table_evidence_keeps_source_location():
    table_data = [{
        "filename": "table1.csv",
        "caption": "Thermoelectric performance at 300 K",
        "rows": [{"Material": "Bi2Te3", "ZT": 1.2, "Temperature": 300,
                  "__table_row": 7}],
    }]

    evidence = build_table_evidence(table_data, "thermoelectric")

    assert evidence["materials"][0]["name"] == "Bi2Te3"
    record = evidence["records"][0]
    assert record["source_filename"] == "table1.csv"
    assert record["source_caption"] == "Thermoelectric performance at 300 K"
    assert record["source_row"] == 7


def test_judge_receives_raw_and_deterministic_table_evidence(monkeypatch, tmp_path):
    captured = {}

    class Response:
        content = '{"correct": {}, "incorrect": {}, "temp_mismatch": {}, "structure_ok": [], "notes": ""}'

    from litdiscovery.agent.extractor_agent_pipeline.extraction import judge
    monkeypatch.setattr(judge, "invoke_messages", lambda *args: Response())
    monkeypatch.setattr(
        judge, "render_prompt_pair",
        lambda domain, key, **kwargs: (captured.update(kwargs) or "system", "user"),
    )

    judge_verify_properties(
        "paper text", thermo_json={"materials": [{"name": "Bi2Te3"}]},
        table_data=[{"caption": "Table caption", "rows": [{"ZT": 1.2}]}],
        deterministic_table_json={"materials": [], "records": [{
            "material": "Bi2Te3", "table": 1, "source_row": 7,
        }]},
        llm=object(), log_path=str(tmp_path / "judge.log"),
    )

    assert "Table caption" in captured["table_context"]
    assert "source_row" in captured["table_context"]
