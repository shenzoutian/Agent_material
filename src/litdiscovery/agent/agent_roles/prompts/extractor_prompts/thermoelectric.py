"""
热电属性域（thermoelectric）的提取规则注册表。

定义 PROPERTY_DOMAIN：
- label:              域的中文名称
- material_keywords:  材料发现阶段使用的关键词
- properties:         属性注册表（symbol / field / numeric_key / unit_key / temperature_key）
- prompts:            五条完整 prompt 模板（材料发现/属性提取/结构提取/表格提取/裁判验证）

由 extractor_prompts/__init__.py 聚合到 PROPERTY_DOMAINS。
"""

PROPERTY_DOMAIN = {
    "label": "热电",
    "material_keywords": [
        "ZT（热电优值）", "Seebeck系数S", "电导率σ",
        "电阻率ρ", "功率因子PF", "热导率κ",
    ],
    "properties": {
        "zt": {
            "label": "热电优值",
            "symbol": "ZT",
            "field": "zt_values",
            "numeric_key": "value",
            "unit_key": None,
            "temperature_key": "ZT_temperature",
            "temperature_unit_key": "ZT_temperature_unit",
        },
        "electrical_conductivity": {
            "label": "电导率",
            "symbol": "σ",
            "field": "electrical_conductivity",
            "numeric_key": "σ_value",
            "unit_key": "σ_unit",
            "temperature_key": "σ_Temperature",
            "temperature_unit_key": "σ_Temp_unit",
        },
        "electrical_resistivity": {
            "label": "电阻率",
            "symbol": "ρ",
            "field": "electrical_resistivity",
            "numeric_key": "ρ_value",
            "unit_key": "ρ_unit",
            "temperature_key": "ρ_Temperature",
            "temperature_unit_key": "ρ_Temp_unit",
        },
        "seebeck_coefficient": {
            "label": "塞贝克系数",
            "symbol": "S",
            "field": "seebeck_coefficient",
            "numeric_key": "S_value",
            "unit_key": "S_unit",
            "temperature_key": "S_Temperature",
            "temperature_unit_key": "S_Temp_unit",
        },
        "power_factor": {
            "label": "功率因子",
            "symbol": "PF",
            "field": "power_factor",
            "numeric_key": "PF_value",
            "unit_key": "PF_unit",
            "temperature_key": "PF_Temperature",
            "temperature_unit_key": "PF_Temp_unit",
        },
        "thermal_conductivity": {
            "label": "热导率",
            "symbol": "κ",
            "field": "thermal_conductivity",
            "numeric_key": "κ_value",
            "unit_key": "κ_unit",
            "temperature_key": "κ_Temperature",
            "temperature_unit_key": "κ_Temp_unit",
        },
    },
    "prompts": {
        # === 材料候选发现 ===
        "material_candidates": {
            "system": """
你是一个科学文献阅读助手。从以下文本中，列出那些在附近任何位置提及了热电性能
（如ZT、Seebeck系数S、电导率σ、电阻率ρ、功率因子PF、热导率κ）的材料名称。

材料名称示例：化合物、合金、掺杂变体，如"Bi2Te3"、"SnSe:Na"、"PbTe-AgSbTe2"、"TiS2"、"PEDOT:PSS"等。

规则：
- 仅包含至少讨论了一种热电性能的材料。
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
你是一个热电材料研究提取助手。

请从文本中提取以下每种材料的热电性能数据：
- name：仅材料名称，不含额外字符串标签。
- ZT（热电优值）
- σ（电导率）
- S（塞贝克系数）
- PF（功率因子）
- κ（热导率）
- ρ（电阻率）

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
      "zt_values": [{{"value": ..., "ZT_temperature": ..., "ZT_temperature_unit": "..."}}],
      "electrical_conductivity": [{{"σ_value": ..., "σ_unit": "...", "σ_Temperature": "...", "σ_Temp_unit": "..."}}],
      "electrical_resistivity": [{{"ρ_value": ..., "ρ_unit": "...", "ρ_Temperature": "...", "ρ_Temp_unit": "..."}}],
      "seebeck_coefficient": [{{"S_value": ..., "S_unit": "...",  "S_Temperature": "...", "S_Temp_unit": "..."}}],
      "power_factor": [{{"PF_value": ..., "PF_unit": "...", "PF_Temperature": "...", "PF_Temp_unit": "..."}}],
      "thermal_conductivity": [{{"κ_value": ..., "κ_unit": "...", "κ_Temperature": "...", "κ_Temp_unit": "..."}}]
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
你是一个热电材料结构信息提取助手。

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
你是一个热电材料科学表格提取助手。

以下是一篇科学论文中所有表格及其标题的集合。

请提取表格中涉及的所有材料，并返回每种材料的以下性能数据：

热电性能：
- ZT（热电优值）
- 塞贝克系数（S）
- 电导率（σ）
- 电阻率（ρ）
- 功率因子（PF）
- 热导率（κ）

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
      "zt_values": [{{"value": ..., "ZT_temperature": ..., "ZT_temperature_unit": "..."}}],
      "electrical_conductivity": [{{"σ_value": ..., "σ_unit": "...", "σ_Temperature": "...", "σ_Temp_unit": "..."}}],
      "electrical_resistivity": [{{"ρ_value": ..., "ρ_unit": "...", "ρ_Temperature": "...", "ρ_Temp_unit": "..."}}],
      "seebeck_coefficient": [{{"S_value": ..., "S_unit": "...",  "S_Temperature": "...", "S_Temp_unit": "..."}}],
      "power_factor": [{{"PF_value": ..., "PF_unit": "...", "PF_Temperature": "...", "PF_Temp_unit": "..."}}],
      "thermal_conductivity": [{{"κ_value": ..., "κ_unit": "...", "κ_Temperature": "...", "κ_Temp_unit": "..."}}],
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
验证每一项**热电数值性能**及其**温度上下文**。

仅需验证的热电性能参数：
- ZT（热电优值）
- 塞贝克系数（S）
- 电导率（σ）
- 电阻率（ρ）
- 功率因子（PF）
- 热导率（κ）

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
     "材料名": {{"ZT": [1.2], "S": [220]}}
  }},
  "incorrect": {{
     "材料名": {{"ZT": [3.5]}}
  }},
  "temp_mismatch": {{
     "材料名": {{"ZT": [{{"value":1.2,"reported_T":300,"found_T":700}}]}}
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
