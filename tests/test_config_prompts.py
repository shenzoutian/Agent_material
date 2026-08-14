"""config + prompts 注册表一致性测试。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from litdiscovery import config  # noqa: E402
from litdiscovery.config import AGENT_ROLES  # noqa: E402
from litdiscovery.agent.agent_roles.prompts import PROPERTY_DOMAINS  # noqa: E402
from litdiscovery.agent.agent_roles.prompts.registry import ROLE_SUB_PROMPTS, ROLE_SYSTEM_PROMPTS  # noqa: E402
from litdiscovery.agent.agent_roles.prompts.extractor_prompts import PROCESS_EXTRACTION  # noqa: E402


def test_config_reexports():
    assert callable(config.create_agent)
    assert callable(config.get_agent_role)
    assert len(config.AGENT_ROLES) == 7
    assert "synthesis_planner" not in config.AGENT_ROLES
    assert "relate_miner" not in config.AGENT_ROLES
    assert "researcher_agent" in config.AGENT_ROLES
    assert "filter_agent" in config.AGENT_ROLES
    assert "extractor_agent" in config.AGENT_ROLES
    # 死配置已移除：review_agent（确定性规则实现）与 knowledge_indexer（纯词法检索）无 LLM 配置
    assert "review_agent" not in config.AGENT_ROLES
    assert "knowledge_indexer" not in config.AGENT_ROLES
    # 旧角色名已并入新角色，不再作为独立角色存在
    assert "doi_reacher" not in config.AGENT_ROLES
    assert "hyde_expander" not in config.AGENT_ROLES
    assert "doi_choose" not in config.AGENT_ROLES
    assert "data_extractor" not in config.AGENT_ROLES
    assert "process_extractor" not in config.AGENT_ROLES


def test_registry_keys_match_roles():
    # registry 中的角色键都应在 AGENT_ROLES 中（反向不要求：data_extractor/process_extractor 在域内）
    for role in ROLE_SYSTEM_PROMPTS:
        if role == "table_header_classifier":
            continue
        assert role in AGENT_ROLES, f"registry key {role} not in AGENT_ROLES"


def test_get_agent_role_prefers_registry():
    # report_writer 在 registry → 取 registry
    from_registry = config.get_agent_role("report_writer")
    assert len(from_registry) > 50
    from_registry = config.get_agent_role("researcher_agent")
    assert from_registry == ROLE_SYSTEM_PROMPTS["researcher_agent"]
    assert "system_prompt" not in AGENT_ROLES["researcher_agent"]
    assert "system_prompt" not in AGENT_ROLES["filter_agent"]


def test_hyde_sub_prompt():
    # HyDE 迁入 researcher_agent 内部子能力（sub="hyde"）
    hyde = config.get_agent_role("researcher_agent", sub="hyde")
    assert "sub_problems" in hyde and "search_terms" in hyde
    assert hyde == ROLE_SUB_PROMPTS[("researcher_agent", "hyde")]
    # 不存在的 sub 返回空串
    assert config.get_agent_role("researcher_agent", sub="no_such") == ""


def test_property_domains():
    assert set(PROPERTY_DOMAINS.keys()) == {"thermoelectric", "ferroelectric", "piezoelectric", "phasechange"}
    # 每域都有 material_candidates/properties/structure/tables/judge 五组 prompt
    for domain in PROPERTY_DOMAINS.values():
        assert {"material_candidates", "properties", "structure", "tables", "judge"} <= set(domain["prompts"].keys())
    assert {"classify", "process"} <= set(PROCESS_EXTRACTION["prompts"])


def test_extractor_prompts_live_in_dedicated_package():
    prompt_root = (Path(__file__).resolve().parents[1] / "src" / "litdiscovery" /
                   "agent" / "agent_roles" / "prompts")
    for name in ("thermoelectric.py", "ferroelectric.py", "piezoelectric.py",
                 "phasechange.py", "process.py"):
        assert not (prompt_root / name).exists()
        assert (prompt_root / "extractor_prompts" / name).exists()


def test_unknown_role_raises():
    try:
        config.get_agent_role("no_such_role")
        assert False, "should raise"
    except ValueError:
        pass
