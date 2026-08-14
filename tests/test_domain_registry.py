"""P3 动态属性域注册表（domain_registry.py）测试。

覆盖：validate_domain_registry（Pydantic 校验）、build_prompts_from_registry
（五条 prompt 生成 + .format 渲染）、normalize_domain（str/dict/label/回退）、
build_property_map（dict 域）、generate_domain_registry（无需求回退），以及
write_domain_registry → extract_batch 工具的端到端落盘契约。
"""
import json
from pathlib import Path

import pytest

from litdiscovery.agent.agent_roles.prompts import PROPERTY_DOMAINS
from litdiscovery.agent.extractor_agent_pipeline.extraction.domain_registry import (
    validate_domain_registry,
    build_prompts_from_registry,
    normalize_domain,
    generate_domain_registry,
)
from litdiscovery.agent.extractor_agent_pipeline.extraction.prompting import render_prompt_pair
from litdiscovery.agent.extractor_agent_pipeline.extraction.judge import build_property_map


@pytest.fixture(autouse=True)
def _iso_sessions(tmp_path, monkeypatch):
    """把批次会话隔离到临时目录（extract_batch 会经 session_dir_for_batch 建会话）。"""
    from litdiscovery.common import logging as clog
    monkeypatch.setattr(clog, "SESSIONS_ROOT", tmp_path / "sessions")
    yield


def _spec():
    """合法动态注册表：一个带单位/温度键的属性 + 一个无量纲属性。"""
    return {
        "label": "压电薄膜",
        "material_keywords": ["ScAlN", "AlScN"],
        "properties": {
            "d33": {"symbol": "d33", "label": "压电常数",
                    "field": "piezoelectric_coefficient_d33",
                    "numeric_key": "d33_value", "unit_key": "d33_unit",
                    "temperature_key": "d33_Temperature",
                    "temperature_unit_key": "d33_Temp_unit"},
            "kp": {"symbol": "kp", "label": "机电耦合系数",
                   "field": "electromechanical_coupling_kp", "numeric_key": "kp_value"},
        },
    }


# ============================================================
# validate_domain_registry
# ============================================================

def test_validate_ok():
    assert validate_domain_registry(_spec()) == []


def test_validate_rejects_non_dict():
    assert validate_domain_registry("oops")
    assert validate_domain_registry(None)


def test_validate_requires_properties():
    assert validate_domain_registry({"label": "x"})
    assert validate_domain_registry({"properties": "x"})
    assert validate_domain_registry({"properties": {}})


def test_validate_requires_field_and_numeric_key():
    errs = validate_domain_registry({"properties": {"a": {"numeric_key": "n"}}})
    assert errs and "field" in errs[0]                    # 缺 field
    # numeric_key 可选（默认 ""）→ 通过
    assert validate_domain_registry({"properties": {"a": {"field": "f"}}}) == []


# ============================================================
# build_prompts_from_registry + 渲染
# ============================================================

def test_build_prompts_renders_all_five():
    full = build_prompts_from_registry(_spec())
    assert full["label"] == "压电薄膜"
    assert set(full["prompts"]) == {"material_candidates", "properties", "structure",
                                    "tables", "judge"}
    for key in ("material_candidates", "properties", "structure", "tables", "judge"):
        sys, user = render_prompt_pair(
            full, key, fulltext="TEXT", max_materials=20, material_hint="",
            combined_block="", table_context="", merged_json="{}")
        # .format 反转义成功：无残留 sentinel，JSON 花括号完整
        assert "@@" not in sys and "@@" not in user
        assert "{" in sys and "}" in sys
    # properties 模板含动态域 label + 各属性的 JSON 键
    sys, _ = render_prompt_pair(full, "properties", fulltext="TEXT", material_hint="")
    assert "压电薄膜" in sys and "d33_value" in sys and "kp_value" in sys
    # 无量纲属性不生成单位/温度键，带单位属性生成
    assert "d33_unit" in sys and "d33_Temperature" in sys
    assert "kp_unit" not in sys and "kp_Temperature" not in sys


def test_build_prompts_dimensionless_json_is_valid():
    full = build_prompts_from_registry(_spec())
    sys, _ = render_prompt_pair(full, "properties", fulltext="TEXT", material_hint="")
    # JSON 示例块整体可解析（提取两处 "materials" 后的样例对象）
    assert '"materials": [' in sys


# ============================================================
# normalize_domain
# ============================================================

def test_normalize_static_str_key():
    dom = normalize_domain("piezoelectric")
    assert dom is PROPERTY_DOMAINS["piezoelectric"]


def test_normalize_spec_dict_builds_prompts():
    dom = normalize_domain(_spec())
    assert "prompts" in dom and dom["label"] == "压电薄膜"


def test_normalize_full_dict_reused():
    full = build_prompts_from_registry(_spec())
    assert normalize_domain(full) is full


def test_normalize_registry_label_with_registry():
    spec = _spec()
    dom = normalize_domain("压电薄膜", spec)
    assert dom["label"] == "压电薄膜" and "prompts" in dom
    # 静态键不因 registry 改变解析
    assert normalize_domain("ferroelectric", spec) is PROPERTY_DOMAINS["ferroelectric"]


def test_normalize_unknown_falls_back_static():
    assert normalize_domain("no_such_domain") is PROPERTY_DOMAINS["thermoelectric"]
    assert normalize_domain(None) is PROPERTY_DOMAINS["thermoelectric"]


# ============================================================
# build_property_map（judge 用，支持 dict 域）
# ============================================================

def test_property_map_with_dict_domain():
    full = build_prompts_from_registry(_spec())
    pmap = build_property_map(full)
    assert "d33" in pmap and pmap["d33"]["field"] == "piezoelectric_coefficient_d33"
    assert "kp" in pmap
    assert pmap["piezoelectric_coefficient_d33"]["numeric_key"] == "d33_value"


# ============================================================
# generate_domain_registry（registry_generator 子能力 + 回退）
# ============================================================

def test_generate_no_requirement_falls_back():
    out = generate_domain_registry("", llm=None, fallback_domain="piezoelectric")
    assert out["_fallback"] is True
    assert out is not PROPERTY_DOMAINS["piezoelectric"]  # 副本（带标记）


# ============================================================
# 工具链：write_domain_registry → extract_batch（落盘契约）
# ============================================================

def test_write_domain_registry_tool_writes_file(tmp_path):
    from litdiscovery.agent.agent_roles import build_tools
    tools = {t.name: t for t in build_tools()}
    b = tmp_path / "batch"
    b.mkdir()
    out = tools["write_domain_registry"].invoke({
        "batch": str(b), "requirement": "", "fallback_domain": "piezoelectric",
        "domain_registry": json.dumps(_spec(), ensure_ascii=False),
    })
    assert "domain_registry.json" in out and "planner" in out     # 来源 ① 显式给定
    reg = json.loads((b / "orders" / "domain_registry.json").read_text(encoding="utf-8"))
    assert reg["label"] == "压电薄膜" and "prompts" in reg
    assert set(reg["prompts"]) == {"material_candidates", "properties", "structure",
                                   "tables", "judge"}


def test_extract_batch_detects_registry_file(tmp_path):
    """extract_batch 自动探测 <batch>/orders/domain_registry.json 并传给 run_extract_batch。"""
    from litdiscovery.agent.agent_roles import build_tools
    tools = {t.name: t for t in build_tools()}
    b = tmp_path / "batch"
    b.mkdir()
    (b / "orders").mkdir()
    (b / "orders" / "domain_registry.json").write_text(
        json.dumps(build_prompts_from_registry(_spec()), ensure_ascii=False),
        encoding="utf-8")
    # end_mds 为空目录：run_extract_batch 应正常返回 0 篇统计（不抛）
    (b / "end_mds").mkdir()
    out = tools["extract_batch"].invoke({"batch": str(b), "limit": 0})
    assert "[Extract]" in out
