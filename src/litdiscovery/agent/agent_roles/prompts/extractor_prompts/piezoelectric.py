"""
压电属性域（piezoelectric）的提取规则注册表。

定义 PROPERTY_DOMAIN：
- label:              域的中文名称
- material_keywords:  材料发现阶段使用的关键词
- properties:         属性注册表（symbol / field / numeric_key / unit_key / temperature_key）
- prompts:            五条完整 prompt 模板（材料发现/属性提取/结构提取/表格提取/裁判验证）

由 extractor_prompts/__init__.py 聚合到 PROPERTY_DOMAINS。
"""

PROPERTY_DOMAIN = {
    "label": "压电",
    "material_keywords": [
        "d_33（压电常数）", "d_31（压电常数）", "k_p（机电耦合系数）",
        "k_33（机电耦合系数）", "Q_m（机械品质因数）", "T_C（居里温度）",
    ],
    "properties": {
        "piezoelectric_coefficient_d33": {
            "label": "压电常数d33",
            "symbol": "d_33",
            "field": "piezoelectric_coefficient_d33",
            "numeric_key": "d_33_value",
            "unit_key": "d_33_unit",
            "temperature_key": "d_33_Temperature",
            "temperature_unit_key": "d_33_Temp_unit",
        },
        "piezoelectric_coefficient_d31": {
            "label": "压电常数d31",
            "symbol": "d_31",
            "field": "piezoelectric_coefficient_d31",
            "numeric_key": "d_31_value",
            "unit_key": "d_31_unit",
            "temperature_key": "d_31_Temperature",
            "temperature_unit_key": "d_31_Temp_unit",
        },
        "electromechanical_coupling_kp": {
            "label": "平面机电耦合系数",
            "symbol": "k_p",
            "field": "electromechanical_coupling_kp",
            "numeric_key": "k_p_value",
            "unit_key": None,
            "temperature_key": "k_p_Temperature",
            "temperature_unit_key": "k_p_Temp_unit",
        },
        "electromechanical_coupling_k33": {
            "label": "纵向机电耦合系数",
            "symbol": "k_33",
            "field": "electromechanical_coupling_k33",
            "numeric_key": "k_33_value",
            "unit_key": None,
            "temperature_key": "k_33_Temperature",
            "temperature_unit_key": "k_33_Temp_unit",
        },
        "mechanical_quality_factor": {
            "label": "机械品质因数",
            "symbol": "Q_m",
            "field": "mechanical_quality_factor",
            "numeric_key": "Q_m_value",
            "unit_key": None,
            "temperature_key": "Q_m_Temperature",
            "temperature_unit_key": "Q_m_Temp_unit",
        },
        "curie_temperature": {
            "label": "居里温度",
            "symbol": "T_C",
            "field": "curie_temperature",
            "numeric_key": "T_C_value",
            "unit_key": "T_C_unit",
            "temperature_key": None,
            "temperature_unit_key": None,
        },
        "dielectric_permittivity": {
            "label": "相对介电常数",
            "symbol": "ε_r",
            "field": "dielectric_permittivity",
            "numeric_key": "ε_r_value",
            "unit_key": None,
            "temperature_key": "ε_r_Temperature",
            "temperature_unit_key": "ε_r_Temp_unit",
        },
    },
    "prompts": {
        # === 材料候选发现 ===
        "material_candidates": {
            "system": """
你是一个科学文献阅读助手。从以下文本中，列出那些在附近任何位置提及了压电性能
（如d_33压电常数、d_31压电常数、k_p机电耦合系数、k_33机电耦合系数、Q_m机械品质因数、T_C居里温度）的材料名称。

材料名称示例：化合物、合金、掺杂变体，如"PZT"、"BaTiO3"、"LiNbO3"、"AlN"、"ZnO"、"PVDF"等。

规则：
- 仅包含至少讨论了一种压电性能的材料。
- 保留原文中的名称写法（包括相关的掺杂剂/相标签）。
- 返回一个包含单个数组"materials"的JSON对象。
- 去重处理。

返回JSON格式：
{{
  "materials": ["...", "..."]
}}
""",
            "user": """
最多返回前{max_materials}项。

文本：
```{fulltext}```
""",
        },
        # === 属性提取 ===
        "properties": {
            "system": """
你是一个压电材料研究提取助手。

请从文本中提取以下每种材料的压电性能数据：
- name：仅材料名称，不含额外字符串标签。
- d_33（压电常数）
- d_31（压电常数）
- k_p（平面机电耦合系数）
- k_33（纵向机电耦合系数）
- Q_m（机械品质因数）
- T_C（居里温度）
- ε_r（相对介电常数）

对于每个性能参数，请提取**数值**及其对应的**温度**和**单位**（如有提及）。

注意事项：
- 缺失值必须严格设为 null。
- 仅包含材料名称，不含额外的字符串标签。
- 不要自行进行任何计算或单位换算。
- 如果存在多个值，将它们作为独立的字典条目全部返回。
- 如找到超过10种材料，只保留前10种。
- 所有字段名和字符串值必须使用**合法的JSON语法**（双引号）。
- 数值保持为数字类型，不加引号（即非字符串）。
- 输出中严格不包含其他任何内容。

返回结构化JSON格式：
{{
  "materials": [
    {{
      "name": "...",
      "piezoelectric_coefficient_d33": [{{"d_33_value": ..., "d_33_unit": "...", "d_33_Temperature": "...", "d_33_Temp_unit": "..."}}],
      "piezoelectric_coefficient_d31": [{{"d_31_value": ..., "d_31_unit": "...", "d_31_Temperature": "...", "d_31_Temp_unit": "..."}}],
      "electromechanical_coupling_kp": [{{"k_p_value": ..., "k_p_Temperature": "...", "k_p_Temp_unit": "..."}}],
      "electromechanical_coupling_k33": [{{"k_33_value": ..., "k_33_Temperature": "...", "k_33_Temp_unit": "..."}}],
      "mechanical_quality_factor": [{{"Q_m_value": ..., "Q_m_Temperature": "...", "Q_m_Temp_unit": "..."}}],
      "curie_temperature": [{{"T_C_value": ..., "T_C_unit": "..."}}],
      "dielectric_permittivity": [{{"ε_r_value": ..., "ε_r_Temperature": "...", "ε_r_Temp_unit": "..."}}]
    }}
  ]
}}
""",
            "user": """
{material_hint}文本：
```{fulltext}```
""",
        },
        # === 结构提取 ===
        "structure": {
            "system": """
你是一个压电材料结构信息提取助手。

对于每种材料，请提取以下结构信息：
- name：仅材料名称, 不含额外字符串标签。
- compound_type、crystal_structure、lattice_structure
- space_group
- doping_type and dopants
- processing_method

注意事项：
- 缺失值必须严格设为 null。
- 仅包含材料名称，不含额外的字符串标签。
- 如找到超过10种材料，只保留前10种。
- 所有字段名和字符串值必须使用**合法的JSON语法**（双引号）。
- 所有字段值必须遵循**合法的JSON语法**，使用双引号。
- 输出中严格不包含其他任何内容。

返回JSON格式：
{{
  "materials": [
    {{
      "name": "...",
      "compound_type": "<类型|null>",
      "crystal_structure": "<结构|null>",
      "lattice_structure": "<结构|null>",
      "space_group": "<群组|null>",
      "doping": {{
        "doping_type": "<类型|null>",
        "dopants": [<字符串列表>]
      }},
      "processing_method": "<字符串|null>"
    }}
  ]
}}
""",
            "user": """
{material_hint}文本：
```{fulltext}```
""",
        },
        # === 表格提取 ===
        "tables": {
            "system": """
你是一个压电材料科学表格提取助手。

以下是一篇科学论文中所有表格及其标题的集合。

请提取表格中涉及的所有材料，并返回每种材料的以下性能数据：

压电性能：
- d_33（压电常数）
- d_31（压电常数）
- k_p（平面机电耦合系数）
- k_33（纵向机电耦合系数）
- Q_m（机械品质因数）
- T_C（居里温度）
- ε_r（相对介电常数）

结构信息：
- compound_type（化合物类型）
- crystal_structure（晶体结构）
- lattice_structure（晶格结构）
- space_group（空间群）
- doping_type（掺杂类型）及dopants（掺杂剂列表）
- processing_method（加工方法）

注意事项：
- 缺失值必须严格设为 null。
- 仅包含材料名称，不含额外的字符串标签。
- 如找到超过10种材料，只保留前10种。
- 所有字段名和字符串值必须使用**合法的JSON语法**（双引号）。
- 所有缺失值必须严格明确设为 null。
- 所有字段值必须遵循**合法的JSON语法**，使用双引号。
- 输出中严格不包含其他任何内容。

返回结构化JSON格式：
{{
  "materials": [
    {{
      "name": "...",
      "piezoelectric_coefficient_d33": [{{"d_33_value": ..., "d_33_unit": "...", "d_33_Temperature": "...", "d_33_Temp_unit": "..."}}],
      "piezoelectric_coefficient_d31": [{{"d_31_value": ..., "d_31_unit": "...", "d_31_Temperature": "...", "d_31_Temp_unit": "..."}}],
      "electromechanical_coupling_kp": [{{"k_p_value": ..., "k_p_Temperature": "...", "k_p_Temp_unit": "..."}}],
      "electromechanical_coupling_k33": [{{"k_33_value": ..., "k_33_Temperature": "...", "k_33_Temp_unit": "..."}}],
      "mechanical_quality_factor": [{{"Q_m_value": ..., "Q_m_Temperature": "...", "Q_m_Temp_unit": "..."}}],
      "curie_temperature": [{{"T_C_value": ..., "T_C_unit": "..."}}],
      "dielectric_permittivity": [{{"ε_r_value": ..., "ε_r_Temperature": "...", "ε_r_Temp_unit": "..."}}],
      "compound_type": "<类型|null>",
      "crystal_structure": "<结构|null>",
      "lattice_structure": "<结构|null>",
      "space_group": "<群组|null>",
      "doping": {{
        "doping_type": "<类型|null>",
        "dopants": [<字符串列表>]
      }},
      "processing_method": "<字符串|null>"
    }}
  ]
}}

所有缺失值必须严格明确设为 null。
""",
            "user": """
{material_hint}
### 表格及标题：
{combined_block}
""",
        },
        # === 裁判验证 ===
        "judge": {
            "system": """
你是一个科学验证助手。

请对照给定的论文全文、带标题的表格数据以及已提取的材料JSON，
验证每一项**压电数值性能**及其**温度上下文**。

仅需验证的压电性能参数：
- d_33（压电常数）
- d_31（压电常数）
- k_p（平面机电耦合系数）
- k_33（纵向机电耦合系数）
- Q_m（机械品质因数）
- T_C（居里温度）
- ε_r（相对介电常数）

数值验证规则：
- 对于每个数值，检查文本或表格中是否出现了相同或相近的值（及其温度，如有提及）。
- 若数值和温度均一致 → 标记为 correct（正确）。
- 若数值正确但温度不一致 → 标记为 temp_mismatch（温度不匹配）。
- 若数值未找到或不合理 → 标记为 incorrect（不正确）。
- 若温度缺失但数值匹配 → 标记为 correct_no_temp（正确但无温度）。
- 绝不凭空编造、求平均或修改数字。

结构验证规则：
- 对于每种材料，检查其结构字段（compound_type、crystal_structure、
  lattice_structure、space_group、doping_type、processing_method）
  是否存在且语法有效。
- 若以上字段全部存在或为 null（但格式正确）→ 标记 structure_ok（结构正确）。
- 不得删除结构信息。

返回严格合法的JSON格式：
{{
  "correct": {{
     "材料名": {{"d_33": [220], "k_p": [0.45]}}
  }},
  "incorrect": {{
     "材料名": {{"d_33": [300]}}
  }},
  "temp_mismatch": {{
     "材料名": {{"d_33": [{{"value":220,"reported_T":300,"found_T":500}}]}}
  }},
  "structure_ok": ["材料A","材料B"],
  "notes": "简要说明"
}}

务必包含全部四个顶级键，即使为空也要包含。
所有键和字符串值使用双引号。
整体输出用 {{ }} 包裹。
""",
            "user": """
### 论文全文
```{fulltext}```

### 表格标题与数据
```{table_context}```

### 已提取值的合并JSON
```{merged_json}```
""",
        },
    },
}
