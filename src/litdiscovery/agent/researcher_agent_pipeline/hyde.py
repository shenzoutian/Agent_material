"""
retrieval/hyde.py —— HyDE 需求拆分（researcher_agent 内部子能力）。

把科研需求拆分为子问题并生成扩展检索词（get_agent_role(sub="hyde") 取 prompt）。
executor 的 HyDE 步骤（runbook kind="hyde"）调用 hyde_expand，检索词确定性写入
ctx，经 search_papers 步骤的 {hyde:terms} 模板流入 keywords 参数。
"""

from langchain_core.messages import HumanMessage, SystemMessage

from litdiscovery.config import create_agent, get_agent_role
from litdiscovery.llm_utils import parse_json_text


def _extract_json(text) -> dict:
    """从 LLM 输出提取 JSON 对象（委托 llm_utils.parse_json_text，失败抛 ValueError）。

    只接受 dict 结构（HyDE 子问题展开需要 .get("sub_problems")）；若解析出 list
    或非 dict 结构，抛 ValueError 让调用方走降级分支。
    """
    data = parse_json_text(text)
    if not isinstance(data, dict):
        raise ValueError(f"HyDE 展开需要 dict 结构，得到 {type(data).__name__}")
    return data


def _extract_hyde_terms(expanded) -> list:
    """从 HyDE 展开结果提取全部检索词（子问题 search_terms + overall_terms）。"""
    expanded = expanded or {}
    terms = [t for sp in (expanded.get("sub_problems") or [])
             for t in (sp.get("search_terms") or [])]
    terms += expanded.get("overall_terms") or []
    return [str(t).strip() for t in terms if str(t).strip()]


def hyde_expand(requirement: str, llm=None) -> dict:
    """HyDE 需求拆分：把用户需求拆解为子问题 + 扩展检索词。

    llm 缺省用 researcher_agent（sub=hyde 子 prompt）实例化；
    失败降级为单子问题（不中断流水线）。
    返回结构：{"sub_problems": [{question, search_terms}], "overall_terms": [...]}
    """
    llm = llm or create_agent("researcher_agent")
    try:
        system = get_agent_role("researcher_agent", sub="hyde")
        out = llm.invoke([SystemMessage(content=system),
                          HumanMessage(content=f"科研需求：{requirement}\n请严格只输出 JSON。")])
        data = _extract_json(getattr(out, "content", str(out)))
        sub = data.get("sub_problems") or []
        if not isinstance(sub, list) or not sub:
            raise ValueError("empty sub_problems")
        return {
            "sub_problems": sub,
            "overall_terms": data.get("overall_terms") or [requirement],
        }
    except Exception as e:
        print(f"  [HyDE] 拆分失败({type(e).__name__})，降级为单子问题")
        return {"sub_problems": [{"question": requirement,
                                  "search_terms": [requirement]}],
                "overall_terms": [requirement]}
