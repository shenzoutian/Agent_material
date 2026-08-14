"""
铁电属性域（ferroelectric）的提取规则注册表。

定义 PROPERTY_DOMAIN：
- label:              域的中文名称
- material_keywords:  材料发现阶段使用的关键词
- properties:         属性注册表（symbol / field / numeric_key / unit_key / temperature_key）
- prompts:            五条完整 prompt 模板（材料发现/属性提取/结构提取/表格提取/裁判验证）

由 extractor_prompts/__init__.py 聚合到 PROPERTY_DOMAINS。
"""

PROPERTY_DOMAIN = {
    "label": "铁电",
    "material_keywords": [
        "P_r（剩余极化）", "P_s（饱和极化）", "E_c（矫顽场）",
        "T_C（居里温度）", "ε_r（介电常数）", "电滞回线",
    ],
    "properties": {
        "remanent_polarization": {
            "label": "剩余极化",
            "symbol": "P_r",
            "field": "remanent_polarization",
            "numeric_key": "P_r_value",
            "unit_key": "P_r_unit",
            "temperature_key": "P_r_Temperature",
            "temperature_unit_key": "P_r_Temp_unit",
        },
        "saturation_polarization": {
            "label": "饱和极化",
            "symbol": "P_s",
            "field": "saturation_polarization",
            "numeric_key": "P_s_value",
            "unit_key": "P_s_unit",
            "temperature_key": "P_s_Temperature",
            "temperature_unit_key": "P_s_Temp_unit",
        },
        "coercive_field": {
            "label": "矫顽场",
            "symbol": "E_c",
            "field": "coercive_field",
            "numeric_key": "E_c_value",
            "unit_key": "E_c_unit",
            "temperature_key": "E_c_Temperature",
            "temperature_unit_key": "E_c_Temp_unit",
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
你是一个科学文献阅读助手。从以下文本中，列出那些在附近任何位置提及了铁电性能
（如P_r剩余极化、P_s饱和极化、E_c矫顽场、T_C居里温度、ε_r介电常数、电滞回线）的材料名称。

材料名称示例：化合物、合金、掺杂变体，如"PZT"、"BaTiO3"、"BFO"、"PVDF"、"SrBi2Ta2O9"等。

规则：
- 仅包含至少讨论了一种铁电性能的材料。
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
你是一个铁电材料研究提取助手。

请从文本中提取以下每种材料的铁电性能数据：
- name：仅材料名称，不含额外字符串标签。
- P_r（剩余极化）
- P_s（饱和极化）
- E_c（矫顽场）
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
      "remanent_polarization": [{{"P_r_value": ..., "P_r_unit": "...", "P_r_Temperature": "...", "P_r_Temp_unit": "..."}}],
      "saturation_polarization": [{{"P_s_value": ..., "P_s_unit": "...", "P_s_Temperature": "...", "P_s_Temp_unit": "..."}}],
      "coercive_field": [{{"E_c_value": ..., "E_c_unit": "...", "E_c_Temperature": "...", "E_c_Temp_unit": "..."}}],
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
你是一个铁电材料结构信息提取助手。

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
你是一个铁电材料科学表格提取助手。

以下是一篇科学论文中所有表格及其标题的集合。

请提取表格中涉及的所有材料，并返回每种材料的以下性能数据：

铁电性能：
- P_r（剩余极化）
- P_s（饱和极化）
- E_c（矫顽场）
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
      "remanent_polarization": [{{"P_r_value": ..., "P_r_unit": "...", "P_r_Temperature": "...", "P_r_Temp_unit": "..."}}],
      "saturation_polarization": [{{"P_s_value": ..., "P_s_unit": "...", "P_s_Temperature": "...", "P_s_Temp_unit": "..."}}],
      "coercive_field": [{{"E_c_value": ..., "E_c_unit": "...", "E_c_Temperature": "...", "E_c_Temp_unit": "..."}}],
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
验证每一项**铁电数值性能**及其**温度上下文**。

仅需验证的铁电性能参数：
- P_r（剩余极化）
- P_s（饱和极化）
- E_c（矫顽场）
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
     "材料名": {{"P_r": [25], "E_c": [50]}}
  }},
  "incorrect": {{
     "材料名": {{"P_r": [40]}}
  }},
  "temp_mismatch": {{
     "材料名": {{"E_c": [{{"value":50,"reported_T":300,"found_T":500}}]}}
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
