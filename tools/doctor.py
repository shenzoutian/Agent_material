"""
litdiscovery 环境自检（doctor）—— 独立脚本，不依赖、不修改 src/ 源码。

用法：
    python tools/doctor.py        # 或
    python -m tools.doctor

作用：在真正运行工作流之前，一次性检查
    1. Python 版本是否满足（>=3.10）
    2. 核心/可选依赖是否已安装及其版本
    3. 各服务密钥是否已配置（.env 或进程环境变量）：缺哪项、影响哪个阶段、去哪申请

目的：避免"跑到一半才因缺密钥 / 缺依赖而崩溃"的体验。
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

REQUIRES_PYTHON = (3, 10)


def _reconfigure_utf8() -> None:
    """Windows 下把 stdin/stdout/stderr 统一为 UTF-8（与 src 的 reconfigure_utf8 同语义）。"""
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

# 依赖清单：(import 名, 发行包名, 用途)
CORE_DEPS = [
    ("requests", "requests", "网络请求（下载 / API 调用）"),
    ("pandas", "pandas", "表格 / CSV 数据处理"),
    ("pydantic", "pydantic", "数据结构校验"),
    ("tiktoken", "tiktoken", "token 计数"),
    ("yaml", "pyyaml", "YAML 配置解析"),
    ("langchain_core", "langchain-core", "LangChain 核心"),
    ("langchain_openai", "langchain-openai", "OpenAI 兼容 LLM 客户端"),
    ("langgraph", "langgraph", "Agent 图编排"),
]
OPTIONAL_DEPS = [
    ("docling", "docling", "PDF→Markdown 转换"),
    ("pytest", "pytest", "运行测试（仅开发者）"),
]

# 密钥清单：(环境变量名, 所属阶段, 是否必需, 申请来源)
KEYS = [
    ("DEEPSEEK_API_KEY", "核心 LLM（planner / 提取全链路）", True, "DeepSeek 开放平台"),
    ("OPENAI_API_KEY", "OpenAI Deep Research（前沿综述）", False, "OpenAI 平台"),
    ("APIFY_API_KEY", "文献检索（Apify Academic Paper Scraper）", False, "Apify"),
    ("TAVILY_API_KEY", "联网前沿检索（Tavily）", False, "Tavily"),
    ("UNPAYWALL_EMAIL", "全文下载（Unpaywall）", False, "Unpaywall（填邮箱即可）"),
    ("ELSEVIER_API_KEY", "全文下载（Elsevier）", False, "Elsevier Developer"),
    ("SPRINGER_NATURE_API_KEY", "全文下载（Springer Nature）", False, "Springer"),
    ("IEEE_API_KEY", "全文下载（IEEE）", False, "IEEE Xplore"),
    ("WILEY_API_KEY", "全文下载（Wiley）", False, "Wiley"),
    ("CORE_API_KEY", "全文下载（CORE）", False, "CORE"),
    ("SEMANTIC_SCHOLAR_API_KEY", "全文下载（Semantic Scholar）", False, "Semantic Scholar"),
    ("CROSSREF_EMAIL", "全文下载（Crossref）", False, "Crossref（填邮箱即可）"),
    ("MATERIALS_PROJECT_API_KEY", "材料数据库验证（Materials Project）", False, "Materials Project"),
]


def _load_dotenv(path: Path) -> None:
    """读取 .env（与 config.py 相同语义：setdefault，真实环境变量优先）。"""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k:
                os.environ.setdefault(k, v)
    except OSError:
        pass


def _check_python() -> bool:
    ok = sys.version_info >= REQUIRES_PYTHON
    mark = "✓" if ok else "✗"
    print(f"[Python] {sys.version.split()[0]}  （要求 >=3.10） {mark}")
    return ok


def _check_deps() -> None:
    print("\n[依赖] 核心依赖")
    for mod, dist, use in CORE_DEPS:
        if importlib.util.find_spec(mod) is not None:
            try:
                ver = importlib.metadata.version(dist)
            except importlib.metadata.PackageNotFoundError:
                ver = "?"
            print(f"  ✓ {dist} {ver}  — {use}")
        else:
            print(f"  ✗ {dist} 未安装  — {use}   [pip install {dist}]")

    print("\n[依赖] 可选依赖")
    for mod, dist, use in OPTIONAL_DEPS:
        if importlib.util.find_spec(mod) is not None:
            try:
                ver = importlib.metadata.version(dist)
            except importlib.metadata.PackageNotFoundError:
                ver = "?"
            print(f"  ✓ {dist} {ver}  — {use}")
        else:
            print(f"  - {dist} 未安装  — {use}")


def _check_keys() -> int:
    env_path = PROJECT_ROOT / ".env"
    print("\n[密钥] 服务凭据（.env 或进程环境变量）")
    if env_path.exists():
        print(f"  .env 文件: {env_path}  （存在）")
        _load_dotenv(env_path)
    else:
        print(f"  .env 文件: 不存在（可复制 .env.example 为 .env 并填写）")

    missing_required = []
    set_count = 0
    for key, stage, required, _hint in KEYS:
        val = os.environ.get(key, "").strip()
        if val:
            set_count += 1
            status = "✓ 已配置   "
        elif required:
            missing_required.append(key)
            status = "✗ 未配置（必需）"
        else:
            status = "  - 未配置（可选）"
        print(f"  {status}  {key:<28} ← {stage}")
    print(f"\n  已配置 {set_count}/{len(KEYS)} 项")

    if missing_required:
        print("\n[警告] 缺失核心密钥，将导致 planner / 提取阶段无法运行：")
        for k in missing_required:
            hint = next((h for kk, _s, _r, h in KEYS if kk == k), "")
            print(f"  → {k}（{hint}）")
        print("  修复：复制 .env.example 为 .env，填入密钥后重跑本脚本。")
    return len(missing_required)


def main() -> int:
    _reconfigure_utf8()
    print("=" * 62)
    print("litdiscovery doctor —— 环境自检")
    print(f"项目根目录: {PROJECT_ROOT}")
    print("=" * 62)
    py_ok = _check_python()
    _check_deps()
    missing = _check_keys()
    print("\n" + "=" * 62)
    if py_ok and missing == 0:
        print("结论：环境就绪 ✓ 可运行 `litdiscovery run --help`")
        return 0
    print("结论：存在缺失项，请按上方提示补齐后再运行。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
