"""Typed request/result contracts for the public Agent pipeline facades.

分层约定（executor 与 Facade 的关系，详见各 facade 模块）：

- Facade（``agent/*_agent_pipeline/pipeline.py`` 的 ``run(request) -> result``）是面向
  程序化调用的**类型化公共 API**，请求/结果契约集中在本模块；
- executor（``agent/orchestrator/pipeline.py`` + ``agent/agent_roles/tools.py``）是
  planner 驱动的**确定性步骤引擎**，其工具步骤内部调用与 Facade 相同的底层阶段函数
  （``api.run_*`` / ``quality.*`` / ``fulltext.*`` 等），因此二者共享实现、不是平行重复；
- researcher/filter 的 Facade 是更精简的程序化入口（不含 HyDE/planner 编排与雪球），
  executor 是完整 planner 链——这是刻意的两层入口，而非实现分歧。
"""

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from litdiscovery.config import MIN_FULLTEXT_USABLE_RATE, QUALITY_FLOOR_DEFAULT


Paper = Mapping[str, Any]


@dataclass(frozen=True)
class ResearcherRequest:
    requirement: str
    batch: str | None = None
    keywords: tuple[str, ...] = ()
    keyword_count: int = 7
    results_per_keyword: int = 20
    use_frontier_search: bool = True
    use_deep_research: bool = False
    use_memory: bool = True
    memory_limit: int = 100
    deep_research_model: str = ""
    deep_research_max_tool_calls: int = 0

    def __post_init__(self):
        if not self.requirement.strip():
            raise ValueError("requirement must not be empty")
        if self.keyword_count < 1 or self.results_per_keyword < 1 or self.memory_limit < 1:
            raise ValueError("retrieval limits must be positive")


@dataclass(frozen=True)
class ResearcherResult:
    papers: tuple[Paper, ...]
    keywords: tuple[str, ...]
    sources: Mapping[str, int]
    batch: str | None = None
    output_path: str | None = None


@dataclass(frozen=True)
class FilterRequest:
    requirement: str
    papers: Sequence[Paper]
    batch: str | None = None
    min_keep: int = 12
    quality_floor: float = QUALITY_FLOOR_DEFAULT
    acquire_fulltext: bool = False
    pdf_fallback: bool = False

    def __post_init__(self):
        if not self.requirement.strip():
            raise ValueError("requirement must not be empty")
        if self.min_keep < 1:
            raise ValueError("min_keep must be positive")


@dataclass(frozen=True)
class FilterResult:
    selected_papers: tuple[Paper, ...]
    reason: str
    attempts: tuple[Mapping[str, Any], ...] = ()
    batch: str | None = None
    output_path: str | None = None


@dataclass(frozen=True)
class ExtractorRequest:
    batch: str | None = None
    base_dir: str | None = None
    domain: str = "thermoelectric"
    limit: int = 2000
    domain_registry: Mapping[str, Any] | None = None
    min_fulltext_usable_rate: float = MIN_FULLTEXT_USABLE_RATE
    allow_low_quality: bool = False

    def __post_init__(self):
        if self.limit < 0:
            raise ValueError("limit must not be negative")
        if not 0 <= self.min_fulltext_usable_rate <= 1:
            raise ValueError("min_fulltext_usable_rate must be between 0 and 1")


@dataclass(frozen=True)
class ExtractorResult:
    base_dir: str
    completed: int
    failed: int
    limit: int
    quality_report: str


@dataclass(frozen=True)
class ResearchGapRequest:
    batch: str | None = None
    skip_llm: bool = False
    limit: int = 0
    concept_llm: Any = field(default=None, repr=False, compare=False)
    judge_llm: Any = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        if self.limit < 0:
            raise ValueError("limit must not be negative")


@dataclass(frozen=True)
class ResearchGapResult:
    batch: str
    output_dir: str
    detected: int
    verdicts: int
    accepted: int
    gaps: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class ValidateRequest:
    formulas: tuple[str, ...] = ()
    batch: str | None = None
    output_dir: str | None = None
    delay: float = 0.3

    def __post_init__(self):
        if self.delay < 0:
            raise ValueError("delay must not be negative")


@dataclass(frozen=True)
class ValidateResult:
    validated: int
    available: int
    reports: tuple[str, ...]
    output_dir: str
