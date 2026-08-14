"""
工艺（process）提取规则注册表。

定义 PROCESS_EXTRACTION：
- label:        中文名称
- keywords:     工艺类关键词（供分类门与文档使用）
- prompts:      两条完整 prompt 模板
                   classify: 论文类型路由（process/property/both/none）+ 属性域识别
                   process:  工艺步骤 + 材料优势提取

由 stages/extraction/process_extract.py 使用；classify 的 system 段含
{domain_descriptions} 占位符，由工具侧从 PROPERTY_DOMAINS 动态拼装。
"""

PROCESS_EXTRACTION = {
    "label": "工艺",
    "keywords": [
        "制备方法（MBE/溅射/CVD/溶胶凝胶/烧结/沉积/退火/刻蚀）",
        "前驱体/原料", "工艺参数（温度/时间/气压/气氛/流量/功率）",
        "衬底", "后处理",
    ],
    "prompts": {
        # === 论文类型路由 ===
        "classify": {
            "system": """
你是一个科研论文路由器。给定论文全文，判断其中包含哪一类可提取信息。

可提取信息类型：
{domain_descriptions}

判断规则：
- 同时包含明确的【工艺/制备信息】和【材料性能数据】→ "both"
- 只有明确的【工艺/制备信息】（如制备方法、前驱体/原料、工艺参数、衬底、后处理、工艺流程步骤）→ "process"
- 只有【材料性能数据】→ "property"
- 两者都无（纯模拟/建模/理论推导/综述）→ "none"

property_domain 字段：仅当 route 为 "property" 或 "both" 时，从上述属性域标识中
选出最匹配的一个；否则为 null。

返回 JSON（严格双引号）：
{{
  "route": "process|property|both|none",
  "property_domain": "<属性域标识|null>",
  "reason": "一句话理由"
}}
""",
            "user": """
文本：
```{fulltext}```
""",
        },
        # === 工艺 + 优势提取 ===
        "process": {
            "system": """
你是一个材料制备工艺提取助手。从论文全文中提取两类信息：

1) 工艺流程（process）：按论文描述的顺序，输出制备/合成/沉积的步骤列表。
   每步包含：
   - step: 步骤序号（从 1 开始）
   - type: 步骤类型，取值之一
     precursor_preparation（前驱体/原料准备）| mixing（混合/配料）|
     deposition（沉积/生长）| annealing（退火）| sintering（烧结）|
     patterning（图形化/刻蚀）| post_treatment（后处理）|
     characterization（表征）| other（其他）
   - action: 该步骤做什么（简短描述）
   - parameters: 该步骤的关键工艺参数，如温度、时间、气压/真空度、气氛/气体流量、
     前驱体/原料、衬底、浓度、功率等，用对象 {{参数名: 值}} 表示；未提及的省略或 null
   - notes: 备注（可为空字符串）

2) 材料优势（materials）：论文明确声称的每种主要材料的优势。
   每项包含：
   - name: 材料名称
   - advantages: 优势列表（性能优势、工艺优势或应用优势，来自论文陈述）
   - source: 一句原文佐证（原句引用）

规则：
- 所有信息必须来自文本；缺失或未提及设为 null 或省略，绝不编造、推断或计算。
- 若文本中没有明确工艺步骤，steps 返回空数组。
- 仅返回合法 JSON，双引号，不加任何其他内容。

返回格式：
{{
  "materials": [
    {{"name": "...", "advantages": ["...", "..."], "source": "..."}}
  ],
  "process": {{
    "process_name": "...",
    "steps": [
      {{"step": 1, "type": "...", "action": "...", "parameters": {{"key": "value"}}, "notes": "..."}}
    ]
  }},
  "notes": "一句话总结制备路线与主要优势"
}}
""",
            "user": """
{material_hint}文本：
```{fulltext}```
""",
        },
    },
}

_PROCESS_GUARDRAILS = """

证据约束：
- 严格区分作者实际执行的工艺、引用他人方法、建议方案和背景描述，只提取前者。
- 每一步必须能在原文中定位；保持先后顺序，不用领域常识补齐缺失步骤。
- 参数保留原值、单位、范围和气氛；材料、衬底、温度、时间等无法绑定到具体步骤时不要强行配对。
- advantages 只记录作者明确声称且有原句支持的优势；不得根据数值自行评价“优异”。
- 只输出合法 JSON，缺失信息使用 null、空字符串或空数组。
"""

for _name, _prompt in PROCESS_EXTRACTION["prompts"].items():
    if _name == "process" and _prompt.get("system"):
        _prompt["system"] = _prompt["system"].rstrip() + _PROCESS_GUARDRAILS
