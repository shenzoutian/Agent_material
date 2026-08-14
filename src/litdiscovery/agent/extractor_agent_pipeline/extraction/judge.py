"""
stages/extraction/judge.py —— judge_verify_properties（extractor_agent 的裁判验证职责）。

对提取的属性值做 LLM 裁判验证；build_property_map / _normalize_judge_key
提供 judge 输出键归一化支持。
"""

import os
import json
import datetime

from litdiscovery.llm_utils import robust_json_parse, invoke_messages
from litdiscovery.agent.extractor_agent_pipeline.extraction.domain_registry import normalize_domain
from litdiscovery.agent.extractor_agent_pipeline.extraction.prompting import render_prompt_pair


# judge 输出键的后缀集合（归一化时去除）
_JUDGE_KEY_SUFFIXES = ("_value", "_values", "_temperature", "_temp", "_unit")


def build_property_map(domain) -> dict:
    """构造 judge 输出键 → 属性 spec 的映射。

    domain 支持 str（静态四域键 / 动态域 label）或 dict（完整域或注册表 spec），
    统一由 normalize_domain 解析后再遍历 properties。
    每个属性注册三条别名：符号小写 / 字段名小写 / 属性 id 小写；
    字段名以 "_values" 结尾的额外注册去后缀别名。
    """
    dom = normalize_domain(domain)
    pmap = {}
    for pid, spec in dom["properties"].items():
        pmap[spec["symbol"].lower()] = spec
        pmap[spec["field"].lower()] = spec
        pmap[pid.lower()] = spec
        if spec["field"].endswith("_values"):
            pmap[spec["field"][:-len("_values")].lower()] = spec
    return pmap


def normalize_judge_key(key: str) -> str:
    """归一化 judge 输出键：去首尾空白、转小写、去后缀。"""
    k = key.strip().lower()
    for suf in _JUDGE_KEY_SUFFIXES:
        if k.endswith(suf):
            return k[:-len(suf)]
    return k


def judge_verify_properties(fulltext: str,
                            thermo_json: dict = None,
                            structure_json: dict = None,
                            table_json: dict = None,
                            table_data: list = None,
                            deterministic_table_json: dict = None,
                            llm=None,
                            folder_name: str = None,
                            *, domain="thermoelectric",
                            log_path: str = None) -> dict:
    """
    验证属性数值及其温度上下文与原文和表格的一致性。
    同时确认每种材料的结构字段有效性。

    返回清洗后的材料字典，格式与输入 thermo_json 一致。
    """
    # --- Merge all extracted JSON blocks ---
    merged = {"materials": []}
    for block in (thermo_json, structure_json, table_json, deterministic_table_json):
        if block and block.get("materials"):
            merged["materials"].extend(block["materials"])

    if not merged["materials"]:
        return {"materials": [], "deleted": [], "notes": "No materials to judge."}

    # --- Build table context (captions + rows) if available ---
    table_context = ""
    all_tables = table_data or []
    deterministic_records = (deterministic_table_json or {}).get("records", [])

    if all_tables:
        table_context += "\n\n### Table Contexts (from paper):\n"
        for i, t in enumerate(all_tables, 1):
            caption = t.get("caption", "")
            rows = json.dumps(t.get("rows", []), indent=2)
            table_context += f"\nTable {i} Caption:\n{caption}\n\nRows:\n{rows}\n"
    if deterministic_records:
        table_context += "\n\n### Deterministic Table Records (with table and row provenance):\n"
        table_context += json.dumps(deterministic_records, ensure_ascii=False, indent=2)

    # --- 提示词：属性数值 + 温度 + 结构验证（按 domain 注入）---
    system, user = render_prompt_pair(
        domain, "judge",
        fulltext=fulltext,
        table_context=table_context,
        merged_json=json.dumps(merged, indent=2)
    )

    # --- Run model ---
    res = invoke_messages(llm, system, user)

    # --- Parse output safely ---
    try:
        verdict = robust_json_parse(res.content)
        if not isinstance(verdict, dict):
            raise ValueError("Judge output is not a dict")

        correct = verdict.get("correct", {}) or {}
        incorrect = verdict.get("incorrect", {}) or {}
        temp_mismatch = verdict.get("temp_mismatch", {}) or {}
        structure_ok = verdict.get("structure_ok", []) or []
        notes = verdict.get("notes", "")
    except Exception as e:
        with open("judge_error_log.txt", "a", encoding="utf-8") as log:
            log.write("\n" + "=" * 60 + "\n")
            log.write(f"TIME: {datetime.datetime.now().isoformat()}\n")
            log.write(f"FOLDER: {folder_name or os.path.basename(os.getcwd())}\n")
            log.write(f"ERROR: {repr(e)}\n")
            log.write(f"RAW OUTPUT:\n{res.content if hasattr(res, 'content') else str(res)}\n")
            log.write("=" * 60 + "\n")
        print(f"⚠️ Judge parsing failed → {e}")
        return merged  # fallback: keep everything

    # --- 按 domain 的属性键映射定位字段 ---
    pmap = build_property_map(domain)

    # --- Local filtering + validation logging ---
    cleaned = []
    log_lines = []

    for mat in merged["materials"]:
        name = mat.get("name", "")

        mat_incorrect = incorrect.get(name, {})
        mat_temp_mismatch = temp_mismatch.get(name, {})
        mat_correct = correct.get(name, {})

        # Remove incorrect numeric values
        for key, bad_values in mat_incorrect.items():
            spec = pmap.get(normalize_judge_key(key))
            if spec is None:
                continue
            field, nk = spec["field"], spec["numeric_key"]
            if isinstance(mat.get(field), list):
                before = len(mat[field])
                mat[field] = [
                    v for v in mat[field]
                    if not any(
                        (isinstance(bad, (int, float)) and
                         (bad == v.get(nk) or bad == v.get("value")))
                        for bad in bad_values
                    )
                ]
                after = len(mat[field])
                if before > after:
                    for val in bad_values:
                        log_lines.append(f"[removed] {name}.{key}={val}")

        # Remove temperature-mismatched values
        for key, mismatch_list in mat_temp_mismatch.items():
            spec = pmap.get(normalize_judge_key(key))
            if spec is None:
                continue
            field, nk = spec["field"], spec["numeric_key"]
            if isinstance(mat.get(field), list):
                for bad in mismatch_list:
                    val = bad.get("value")
                    mat[field] = [
                        v for v in mat[field]
                        if val != v.get(nk) and val != v.get("value")
                    ]
                    log_lines.append(f"[temp-mismatch] {name}.{key} value={val} "
                                     f"reported_T={bad.get('reported_T')} found_T={bad.get('found_T')}")

        # Log correct and structural info
        for key, ok_values in mat_correct.items():
            for val in ok_values:
                log_lines.append(f"[ok] {name}.{key}={val}")

        if name in structure_ok:
            log_lines.append(f"[structure_ok] {name}")

        cleaned.append(mat)

    # --- Write validation log（写入统一日志会话目录：每批一会话） ---
    if not log_path:
        from litdiscovery.common.logging import session_dir_for_batch
        from litdiscovery.paths import batch_of
        # 从当前工作目录反推批次（judge 常在批次/end_mds 下运行；兜底用 cwd 名）
        session = session_dir_for_batch(batch_of(Path.cwd()))
        log_path = str(session / "judge_validation.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as log:
        log.write("\n" + "=" * 60 + "\n")
        log.write(f"TIME: {datetime.datetime.now().isoformat()}\n")
        log.write(f"FOLDER: {folder_name or os.path.basename(os.getcwd())}\n")
        log.write("\n".join(log_lines))
        log.write("\n" + "=" * 60 + "\n")

    print(f"🧾 Judge log: {len(log_lines)} entries validated for this folder.")
    return {"materials": cleaned, "notes": notes}
