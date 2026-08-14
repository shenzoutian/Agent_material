"""纯函数单测：retrieval / gap / extraction 的确定性逻辑（无 LLM、无网络）。

覆盖新增的 item 7 目标：snowball（DOI 归一化/去重/排序）、choose（下标解析）、
gap 检测器（纯 pandas）、materialize（材料归一化/单位换算/DOI 解析/行迭代）、
judge（属性键归一化）、llm_utils（JSON 鲁棒解析）。
"""

import sys
import json
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from litdiscovery.agent.researcher_agent_pipeline.snowball import _norm_doi, dedup_papers, rank_candidates
from litdiscovery.agent.filter_agent_pipeline.choose import _normalize_indices, _parse_choose_response
from litdiscovery.agent.filter_agent_pipeline.quality import (
    audit_fulltext_corpus, balanced_quality_fill, deduplicate_papers, quality_assessment,
)
from litdiscovery.agent.research_gap_agent import detectors
from litdiscovery.agent.research_gap_agent.materialize import (
    build_specs,
    normalize_material,
    normalize_method,
    normalize_crystal,
    _to_num,
    _unit_factor,
    _convert_unit,
    resolve_doi,
    _normalize_title,
    _strip_parens,
    _extract_title,
    _extract_abstract,
    _iter_prop_rows,
    _iter_struct_rows,
)
from litdiscovery.agent.research_gap_agent.adjudicate import _fmt_candidate
from litdiscovery.agent.extractor_agent_pipeline.extraction.judge import build_property_map, normalize_judge_key
from litdiscovery.llm_utils import parse_json_text, robust_json_parse


# ============================================================
# retrieval/snowball.py
# ============================================================

def test_norm_doi():
    assert _norm_doi("https://doi.org/10.1000/AbC") == "10.1000/abc"
    assert _norm_doi("http://dx.doi.org/10.1000/xYz") == "10.1000/xyz"
    assert _norm_doi("https://dx.doi.org/10.1/Def") == "10.1/def"
    assert _norm_doi("10.1000/Plain") == "10.1000/plain"
    assert _norm_doi("  10.1/ABC  ") == "10.1/abc"
    assert _norm_doi(None) == ""
    assert _norm_doi("") == ""


def test_dedup_papers():
    cands = [
        {"doi": "10.1/a", "title": "A"},
        {"doi": "https://doi.org/10.1/a", "title": "A dup"},  # 同一归一化 DOI
        {"doi": "", "title": "Same Title"},
        {"doi": "", "title": "  same title "},                # 按标题小写去重
        {"doi": "10.2/b", "title": "B"},
    ]
    out = dedup_papers(cands)
    assert [p["title"] for p in out] == ["A", "Same Title", "B"]
    # seen_keys 视为已存在键
    out2 = dedup_papers([{"doi": "10.2/b", "title": "B"}], seen_keys={"10.2/b"})
    assert out2 == []
    # 空 key 丢弃
    assert dedup_papers([{"doi": "", "title": "  "}]) == []


def test_rank_candidates():
    cands = [
        {"citation_count": 100, "year": 2020},
        {"citation_count": 5, "year": 2025},   # 引用低但近 5 年加成
        {"citation_count": "abc", "year": 1990},  # 坏值不崩溃
        {"citation_count": None, "year": None},
    ]
    ranked = rank_candidates(cands)
    assert ranked[0]["citation_count"] == 100
    assert ranked[1]["citation_count"] == 5
    # 不修改入参顺序
    assert [c["citation_count"] for c in cands] == [100, 5, "abc", None]


# ============================================================
# retrieval/choose.py
# ============================================================

def test_normalize_indices():
    assert _normalize_indices([0, 2, 2, 5], 4) == [0, 2]   # 去重 + 越界剔除
    assert _normalize_indices(["1", 3], 4) == [1, 3]        # 字符串下标
    assert _normalize_indices([], 4) == []
    assert _normalize_indices([-1, 9, "x"], 4) == []        # 全非法


def test_parse_choose_response():
    # dict 形态 kept_indices
    idx, reason = _parse_choose_response(
        json.dumps({"reason": "按需求", "kept_indices": [0, 2]}), 3)
    assert idx == [0, 2] and reason == "按需求"
    # list 形态（纯数字数组）
    assert _parse_choose_response("[0, 1, 3]", 4)[0] == [0, 1, 3]
    # list 形态（dict 数组带 index 键，保输入序）
    assert _parse_choose_response('[{"index": 2}, {"index": 0}]', 4)[0] == [2, 0]
    # 无 JSON 时文本数字兜底
    assert _parse_choose_response("保留第 1 和第 3 篇", 5)[0] == [1, 3]
    # 越界下标剔除
    assert _parse_choose_response(json.dumps({"kept_indices": [0, 99]}), 2)[0] == [0]


def test_select_papers_mock(monkeypatch):
    from litdiscovery.agent.filter_agent_pipeline.choose import select_papers

    class FakeLLM:
        def invoke(self, messages):
            return type("Msg", (), {"content": '{"kept_indices": [1, 3], "reason": "ok"}'})()

    monkeypatch.setattr("litdiscovery.agent.filter_agent_pipeline.choose.create_agent", lambda role: FakeLLM())
    papers = [{"title": f"P{i}", "doi": f"10.0/{i}", "year": 2020} for i in range(4)]
    selected, reason = select_papers("req", papers, min_keep=1)
    assert reason == "ok"
    assert [p["doi"] for p in selected] == ["10.0/1", "10.0/3"]
    # 空候选直接返回
    assert select_papers("req", []) == ([], "")


def test_corpus_quality_dedup_and_balanced_fill():
    papers = [
        {"doi": "https://doi.org/10.1/a", "title": "GST switching", "abstract": "x" * 300,
         "year": 2024, "venue": "Nature", "source": "openalex"},
        {"doi": "10.1/a", "title": "duplicate", "abstract": "short"},
        {"doi": "10.1/b", "title": "Phase change crystallization", "abstract": "y" * 400,
         "year": 2012, "venue": "APL", "source": "crossref"},
    ]
    unique, removed = deduplicate_papers(papers)
    assert len(unique) == 2 and len(removed) == 1
    assessment = quality_assessment(unique[0], "GST phase change switching")
    assert assessment["score"] > 0 and "short_or_missing_abstract" not in assessment["issues"]
    zh = quality_assessment({"title": "相变材料结晶温度", "abstract": "z" * 200},
                            "相变材料的结晶温度")
    assert zh["relevance"] > 0
    filled = balanced_quality_fill([], unique, "phase change", 2)
    assert len(filled) == 2
    assert all(p["selection_reason"] == "quality_diversity_fill"
               or p["selection_reason"].startswith("quota_fill:") for p in filled)


def test_fulltext_quality_audit(tmp_path):
    good = tmp_path / "good"
    good.mkdir()
    (good / "fulltext.md").write_text(
        "Title: GST\nDOI: 10.1/x\nAbstract\n" + "phase change " * 300, encoding="utf-8")
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "fulltext.md").write_text("short", encoding="utf-8")
    audit = audit_fulltext_corpus(tmp_path)
    assert audit["papers"] == 2 and audit["usable"] == 1


# ============================================================
# stages/gap/detectors.py（纯 pandas）
# ============================================================

def test_detect_underexplored():
    props_df = pd.DataFrame([
        {"material_family": "AlN", "doi": "10.1/a", "property_key": "k2"},
    ])
    struct_df = pd.DataFrame([
        {"material_family": "AlN", "doi": "10.1/a"},
        {"material_family": "AlN", "doi": "10.2/b"},
    ])
    papers = {"f1": {"doi": "10.1/a", "title": "A"}, "f2": {"doi": "10.2/b", "title": "B"}}
    cands = detectors.detect_underexplored(props_df, struct_df, papers, build_specs(),
                                           min_papers=2)
    assert cands and all(c["type"] == "underexplored" for c in cands)
    # 已覆盖的 (AlN, k2) 不应出现在候选里
    assert all("k2" not in c["statement"] for c in cands)
    # evidence 带论文 title
    assert all(any(e.get("title") for e in c["evidence"]) for c in cands)
    # 空输入
    assert detectors.detect_underexplored(pd.DataFrame(), pd.DataFrame(), {}, {}, min_papers=2) == []


def test_detect_missing_links():
    # AlN(3篇)×MBE、GaN(2篇)×CVD 各自共现 → 交叉零元格成候选
    concepts_df = pd.DataFrame([
        {"doi": "10.1/a", "concept": "AlN", "type": "materials"},
        {"doi": "10.1/a", "concept": "MBE", "type": "methods"},
        {"doi": "10.2/b", "concept": "AlN", "type": "materials"},
        {"doi": "10.2/b", "concept": "MBE", "type": "methods"},
        {"doi": "10.3/c", "concept": "AlN", "type": "materials"},
        {"doi": "10.3/c", "concept": "MBE", "type": "methods"},
        {"doi": "10.4/d", "concept": "GaN", "type": "materials"},
        {"doi": "10.4/d", "concept": "CVD", "type": "methods"},
        {"doi": "10.5/e", "concept": "GaN", "type": "materials"},
        {"doi": "10.5/e", "concept": "CVD", "type": "methods"},
    ])
    cands = detectors.detect_missing_links(concepts_df, min_papers=2, top_k=10)
    pairs = {(c["concept_a"], c["concept_b"]) for c in cands}
    assert ("AlN", "CVD") in pairs and ("GaN", "MBE") in pairs
    assert all(c["type"] == "missing_link" for c in cands)
    # support_score = min(两边支撑)
    assert next(c for c in cands if (c["concept_a"], c["concept_b"]) == ("AlN", "CVD"))["support_score"] == 2
    # 支撑不足（method 各 1 篇）→ 无候选
    sparse = concepts_df[concepts_df["doi"].isin(["10.1/a", "10.2/b"])]
    assert detectors.detect_missing_links(sparse, min_papers=2) == []


def test_detect_structure_contradiction():
    struct_df = pd.DataFrame([
        {"material_family": "AlN", "crystal_norm": "wurtzite", "doi": "10.1/a"},
        {"material_family": "AlN", "crystal_norm": "zincblende", "doi": "10.2/b"},
    ])
    papers = {"f1": {"doi": "10.1/a", "title": "A"}, "f2": {"doi": "10.2/b", "title": "B"}}
    cands = detectors.detect_structure_contradiction(struct_df, papers, min_papers=2)
    assert len(cands) == 1
    assert cands[0]["subtype"] == "structure"
    assert cands[0]["concept_a"] == "AlN"
    assert cands[0]["support_papers"] == ["10.1/a", "10.2/b"]
    # 单篇（不足 min_papers）不报矛盾
    assert detectors.detect_structure_contradiction(struct_df.iloc[:1], papers, min_papers=2) == []


def test_detect_numeric_contradiction():
    def frame(a, b):
        return pd.DataFrame([
            {"material_family": "ScAlN", "property_key": "k2", "doi": "10.1/a",
             "value": a, "temp_K": 300, "source": "structured"},
            {"material_family": "ScAlN", "property_key": "k2", "doi": "10.2/b",
             "value": b, "temp_K": 310, "source": "structured"},
        ])

    # 差异 5 倍 > 50% → 矛盾
    cands, flags = detectors.detect_numeric_contradiction(frame(1.0, 5.0), min_papers=2)
    assert flags["data_sufficient"] is True
    assert len(cands) == 1 and cands[0]["subtype"] == "numeric"
    # 差异 20% < 50% → 不报
    cands2, _ = detectors.detect_numeric_contradiction(frame(10.0, 12.0), min_papers=2)
    assert cands2 == []
    # 只有 process_claim 文本 → 数据不足
    only_claim = frame(1.0, 5.0).assign(source="process_claim")
    cands3, flags3 = detectors.detect_numeric_contradiction(only_claim, min_papers=2)
    assert cands3 == [] and flags3["data_sufficient"] is False


# ============================================================
# stages/gap/materialize.py
# ============================================================

def test_normalize_material():
    assert normalize_material("Sc0.3Al0.7N") == ("Sc0.3Al0.7N", "ScAlN")
    assert normalize_material("GaN:Ge") == ("GaN:Ge", "GaN")
    assert normalize_material("BaTiO3 (001)") == ("BaTiO3 (001)", "BaTiO")
    assert normalize_material("") == ("", "")


def test_normalize_method_crystal():
    assert normalize_method("Molecular Beam Epitaxy (MBE)") == "molecular beam epitaxy"
    assert normalize_method("") == ""
    assert normalize_crystal("Wurtzite") == "wurtzite"
    assert normalize_crystal("Hexagonal (hcp)") == "hexagonalhcp"


def test_to_num():
    assert _to_num("1,234.5") == 1234.5
    assert _to_num("0.3") == 0.3
    assert _to_num("-5e2") == -500.0
    assert _to_num("abc") is None
    assert _to_num(None) is None


def test_unit_convert():
    from litdiscovery.agent.research_gap_agent.materialize import _attach_key
    specs = build_specs()
    s = _attach_key(specs)["thermoelectric:seebeck_coefficient"]  # _key 由 _attach_key 挂载
    # 单位换算：V/K → μV/K（×1e6）
    assert _unit_factor(s, "V/K") == 1e6
    assert _convert_unit(2.0, "V/K", s) == (2e6, "μV/K")
    # 同单位原值
    assert _convert_unit(200.0, "μV/K", s)[0] == 200.0
    # 未挂 _key 的裸 spec 不换算（因子 1.0）
    assert _unit_factor(specs["thermoelectric:seebeck_coefficient"], "V/K") == 1.0


def test_resolve_doi():
    authority = [
        {"doi": "10.1000/abc", "title": "ScAlN Piezoelectric"},
        {"doi": "10.2000/xyz", "title": "GaN Device"},
    ]
    # override 优先
    r = resolve_doi("folder1", "some title", authority, {"folder1": "10.1000/abc"})
    assert r["resolution"] == "override" and r["doi"] == "10.1000/abc"
    # decode：folder 名下划线还原为斜杠
    r = resolve_doi("10.1000_abc", "whatever", authority, {})
    assert r["resolution"] == "decode" and r["doi"] == "10.1000/abc"
    # title 归一化匹配
    r = resolve_doi("anything", "ScAlN Piezoelectric", authority, {})
    assert r["resolution"] == "title" and r["doi"] == "10.1000/abc"
    # 全部未命中
    r = resolve_doi("nope", "not in authority", authority, {})
    assert r["resolution"] == "missing"


def test_normalize_title_strip_parens():
    assert _normalize_title("ScAlN Piezoelectric!") == "scalnpiezoelectric"
    assert _normalize_title("") == ""
    assert _strip_parens("10.1000/(abc)") == "10.1000/abc"


def test_extract_title_abstract():
    md = "=== Preamble ===\n\nTitle: ScAlN Thin Films\n\nAbstract - The abstract body.\n\n=== Body ===\n..."
    assert _extract_title(md) == "ScAlN Thin Films"
    assert _extract_abstract(md) == "The abstract body."
    # 垃圾标题 → 正文首行
    md2 = "=== Preamble ===\n\nTitle: Peer Reviewed\n\nReal Title Here\n"
    assert _extract_title(md2) == "Real Title Here"


def test_iter_prop_rows():
    specs = build_specs()
    skey = "thermoelectric:seebeck_coefficient"
    spec = specs[skey]
    perf = {
        "materials": [
            {"name": "Sc0.3Al0.7N",
             spec["field"]: [{"S_value": "200", "S_unit": "μV/K", "S_Temperature": 300,
                              "sample_form": "thin_film", "evidence_quote": "S = 200 μV/K",
                              "evidence_page": 4}]},
            {"name": "Bi2Te3",
             spec["field"]: [{"S_value": "150", "S_unit": "µV/K",
                              "S_Temperature": "100", "S_Temp_unit": "°C"}]},
        ]
    }
    rows = _iter_prop_rows(perf, {skey: spec})
    assert len(rows) == 2
    # Sc0.3Al0.7N → family 归一化，单位不变，温度 K
    assert rows[0]["material_family"] == "ScAlN"
    assert rows[0]["value"] == 200.0 and rows[0]["unit"] == "μV/K"
    assert rows[0]["temp_K"] == 300.0
    assert rows[0]["sample_form"] == "thin_film"
    assert rows[0]["evidence_quote"] == "S = 200 μV/K"
    # Bi2Te3 温度 100°C → 373.15 K（family 去化学计量下标）
    assert rows[1]["material_family"] == "BiTe"
    assert rows[1]["temp_K"] == 373.15
    # 无数值材料行被跳过
    assert _iter_prop_rows({"materials": [{"name": "X"}]}, {skey: spec}) == []


def test_iter_struct_rows():
    struct = {
        "materials": [
            {"name": "Sc0.3Al0.7N", "processing_method": "MOCVD (PAMBE)",
             "crystal_structure": "Wurtzite", "doping": {"dopants": ["Ge"]}},
        ]
    }
    rows = _iter_struct_rows(struct)
    assert len(rows) == 1
    assert rows[0]["material_family"] == "ScAlN"
    assert rows[0]["processing_method_norm"] == "mocvd"
    assert rows[0]["crystal_norm"] == "wurtzite"
    assert rows[0]["dopants"] == ["Ge"]


# ============================================================
# stages/gap/adjudicate.py
# ============================================================

def test_fmt_candidate():
    c = {"id": "sc-001", "type": "contradiction", "statement": "diff structure",
         "evidence": [{"doi": "10.1/a", "detail": "structure=wurtzite"}]}
    text = _fmt_candidate(c)
    assert "[sc-001]" in text and "diff structure" in text and "10.1/a" in text


# ============================================================
# stages/extraction/judge.py
# ============================================================

def test_normalize_judge_key():
    assert normalize_judge_key(" S_value ") == "s"            # strip + lower + 去 _value 后缀
    assert normalize_judge_key("S_value") == "s"
    assert normalize_judge_key("k2_Temperature") == "k2"
    assert normalize_judge_key("K2") == "k2"


def test_build_property_map():
    pmap = build_property_map("thermoelectric")
    # 符号 / 字段 / 属性 id 三条别名都应指向同一 spec
    s = pmap["s"]
    assert pmap["seebeck_coefficient"] is s
    assert pmap["seebeck_coefficient"] is s


# ============================================================
# llm_utils JSON 鲁棒解析（item 4 新增）
# ============================================================

def test_parse_json_text():
    assert parse_json_text('{"a": 1}') == {"a": 1}
    assert parse_json_text('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_text('回答：{"a": 1} 完毕') == {"a": 1}
    assert parse_json_text('{"a": [1, 2,], }') == {"a": [1, 2]}   # 尾部逗号
    assert parse_json_text('[1, 2]') == [1, 2]
    assert parse_json_text('None 与 true 混入：{"x": None}') == {"x": None}
    assert parse_json_text('{"a": {"b": 2}} trailing text') == {"a": {"b": 2}}
    # AIMessage 形态
    msg = type("Msg", (), {"content": '{"ok": 1}'})()
    assert parse_json_text(msg) == {"ok": 1}
    # 完全无法解析 → ValueError
    try:
        parse_json_text("完全没有 JSON 结构的内容")
        assert False, "should raise"
    except ValueError:
        pass


def test_robust_json_parse_default():
    assert robust_json_parse('{"materials": [1]}') == {"materials": [1]}
    assert robust_json_parse("垃圾内容") == {"materials": []}
    # default=None 是"用标准兜底"的哨兵
    assert robust_json_parse("垃圾内容", default=None) == {"materials": []}
    assert robust_json_parse("垃圾内容", default={"x": 1}) == {"x": 1}
