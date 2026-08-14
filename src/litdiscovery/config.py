"""
litdiscovery/config.py —— LLM 模型/密钥配置 + create_agent 工厂。

AGENT_ROLES 只保存模型与运行参数；角色能力/职责描述的唯一事实源在
`agent/agent_roles/registry.py`（ROLE_DESCRIPTIONS），planner 的世界模型在
`agent/orchestrator/agent_directory.py`（其 description 从 registry 派生）。
"""

import os


# 优先级：进程环境变量 > .env 文件 > 代码内默认值（非密钥默认值仅保留非敏感项）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load_dotenv(path: str = None) -> None:
    """读取项目根 .env（若存在）：每行 KEY=VALUE，跳过 # 注释与空行。

    仅用 setdefault，保证真实环境变量优先于 .env。
    """
    path = path or os.path.join(_PROJECT_ROOT, ".env")
    try:
        with open(path, encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if not _line or _line.startswith("#") or "=" not in _line:
                    continue
                _k, _, _v = _line.partition("=")
                _k = _k.strip()
                _v = _v.strip().strip('"').strip("'")
                if _k:
                    os.environ.setdefault(_k, _v)
    except FileNotFoundError:
        pass


_load_dotenv()

# Apify API Key,调用 Academic Paper Scraper,DOI 列表检索与下载。
APIFY_API_KEY = os.environ.get("APIFY_API_KEY", "")
DEFAULT_KEYWORDS = 7
DEFAULT_RESULTS_PER_KEYWORD = 20
DEFAULT_APIFY_MCP_URL = "https://mcp.apify.com/?tools=labrat011/academic-paper-scraper"
DOI_LIST_FILE = "doi_list.txt"
RESULT_JSON_FILE = "doi_reach_results.json"
KEYWORDS_FILE = "keywords.txt"

# OpenAI Deep Research（Responses API）配置。
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_BASE_URL = "https://api.openai.com/v1"  
OPENAI_DEEP_RESEARCH_MODEL = "o4-mini-deep-research"  
OPENAI_DEEP_RESEARCH_MAX_TOOL_CALLS = 12  
OPENAI_DEEP_RESEARCH_POLL_INTERVAL = 5.0  
OPENAI_DEEP_RESEARCH_TIMEOUT = 900.0  

# 文献下载 API 密钥, .env 读取
UNPAYWALL_EMAIL = os.environ.get("UNPAYWALL_EMAIL", "")
ELSEVIER_API_KEY = os.environ.get("ELSEVIER_API_KEY", "")
SPRINGER_NATURE_API_KEY = os.environ.get("SPRINGER_NATURE_API_KEY", "")
IEEE_API_KEY = os.environ.get("IEEE_API_KEY", "")
WILEY_API_KEY = os.environ.get("WILEY_API_KEY", "")
CORE_API_KEY = os.environ.get("CORE_API_KEY", "")
SEMANTIC_SCHOLAR_API_KEY = os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "")
CROSSREF_EMAIL = os.environ.get("CROSSREF_EMAIL", "")
DOWNLOAD_USER_AGENT = os.environ.get(
    "DOWNLOAD_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
)

ACADEMIC_MCP_DOWNLOAD_COMMAND = os.environ.get("ACADEMIC_MCP_DOWNLOAD_COMMAND", "")
PAPER_SEARCH_CLI_DOWNLOAD_COMMAND = os.environ.get("PAPER_SEARCH_CLI_DOWNLOAD_COMMAND", "")

# DeepSeek API 配置
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
# 默认模型参数
DEEPSEEK_MODEL = "deepseek-v4-flash"
DEFAULT_TEMPERATURE = 0.001  
DEFAULT_MAX_TOKENS = 10000    
DEFAULT_REASONING_EFFORT = "low"
DEFAULT_THINKING = {"type": "enabled"}

#  researcher_agent 联网搜索配置
SEARCH_ENABLED = True
SEARCH_QUERY_LIMIT = 3
SEARCH_RESULTS_PER_QUERY = 6
SEARCH_RECENT_YEARS = 4
SEARCH_MAX_TOTAL = 24
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
TAVILY_SEARCH_URL = "https://mcp.tavily.com/mcp"

# 引用雪球检索配置
SNOWBALL_REF_LIMIT = 100            
SNOWBALL_CIT_LIMIT = 100             # 每篇种子引用扩展上限
SNOWBALL_MAX_CANDIDATES = 500        # 雪球候选总上限
SNOWBALL_SAMPLE_PER_PAPER = 10       # researcher 雪球扩容：每篇种子随机抽取的参考文献条数
SEED_KEEP_DEFAULT = 12               # filter_agent 取舍保留下限（实际 = max(12, 关键词数×3)）
DOWNLOAD_N_AUTO_DEFAULT = 300        # --auto 模式默认下载数量上限
SNOWBALL_OPTION_LOW = 100            # 雪球扩展三档：少
SNOWBALL_OPTION_MID = 200           # 雪球扩展三档：中
FULLTEXT_CONCURRENCY = 4             # 全文下载并发度（fetch_fulltext 每批 DOI 并发数）

# 材料数据库验证
MATERIALS_PROJECT_API_KEY = os.environ.get("MATERIALS_PROJECT_API_KEY", "")

# 超大文档保护（内存 / LLM 上下文保护）：文档超过阈值时直接跳过
#   MAX_FULLTEXT_BYTES     end_mds/<doi>/fulltext.md 等文本全文阈值：
#                          extraction 提取 / gap 物化读入前先检查，超限跳过。
#   MIN_FULLTEXT_BYTES     fulltext.md 下限：低于此值视为"仅有摘要/无正文"，
#                          提取前同样跳过——防止仅权限返回摘要的文献占用 LLM 资源。
#   MAX_CONVERT_SRC_BYTES  to_markdown 转换源文件阈值（pdfs/xmls/txts/texs 原始文件）：
#                          超限跳过转换，避免 Docling 转换期内存/时间爆炸。
MAX_FULLTEXT_BYTES = 25 * 1024 * 1024             # 25 MB
MIN_FULLTEXT_BYTES = 20000                        # 20 KB
MAX_CONVERT_SRC_BYTES = 100 * 1024 * 1024         # 100 MB
# 全文"仅有摘要/过小"字符阈值（低于此值标记 .too_small，供提取跳过）
TOO_SMALL_FULLTEXT_CHARS = 2000
# 抽取前全文可用率硬门（end_mds 全文可用率 ≥ 此值才允许提取）
MIN_FULLTEXT_USABLE_RATE = 0.8
# filter_agent 确定性语料质量补齐门槛（0-100）
QUALITY_FLOOR_DEFAULT = 35.0

# Docling OCR 页批大小（PdfPipelineOptions.ocr_batch_size）。检测头 ConvTranspose
# 上采样的内存峰值与批大小成正比：默认 4 在大页/扫描版 PDF 上易触发 onnxruntime
# "bad allocation"（OOM）。降为 1 逐页 OCR，牺牲少量吞吐换取不再崩溃。
OCR_BATCH_SIZE = 1

# 转换 worker 子进程看门狗超时（秒）。单篇 PDF 在子进程内超过该时长无响应即判定
# 卡死（如 onnxruntime 内存耗尽导致的长时间 stall），父进程杀掉 worker 重启并跳过
# 该篇。取值需覆盖首次 Docling 模型加载（~500MB 下载/加载），故偏宽松。
CONVERT_WORKER_TIMEOUT = 900

# AGENT 角色注册表
AGENT_ROLES = {
    "researcher_agent": {
        # 内部子能力：HyDE 需求拆分。
        # retrieval/hyde.py 通过 get_agent_role("researcher_agent", sub="hyde") 取用。
        "model": DEEPSEEK_MODEL,
        "temperature": 0.3,
        "max_tokens": 2048,
        "reasoning_effort": "low",
        "thinking": {"type": "disabled"},
    },

    "filter_agent": {
        "model": DEEPSEEK_MODEL,
        "temperature": 0.2,
        "max_tokens": 2048,
        "reasoning_effort": "high",
        "thinking": {"type": "enabled"},
    },

    "extractor_agent": {
        # 职责：1) 分类门（classify）以 small max_tokens 实例化，路由 process/property/both/none；
        #       2) 属性/结构/表格提取（性能 performance.json / 结构 structure.json）；
        #       3) 工艺提取（process.json 步骤 + 材料优势）；
        #       4) 裁判验证（judge）清洗 performance.json。
        # 具体提取规则/系统提示词统一位于 prompts/extractor_prompts/，
        # 此处只定义基础模型设置（model / temperature / max_tokens / reasoning_effort / thinking）。
        "model": DEEPSEEK_MODEL,
        "temperature": 0.0,
        "max_tokens": DEFAULT_MAX_TOKENS,
        "reasoning_effort": DEFAULT_REASONING_EFFORT,
        "thinking": {"type": "disabled"},
    },

    "gap_concept_extractor": {
        "model": DEEPSEEK_MODEL,
        "temperature": 0.0,
        "max_tokens": 512,
        "reasoning_effort": "low",
        "thinking": {"type": "disabled"},
    },

    "gap_adjudicator": {
        "model": DEEPSEEK_MODEL,
        "temperature": 0.0,
        "max_tokens": 2048,
        "reasoning_effort": "low",
        "thinking": {"type": "disabled"},
    },

    "planner": {
        # 纯路由 planner：由 orchestrator/planner.py 以 create_agent 实例化（普通 ChatOpenAI），
        # 注入 AGENT_DIRECTORY 生成 plan.v3.json，不执行任何工具（执行统一交 executor）。
        "model": DEEPSEEK_MODEL,
        "temperature": 0.3,
        "max_tokens": 4096,
        "reasoning_effort": "high",
        "thinking": {"type": "enabled"},
    },

    "report_writer": {
        "model": DEEPSEEK_MODEL,
        "temperature": 0.0,
        "max_tokens": 8192,
        "reasoning_effort": "low",
        "thinking": {"type": "disabled"},
    },
}


def create_agent(agent_name: str,
                 temperature: float = None,
                 max_tokens: int = None,
                 reasoning_effort: str = None,
                 thinking: dict = None,
                 system_prompt: str = None,
                 **kwargs):
    """
    按 AGENT_ROLES 注册表创建指定 agent 的 ChatOpenAI 实例。
    采用惰性导入，仅在需要时加载 langchain_openai 依赖。

    参数:
        agent_name:       AGENT_ROLES 中的角色名，如 "researcher_agent" / "extractor_agent"
        temperature:      临时覆盖注册表中的采样温度；None 时使用注册表默认值
        max_tokens:       临时覆盖注册表中的最大输出 token；None 时使用注册表默认值
        reasoning_effort: 临时覆盖推理强度 "low"|"high"|"max"；None 时使用注册表默认值。
                          thinking 禁用时本参数不发送
        thinking:         临时覆盖思考模式 {"type":"enabled"|"disabled"}；None 时使用注册表默认值
        system_prompt:    可选。非 None 时作为 ChatOpenAI 的 system 参数绑定。
                          None 时**不传** system（避免与 messages 方式注入的 system 段重复）。
        kwargs:           可选覆盖 api_key / base_url

    返回:
        配置好的 ChatOpenAI 实例

    示例:
        llm = create_agent("researcher_agent")
        llm = create_agent("extractor_agent", max_tokens=4096)
        llm = create_agent("extractor_agent", system_prompt="你是专用提取助手")
    """
    if agent_name not in AGENT_ROLES:
        available = ", ".join(AGENT_ROLES)
        raise ValueError(f"未知 agent: {agent_name}。已注册: {available}")
    cfg = AGENT_ROLES[agent_name]

    # 解析思考模式与推理强度（思考禁用时不发送 reasoning_effort）
    think = thinking if thinking is not None else cfg.get("thinking", DEFAULT_THINKING)
    effort = reasoning_effort if reasoning_effort is not None else cfg.get(
        "reasoning_effort", DEFAULT_REASONING_EFFORT)
    think_disabled = isinstance(think, dict) and think.get("type") == "disabled"
    extra_body = {"thinking": think} if think is not None else None

    from langchain_openai import ChatOpenAI
    llm_kwargs = dict(
        model=cfg.get("model", DEEPSEEK_MODEL),
        api_key=kwargs.get("api_key", DEEPSEEK_API_KEY),
        base_url=kwargs.get("base_url", DEEPSEEK_BASE_URL),
        temperature=cfg["temperature"] if temperature is None else temperature,
        max_tokens=cfg["max_tokens"] if max_tokens is None else max_tokens,
        reasoning_effort=None if think_disabled else effort,
        extra_body=extra_body,
    )
    # 仅当显式传入 system_prompt 时才绑定 system，避免与 messages 注入重复
    if system_prompt is not None:
        llm_kwargs["system"] = system_prompt
    return ChatOpenAI(**llm_kwargs)


def get_agent_role(agent_name: str, sub: str = None) -> str:
    """获取指定 agent 的系统提示词。

    sub=None：从 prompts.registry.ROLE_SYSTEM_PROMPTS 获取主提示词。
    sub 非 None：从 ROLE_SUB_PROMPTS 获取内部子能力提示词（如 HyDE）。
    config 只保存模型和运行参数，不保存提示词正文。
    """
    if agent_name not in AGENT_ROLES:
        available = ", ".join(AGENT_ROLES)
        raise ValueError(f"未知 agent: {agent_name}。已注册: {available}")
    from litdiscovery.agent.agent_roles.prompts.registry import ROLE_SUB_PROMPTS, ROLE_SYSTEM_PROMPTS
    if sub:
        return ROLE_SUB_PROMPTS.get((agent_name, sub), "")
    return ROLE_SYSTEM_PROMPTS.get(agent_name, "")
