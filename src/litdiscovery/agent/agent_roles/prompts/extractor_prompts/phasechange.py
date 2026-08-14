"""
相变材料（phasechange）属性域的提取规则注册表。

针对相变存储器（PCM）与相变材料：
- 相变热学：T_x（结晶温度）、T_m（熔点）、T_retention（数据保持温度）
- 电学对比：ρ_amor / ρ_cry（非晶/晶态电阻率）、R_ratio（电阻率对比度）
- 器件/热学：κ（热导率）、V_th（阈值开关电压）、t_sw（置/复位时间）、endurance（循环寿命）

定义 PROPERTY_DOMAIN：
- label:              域的中文名称
- material_keywords:  材料发现阶段使用的关键词
- properties:         属性注册表（symbol / field / numeric_key / unit_key / temperature_key）
- prompts:            五条完整 prompt 模板（材料发现/属性提取/结构提取/表格提取/裁判验证）

由 extractor_prompts/__init__.py 聚合到 PROPERTY_DOMAINS。
"""

PROPERTY_DOMAIN = {
    "label": "相变",
    "material_keywords": [
        "T_x（结晶温度）", "T_m（熔点）", "非晶/晶态电阻率",
        "V_th（阈值开关电压）", "数据保持温度", "PCM/相变存储器", "GST/Ge2Sb2Te5",
    ],
    "properties": {
        "crystallization_temperature": {
            "label": "结晶温度",
            "symbol": "T_x",
            "field": "crystallization_temperature",
            "numeric_key": "T_x_value",
            "unit_key": "T_x_unit",
            "temperature_key": None,
            "temperature_unit_key": None,
        },
        "melting_temperature": {
            "label": "熔点",
            "symbol": "T_m",
            "field": "melting_temperature",
            "numeric_key": "T_m_value",
            "unit_key": "T_m_unit",
            "temperature_key": None,
            "temperature_unit_key": None,
        },
        "amorphous_resistivity": {
            "label": "非晶态电阻率",
            "symbol": "ρ_amor",
            "field": "amorphous_resistivity",
            "numeric_key": "ρ_amor_value",
            "unit_key": "ρ_amor_unit",
            "temperature_key": "ρ_amor_Temperature",
            "temperature_unit_key": "ρ_amor_Temp_unit",
        },
        "crystalline_resistivity": {
            "label": "晶态电阻率",
            "symbol": "ρ_cry",
            "field": "crystalline_resistivity",
            "numeric_key": "ρ_cry_value",
            "unit_key": "ρ_cry_unit",
            "temperature_key": "ρ_cry_Temperature",
            "temperature_unit_key": "ρ_cry_Temp_unit",
        },
        "resistivity_contrast": {
            "label": "电阻率对比度",
            "symbol": "R_ratio",
            "field": "resistivity_contrast",
            "numeric_key": "R_ratio_value",
            "unit_key": None,
            "temperature_key": None,
            "temperature_unit_key": None,
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
        "threshold_switching_voltage": {
            "label": "阈值开关电压",
            "symbol": "V_th",
            "field": "threshold_switching_voltage",
            "numeric_key": "V_th_value",
            "unit_key": "V_th_unit",
            "temperature_key": "V_th_Temperature",
            "temperature_unit_key": "V_th_Temp_unit",
        },
        "data_retention_temperature": {
            "label": "数据保持温度",
            "symbol": "T_retention",
            "field": "data_retention_temperature",
            "numeric_key": "T_retention_value",
            "unit_key": "T_retention_unit",
            "temperature_key": None,
            "temperature_unit_key": None,
        },
        "switching_speed": {
            "label": "置/复位时间",
            "symbol": "t_sw",
            "field": "switching_speed",
            "numeric_key": "t_sw_value",
            "unit_key": "t_sw_unit",
            "temperature_key": None,
            "temperature_unit_key": None,
        },
        "endurance_cycles": {
            "label": "循环寿命",
            "symbol": "endurance",
            "field": "endurance_cycles",
            "numeric_key": "endurance_value",
            "unit_key": "endurance_unit",
            "temperature_key": None,
            "temperature_unit_key": None,
        },
    },
    "prompts": {
        # === 材料候选发现 ===
        "material_candidates": {
            "system": """
你是一个科学文献阅读助手。从以下文本中，列出那些在附近任何位置提及了相变性能
（如T_x结晶温度、T_m熔点、非晶/晶态电阻率及对比度、V_th阈值开关电压、数据保持温度、
相变存储器PCM、GST/Ge2Sb2Te5等）的材料名称。

材料名称示例：化合物、合金、掺杂变体，如"Ge2Sb2Te5"、"Sb2Te3"、"GeTe"、
"AgInSbTe"、"Sc2(Sb2Te3)"、"Bi2Te3"、"GST-225"等。

规则：
- 仅包含至少讨论了一种相变性能的材料。
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
你是一个相变材料研究提取助手。

请从文本中提取以下每种材料的相变性能数据：
- name：仅材料名称，不含额外字符串标签。
- T_x（结晶温度，非晶→晶态转变温度）
- T_m（熔点）
- ρ_amor（非晶态电阻率）
- ρ_cry（晶态电阻率）
- R_ratio（非晶/晶态电阻率对比度，无量纲）
- κ（热导率）
- V_th（阈值开关电压）
- T_retention（数据保持温度）
- t_sw（置/复位时间）
- endurance（循环寿命）

对于每个性能参数，请提取数值、单位以及与该数值直接绑定的实验/计算上下文：
- sample_form：thin_film/bulk/nanostructure/device/unknown
- phase_state：amorphous/crystalline/cubic/hexagonal/mixed/unknown
- measurement_method：DSC、four-probe、pump-probe、electrical pulse 等原文方法
- heating_rate：仅用于热分析且原文明确给出时填写
- film_thickness：薄膜厚度及单位
- pulse_width：开关速度/阈值测量对应的脉冲宽度及单位
- pulse_type：SET/RESET/unknown
- crystallization_definition：onset/peak/unknown，仅用于结晶温度
- value_origin：experimental/calculated/cited/unknown，区分实验、计算和转引值
- endurance_basis：measured/extrapolated/unknown，仅用于 endurance
- evidence_quote：包含材料、性能值和关键条件的最短充分原句
- evidence_section、evidence_page、evidence_table：能够从文本或表格标题确定时填写
- evidence_table_row、evidence_table_column：表格行号与数值列名；无则 null
注意区分非晶态与晶态电阻率；若论文同时给出两者，务必分别填入对应字段。

注意事项：
- 缺失值必须严格设为 null。
- 仅包含材料名称，不含额外的字符串标签。
- 不要自行进行任何计算或单位换算。
- 如果存在多个值，将它们作为独立的字典条目全部返回。
- 不同升温速率、膜厚、相态、脉冲条件或实验/计算方法的值必须拆成独立条目。
- T_x 区分 onset/peak 值，在 measurement_method 或 evidence_quote 中保留定义。
- t_sw 区分 SET/RESET；endurance 区分实测循环数和外推值，不得合并。
- evidence_quote 必须逐字来自输入；无法定位证据时设为 null，不得改写或生成引文。
- 如找到超过10种材料，只保留前10种。
- 所有字段名和字符串值必须使用**合法的JSON语法**（双引号）。
- 数值保持为数字类型，不加引号（即非字符串）。
- 输出中严格不包含其他任何内容。

返回结构化JSON格式：
{{
  "materials": [
    {{
      "name": "...",
      "crystallization_temperature": [{{"T_x_value": ..., "T_x_unit": "...", "crystallization_definition": "onset|peak|unknown", "value_origin": "experimental|calculated|cited|unknown", "sample_form": "...", "phase_state": "...", "measurement_method": "...", "heating_rate": "...", "film_thickness": "...", "evidence_quote": "...", "evidence_section": "...", "evidence_page": null, "evidence_table": null, "evidence_table_row": null, "evidence_table_column": null}}],
      "melting_temperature": [{{"T_m_value": ..., "T_m_unit": "..."}}],
      "amorphous_resistivity": [{{"ρ_amor_value": ..., "ρ_amor_unit": "...", "ρ_amor_Temperature": "...", "ρ_amor_Temp_unit": "..."}}],
      "crystalline_resistivity": [{{"ρ_cry_value": ..., "ρ_cry_unit": "...", "ρ_cry_Temperature": "...", "ρ_cry_Temp_unit": "..."}}],
      "resistivity_contrast": [{{"R_ratio_value": ...}}],
      "thermal_conductivity": [{{"κ_value": ..., "κ_unit": "...", "κ_Temperature": "...", "κ_Temp_unit": "..."}}],
      "threshold_switching_voltage": [{{"V_th_value": ..., "V_th_unit": "...", "V_th_Temperature": "...", "V_th_Temp_unit": "..."}}],
      "data_retention_temperature": [{{"T_retention_value": ..., "T_retention_unit": "..."}}],
      "switching_speed": [{{"t_sw_value": ..., "t_sw_unit": "...", "pulse_type": "SET|RESET|unknown", "pulse_width": "...", "sample_form": "...", "evidence_quote": "...", "evidence_section": "...", "evidence_page": null, "evidence_table": null}}],
      "endurance_cycles": [{{"endurance_value": ..., "endurance_unit": "...", "endurance_basis": "measured|extrapolated|unknown", "value_origin": "experimental|calculated|cited|unknown"}}]
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
你是一个相变材料结构信息提取助手。

对于每种材料，请提取以下结构信息：
- name：仅材料名称, 不含额外字符串标签。
- compound_type、crystal_structure、lattice_structure
- space_group
- doping_type and dopants
- processing_method

注意：相变材料常同时存在非晶态（amorphous）与晶态（六方/立方），
请分别保留论文明确讨论的相态。若同一材料同时有非晶态和多个晶态，使用 phase_states 数组；
不要用一个“优先晶态”字段覆盖其他相。结构证据需给出 evidence_quote 和可定位信息。

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
      ,"phase_states": [{{"phase_state": "...", "crystal_structure": "...", "space_group": "...", "conditions": "...", "evidence_quote": "...", "evidence_section": "...", "evidence_page": null}}]
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
你是一个相变材料科学表格提取助手。

以下是一篇科学论文中所有表格及其标题的集合。

请提取表格中涉及的所有材料，并返回每种材料的以下性能数据：

相变性能：
- T_x（结晶温度）
- T_m（熔点）
- ρ_amor（非晶态电阻率）
- ρ_cry（晶态电阻率）
- R_ratio（电阻率对比度）
- κ（热导率）
- V_th（阈值开关电压）
- T_retention（数据保持温度）
- t_sw（置/复位时间）
- endurance（循环寿命）

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
- 每个数值条目附带 sample_form、phase_state、measurement_method、film_thickness、
  heating_rate/pulse_width（适用时）以及 evidence_quote/evidence_page/evidence_table。
- 表格证据的 evidence_table 使用表号或标题，evidence_quote 使用对应行的紧凑文本表示。
- 输入行中的 __table_row 必须原样写入 evidence_table_row，数值所在列名写入 evidence_table_column。
- 所有字段值必须遵循**合法的JSON语法**，使用双引号。
- 输出中严格不包含其他任何内容。

返回结构化JSON格式：
{{
  "materials": [
    {{
      "name": "...",
      "crystallization_temperature": [{{"T_x_value": ..., "T_x_unit": "...", "sample_form": "...", "phase_state": "...", "measurement_method": "...", "heating_rate": "...", "film_thickness": "...", "evidence_quote": "...", "evidence_table": "..."}}],
      "melting_temperature": [{{"T_m_value": ..., "T_m_unit": "..."}}],
      "amorphous_resistivity": [{{"ρ_amor_value": ..., "ρ_amor_unit": "...", "ρ_amor_Temperature": "...", "ρ_amor_Temp_unit": "..."}}],
      "crystalline_resistivity": [{{"ρ_cry_value": ..., "ρ_cry_unit": "...", "ρ_cry_Temperature": "...", "ρ_cry_Temp_unit": "..."}}],
      "resistivity_contrast": [{{"R_ratio_value": ...}}],
      "thermal_conductivity": [{{"κ_value": ..., "κ_unit": "...", "κ_Temperature": "...", "κ_Temp_unit": "..."}}],
      "threshold_switching_voltage": [{{"V_th_value": ..., "V_th_unit": "...", "V_th_Temperature": "...", "V_th_Temp_unit": "..."}}],
      "data_retention_temperature": [{{"T_retention_value": ..., "T_retention_unit": "..."}}],
      "switching_speed": [{{"t_sw_value": ..., "t_sw_unit": "..."}}],
      "endurance_cycles": [{{"endurance_value": ..., "endurance_unit": "..."}}],
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
验证每一项**相变数值性能**及其**温度上下文**。

仅需验证的相变性能参数：
- T_x（结晶温度）
- T_m（熔点）
- ρ_amor / ρ_cry（非晶/晶态电阻率）
- R_ratio（电阻率对比度）
- κ（热导率）
- V_th（阈值开关电压）
- T_retention（数据保持温度）
- t_sw（置/复位时间）
- endurance（循环寿命）

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
     "材料名": {{"T_x": [150], "R_ratio": [1e5]}}
  }},
  "incorrect": {{
     "材料名": {{"T_x": [200]}}
  }},
  "temp_mismatch": {{
     "材料名": {{"ρ_cry": [{{"value":1e-3,"reported_T":300,"found_T":500}}]}}
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
