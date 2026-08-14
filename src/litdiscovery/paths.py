"""
paths.py —— 产物根唯一事实源。

统一 artifacts/ 布局：

    artifacts/batches/<批次>/            检索/全文/报告批次
        <批次>/orders/                   Agent 交接文件（json/txt，与 end_mds/ 同级）
    artifacts/extracted/<批次>/<folder>/ 提取产物（性能/工艺/结构）
    artifacts/sessions/<会话>/            会话日志（每批一会话，会话名 = 批次名）
    artifacts/validation/<formula>/       材料库验证对比
    artifacts/knowledge/<batch>.jsonl     知识库条目
    artifacts/memory/                     长期记忆索引

所有模块一律从本模块取路径，禁止手写 "data_doi" / "doi_reach_log" 等字面量。
产物根可通过环境变量 LITDISCOVERY_ARTIFACTS 覆盖。
"""

import os
import re
from dataclasses import dataclass
from pathlib import Path

# 项目根 = src/litdiscovery/paths.py 向上 3 级
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# 统一产物根（可覆盖）
ARTIFACTS_ROOT = Path(os.environ.get("LITDISCOVERY_ARTIFACTS", PROJECT_ROOT / "artifacts"))

# 各子根
BATCHES_ROOT = ARTIFACTS_ROOT / "batches"
EXTRACTED_ROOT = ARTIFACTS_ROOT / "extracted"
SESSIONS_ROOT = ARTIFACTS_ROOT / "sessions"
VALIDATION_ROOT = ARTIFACTS_ROOT / "validation"
KNOWLEDGE_ROOT = ARTIFACTS_ROOT / "knowledge"
MEMORY_ROOT = ARTIFACTS_ROOT / "memory"

@dataclass(frozen=True)
class BatchPaths:
    """Canonical paths for one run; services should depend on this object."""

    root: Path

    @classmethod
    def from_value(cls, batch: str | Path) -> "BatchPaths":
        return cls(Path(batch))

    @property
    def orders(self) -> Path:
        return self.root / "orders"

    @property
    def fulltext(self) -> Path:
        return self.root / "end_mds"

    @property
    def gap(self) -> Path:
        return self.root / "gap_output"

    @property
    def validation(self) -> Path:
        return self.root / "validation"

    @property
    def evidence(self) -> Path:
        return self.root / "evidence"

    @property
    def report_markdown(self) -> Path:
        return self.root / "report.md"

    @property
    def report_json(self) -> Path:
        return self.root / "report.json"

    @property
    def run_state(self) -> Path:
        return self.root / "run_state.json"

    def ensure(self) -> "BatchPaths":
        self.root.mkdir(parents=True, exist_ok=True)
        self.orders.mkdir(parents=True, exist_ok=True)
        return self


def ensure_roots() -> None:
    """确保各产物子根存在（幂等）。"""
    for sub in (BATCHES_ROOT, EXTRACTED_ROOT, SESSIONS_ROOT,
                VALIDATION_ROOT, KNOWLEDGE_ROOT, MEMORY_ROOT):
        sub.mkdir(parents=True, exist_ok=True)


def resolve_batch(batch: str | Path | None = None) -> Path:
    """解析批次目录。

    - 显式传入：绝对路径直接用；相对路径先按 BATCHES_ROOT 解析，再按项目根解析；
    - None：定位最新含 end_mds 的批次。
    """
    if batch:
        p = Path(batch)
        if p.is_absolute():
            return p
        if (BATCHES_ROOT / p).exists():
            return BATCHES_ROOT / p
        if (PROJECT_ROOT / p).exists():
            return PROJECT_ROOT / p
        return BATCHES_ROOT / p
    return latest_batch(require_end_mds=True)


def latest_batch(require_end_mds: bool = True) -> Path:
    """定位最新批次（按目录名字典序取最大；require_end_mds 时只挑含 end_mds 的）。"""
    if not BATCHES_ROOT.is_dir():
        raise FileNotFoundError(f"批次根不存在: {BATCHES_ROOT}")
    cands = [d for d in BATCHES_ROOT.iterdir() if d.is_dir()]
    if not cands:
        raise FileNotFoundError(f"批次根下无批次目录: {BATCHES_ROOT}")
    if require_end_mds:
        cands = [d for d in cands if (d / "end_mds").is_dir()]
        if not cands:
            raise FileNotFoundError(f"无含 end_mds 的批次: {BATCHES_ROOT}")
    return max(cands, key=batch_sort_key)


_BATCH_STAMP_RE = re.compile(r"(?<!\d)(\d{4}_\d{2}_\d{2}_\d{6})(?!\d)")


def batch_sort_key(path: str | Path) -> tuple[str, float]:
    """Sort batches by embedded timestamp, with mtime as a legacy fallback."""
    p = Path(path)
    matches = _BATCH_STAMP_RE.findall(p.name)
    stamp = matches[-1] if matches else ""
    try:
        mtime = p.stat().st_mtime
    except OSError:
        mtime = 0.0
    return stamp, mtime


def data_doi_dir(batch: str | Path) -> Path:
    """批次对应的提取产物目录 artifacts/extracted/<批次名>/。"""
    b = Path(batch)
    return EXTRACTED_ROOT / b.name


# ---- Agent 交接文件（orders/）约定 ----
# 跨 Agent 传递的 json/txt 统一落在 <batch>/orders/（与 end_mds/ 同级），
# 取代"散落在批次根"的旧布局：交接文件 = 上个 Agent 的产物 / 下个 Agent 的输入。
ORDERS_SUBDIR = "orders"

ORDERS_FILES = {
    "search_results.json",      # researcher 检索原始结果
    "query_audit.json",         # 查询级来源、命中、去重及纳入审计
    "uncertain_review.json",    # filter_agent 不确定集复核入口
    "deep_research_results.json", # Deep Research 原始响应摘要与 DOI
    "memory_papers.json",       # 历史文献目录命中
    "fulltext_attempts.json",   # 全文来源逐次尝试审计
    "snowball_candidates.json", # filter 雪球候选
    "seed_papers.json",         # filter 种子（兼容旧链）
    "doi_choose_results.json",  # filter 取舍结果
    "corpus_quality.json",      # 检索语料质量与多样性审计
    "fulltext_quality.json",    # 全文可用性审计
    "extraction_quality.json",  # 抽取失败、缺失条件与 locator 清单
    "review_report.json",       # review_agent 执行错误分析
    "doi_list.json",            # researcher 收敛的最小契约
    "domain_registry.json",     # extractor 动态属性域注册表
    "keywords.txt",             # 确认后的关键词
    "doi_list.txt",             # 最终 DOI 列表（下载阶段输入）
    "doi_reach_results.json",   # 权威文献 JSON（兼容位）
}


def orders_dir(batch: str | Path) -> Path:
    """批次交接文件目录 <batch>/orders/（与 end_mds/ 同级）。"""
    return BatchPaths.from_value(batch).orders


def handoff_path(batch: str | Path, name: str | Path) -> Path:
    """交接文件写入路径：<batch>/orders/<name>（end_mds 同级，写入统一走此函数）。

    - name 为绝对路径 → 原样返回（调用方自己落盘）；
    - name 已带 orders/ 前缀 → 原样拼到批次下；
    - name 是 ORDERS_FILES 清单内文件 → 落到 <batch>/orders/<name>；
    - 其他（如 gap_output/x.json 阶段产物）→ 原样拼到批次下（不入 orders/）。
    """
    b = Path(batch)
    p = Path(name)
    if p.is_absolute():
        return p
    if p.parts and p.parts[0] == ORDERS_SUBDIR:
        return b / p
    if p.name in ORDERS_FILES:
        return b / ORDERS_SUBDIR / p.name
    return b / p


def read_handoff(batch: str | Path, name: str | Path) -> Path:
    """交接文件读取路径：优先 <batch>/orders/<name>，回退批次根（兼容旧布局批次）。

    读取统一走此函数，使新批次（orders/）与旧批次（批次根）都能被下游消费。
    """
    p = handoff_path(batch, name)
    return p if p.exists() else Path(batch) / Path(name)


def batch_of(path: str | Path) -> Path:
    """由批次内任意路径推导批次目录。

    处理常见形态：orders/ 下的交接文件 → 批次；end_mds / orders 本身 → 批次；
    批次内普通子目录（gap_output 等）或批次根文件 → 批次（依据父目录存在
    orders/ 或 end_mds 判定）；批次目录本身 → 自身。
    """
    p = Path(path)
    if p.name == ORDERS_SUBDIR or p.name == "end_mds":
        return p.parent
    if p.parent.name == ORDERS_SUBDIR:
        return p.parent.parent
    parent = p if p.is_dir() else p.parent
    if (parent / ORDERS_SUBDIR).exists() or (parent / "end_mds").exists():
        return parent
    return p


def session_log_dir(requirement: str = "") -> Path:
    """返回会话日志根目录；具体会话沿用批次名称。"""
    SESSIONS_ROOT.mkdir(parents=True, exist_ok=True)
    return SESSIONS_ROOT
