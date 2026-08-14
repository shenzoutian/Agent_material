"""
litdiscovery/agent/filter_agent_pipeline/pdf_fetch.py — 按 DOI 下载论文 PDF。

按序尝试以下来源（每篇命中第一个成功源）：
    1. arXiv 专属（10.48550/arXiv.*）
    2. Unpaywall API（OA 发现，需真实邮箱）
    3. 出版社专用 API（Elsevier / Springer / IEEE / Wiley，需对应 Key）
    4. Semantic Scholar（免费，需 key 可提限流）
    5. CORE（OA 聚合库，可突破出版社 403）
    6. doi.org 直连
"""

import os
import re
import sys
import time
import random
import requests
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional, List

from litdiscovery.agent.filter_agent_pipeline.acquisition import (
    DownloadAudit, DownloadCandidate, DownloadStats, ProviderCircuitBreaker,
    classify_response, format_from_path, unique_candidates, validate_pdf_bytes,
)

from litdiscovery.config import (
    DOI_LIST_FILE,
    UNPAYWALL_EMAIL,
    ELSEVIER_API_KEY,
    SPRINGER_NATURE_API_KEY,
    IEEE_API_KEY,
    WILEY_API_KEY,
    CORE_API_KEY,
    SEMANTIC_SCHOLAR_API_KEY,
    DOWNLOAD_USER_AGENT,
)

# ============================================================
# 配置
# ============================================================
UNPAYWALL_URL = "https://api.unpaywall.org/v2/{doi}"
SEMANTIC_SCHOLAR_URL = "https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}"
DOI_RESOLVER_URL = "https://doi.org/{doi}"
ELSEVIER_URL = "https://api.elsevier.com/content/article/doi/{doi}"
SPRINGER_URL = "https://api.springernature.com/meta/v2/json"
IEEE_URL = "https://api.ieee.org/rest/search/v2/papers"
WILEY_URL = "https://onlinelibrary.wiley.com/doi/pdfdirect/{doi}"
CORE_URL = "https://api.core.ac.uk/v3/search/works"
PDF_OUTPUT_SUBDIR = "pdfs"          # 默认输出子文件夹
RATE_LIMIT_DELAY = (1.0, 3.0)       # 请求间随机延迟（秒）
REQUEST_TIMEOUT = 60                # 单次请求超时（秒）
MAX_RETRIES = 3                     # 失败重试次数
_CIRCUIT_BREAKER = ProviderCircuitBreaker()


def _safe_name(doi: str) -> str:
    """DOI → 安全文件名（统一委托 common.fs.safe_folder_name）。"""
    from litdiscovery.common.fs import safe_folder_name
    return safe_folder_name(doi)
HEADERS = {"User-Agent": DOWNLOAD_USER_AGENT}  # 浏览器 UA，降低出版社 403


def find_latest_log_dir() -> Optional[Path]:
    """返回 artifacts/batches/ 下最新的运行文件夹。

    使用公共 batch_sort_key 解析名称中的时间戳，兼容新格式“名称_时间戳”
    和历史格式“时间戳_名称”。
    """
    from litdiscovery.paths import BATCHES_ROOT, batch_sort_key
    root = BATCHES_ROOT
    if not root.is_dir():
        return None
    subdirs = [d for d in root.iterdir() if d.is_dir()]
    if not subdirs:
        return None
    return max(subdirs, key=batch_sort_key)


def default_doi_list() -> Optional[Path]:
    """默认 DOI 来源：最新日志文件夹中的 doi_list.txt（新批次在 orders/，兼容批次根）。"""
    latest = find_latest_log_dir()
    if latest is None:
        return None
    from litdiscovery.paths import read_handoff
    path = read_handoff(latest, DOI_LIST_FILE)
    return path if path.is_file() else None


def load_doi_list(file_path: Path) -> List[str]:
    """从文件中加载 DOI 列表（每行一个 DOI，忽略空行和注释行）。"""
    if not file_path.exists():
        print(f"[ERROR] DOI 列表文件不存在: {file_path}")
        sys.exit(1)

    with open(file_path, "r", encoding="utf-8") as f:
        dois = [line.strip() for line in f
                if line.strip() and not line.strip().startswith("#")]

    print(f"[List] 从 {file_path} 加载了 {len(dois)} 个 DOI")
    return dois


# ============================================================
# PDF 链接解析（Unpaywall → Semantic Scholar → doi.org 直连）
# ============================================================
def _arxiv_pdf_url(doi: str) -> Optional[str]:
    """arXiv 专属：DOI 前缀 10.48550/arXiv.<id> 或含 arxiv 标识 → 直接生成 arXiv PDF 链接。"""
    s = doi.lower()
    if "arxiv" not in s and not s.startswith("10.48550/"):
        return None
    # 提取 arXiv id（如 1706.03762 / 2201.00001v3）
    m = re.search(r"arxiv[.:/]?(\d{4}\.\d{4,5}(?:v\d+)?)", s)
    if not m:
        return None
    aid = m.group(1)
    url = f"https://arxiv.org/pdf/{aid}"
    print(f"      [arXiv] 检测到 arXiv DOI -> {url}")
    return url


def _try_unpaywall(doi: str) -> Optional[str]:
    """通过 Unpaywall API 获取 OA PDF 链接（主要覆盖出版社期刊论文）。

    必须使用真实邮箱；example.com 等会被 Unpaywall 以 422 拒绝。
    """
    email = (UNPAYWALL_EMAIL or "").strip()
    if not email or "example.com" in email:
        print(f"      [Unpaywall] 已跳过：请先在 config.py 的 UNPAYWALL_EMAIL 填入真实邮箱")
        return None
    try:
        resp = requests.get(
            UNPAYWALL_URL.format(doi=doi),
            params={"email": email},
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        oa = data.get("best_oa_location") or {}
        pdf = oa.get("url_for_pdf") or oa.get("url") or ""
        if pdf:
            print(f"      [Unpaywall] 找到 OA PDF: {pdf[:100]}")
        return pdf or None
    except Exception as e:
        print(f"      [Unpaywall] 查询失败: {type(e).__name__}: {e}")
        return None


def _try_semantic_scholar(doi: str) -> Optional[str]:
    """通过 Semantic Scholar API 获取 openAccessPdf 链接（429 限流时退避重试）。"""
    for attempt in range(1, 3):
        try:
            headers = dict(HEADERS)
            if SEMANTIC_SCHOLAR_API_KEY:
                headers["x-api-key"] = SEMANTIC_SCHOLAR_API_KEY
            resp = requests.get(
                SEMANTIC_SCHOLAR_URL.format(doi=doi),
                params={"fields": "openAccessPdf"},
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code == 429:
                wait = 5 * attempt
                print(f"      [SemanticScholar] 触发限流(429)，{wait}s 后重试 ...")
                time.sleep(wait)
                continue
            if resp.status_code != 200:
                return None
            data = resp.json()
            pdf = (data.get("openAccessPdf") or {}).get("url") or ""
            if pdf:
                print(f"      [SemanticScholar] 找到 PDF: {pdf[:100]}")
            return pdf or None
        except Exception as e:
            print(f"      [SemanticScholar] 查询失败: {type(e).__name__}: {e}")
            return None
    return None


def _try_elsevier(doi: str) -> Optional[str]:
    """Elsevier / ScienceDirect 期刊（10.1016/*）：由 API 返回的 PDF 链接。"""
    if not ELSEVIER_API_KEY or not doi.lower().startswith("10.1016/"):
        return None
    try:
        resp = requests.get(
            ELSEVIER_URL.format(doi=doi),
            headers={"X-ELS-APIKey": ELSEVIER_API_KEY,
                     "Accept": "application/pdf",
                     **HEADERS},
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code != 200:
            print(f"      [Elsevier] API 返回 {resp.status_code}")
            return None
        return resp.url
    except Exception as e:
        print(f"      [Elsevier] 查询失败: {type(e).__name__}: {e}")
        return None


def _try_springer(doi: str) -> Optional[str]:
    """Springer 期刊（10.1007/*）：meta/v2 接口返回 OpenURL/PDF 链接。"""
    if not SPRINGER_NATURE_API_KEY or not doi.lower().startswith("10.1007/"):
        return None
    try:
        resp = requests.get(
            SPRINGER_URL,
            params={"q": f'doi:"{doi}"', "api_key": SPRINGER_NATURE_API_KEY},
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code != 200:
            print(f"      [Springer] API 返回 {resp.status_code}")
            return None
        data = resp.json()
        recs = data.get("records") or []
        if not recs:
            return None
        pdf = (recs[0].get("url") or [{}])[0].get("value", "") or ""
        if "pdf" in pdf.lower():
            print(f"      [Springer] 找到 PDF: {pdf[:100]}")
        return pdf or None
    except Exception as e:
        print(f"      [Springer] 查询失败: {type(e).__name__}: {e}")
        return None


def _try_ieee(doi: str) -> Optional[str]:
    """IEEE Xplore（10.1109/*）：由 API 返回的 PDF 链接（如不提供则退到 doi.org）。"""
    if not IEEE_API_KEY or not doi.lower().startswith("10.1109/"):
        return None
    try:
        resp = requests.get(
            IEEE_URL,
            params={"querytext": f"doi:{doi}", "apikey": IEEE_API_KEY,
                    "format": "json"},
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code != 200:
            print(f"      [IEEE] API 返回 {resp.status_code}")
            return None
        data = resp.json()
        doc = (data.get("documents") or [{}])[0]
        pdf = doc.get("pdfUrl") or ""
        if not pdf:
            # IEEE API 常不给直链，退到 doi.org 页面
            pdf = f"https://doi.org/{doi}"
            print(f"      [IEEE] API 无 PDF 直链，退到 doi.org: {pdf}")
        else:
            print(f"      [IEEE] 找到 PDF: {pdf[:100]}")
        return pdf or None
    except Exception as e:
        print(f"      [IEEE] 查询失败: {type(e).__name__}: {e}")
        return None


def _try_wiley(doi: str) -> Optional[str]:
    """Wiley 期刊（10.1002/*）：pdfdirect 直链（需要浏览器 UA 防 403）。"""
    if not WILEY_API_KEY or not doi.lower().startswith("10.1002/"):
        return None
    try:
        pdf_url = WILEY_URL.format(doi=doi)
        print(f"      [Wiley] 使用 pdfdirect 直链: {pdf_url[:90]}")
        return pdf_url
    except Exception as e:
        print(f"      [Wiley] 查询失败: {type(e).__name__}: {e}")
        return None


def _try_core(doi: str) -> Optional[str]:
    """CORE（OA 聚合库）：按 DOI 搜工作，返回开放获取 PDF 链接。"""
    if not CORE_API_KEY:
        return None
    try:
        resp = requests.post(
            CORE_URL,
            headers={"Authorization": f"Bearer {CORE_API_KEY}",
                     "Content-Type": "application/json",
                     **HEADERS},
            params={"q": f'doi:"{doi}"', "limit": 5},
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code != 200:
            print(f"      [CORE] API 返回 {resp.status_code}")
            return None
        data = resp.json()
        for res in data.get("results", []):
            pdf = (res.get("downloadUrl") or "") or (res.get("pdfUrl") or "")
            if pdf:
                print(f"      [CORE] 找到 PDF: {pdf[:100]}")
                return pdf
        return None
    except Exception as e:
        print(f"      [CORE] 查询失败: {type(e).__name__}: {e}")
        return None


def _try_direct(doi: str) -> Optional[str]:
    """直接解析 doi.org：重定向终点是 PDF 则用，是 arXiv 摘要页则转为 PDF 链接。"""
    try:
        resp = requests.get(
            DOI_RESOLVER_URL.format(doi=doi),
            headers={"Accept": "application/pdf", **HEADERS},
            allow_redirects=True,
            timeout=REQUEST_TIMEOUT,
        )
        final_url = resp.url
        ctype = (resp.headers.get("Content-Type") or "").lower()
        # arXiv 摘要页 → 转 PDF
        m = re.search(r"arxiv\.org/abs/(\d{4}\.\d{4,5}(?:v\d+)?)", final_url)
        if m:
            pdf = f"https://arxiv.org/pdf/{m.group(1)}"
            print(f"      [DOI 直连] arXiv 摘要页 -> {pdf}")
            return pdf
        if resp.status_code == 200 and ("pdf" in ctype or final_url.lower().endswith(".pdf")):
            print(f"      [DOI 直连] 返回 PDF ({ctype}): {final_url[:100]}")
            return final_url
        return None
    except Exception as e:
        print(f"      [DOI 直连] 失败: {type(e).__name__}: {e}")
        return None


def get_pdf_url(doi: str) -> Optional[str]:
    """按优先级尝试各策略，返回可下载的 PDF 链接（无则 None）。

    顺序:
        1. arXiv 专属（10.48550/arXiv.*）
        2. Unpaywall（OA 发现，需真实邮箱）
        3. 出版社专用 API（Elsevier / Springer / IEEE / Wiley，需对应 Key）
        4. Semantic Scholar（免费，需 key 可提限流）
        5. CORE（OA 聚合库，可突破出版社 403）
        6. doi.org 直连
    """
    s = doi.lower()
    return (_arxiv_pdf_url(doi)
            or _try_unpaywall(doi)
            or (_try_elsevier(doi) if s.startswith("10.1016/") else None)
            or (_try_springer(doi) if s.startswith("10.1007/") else None)
            or (_try_ieee(doi) if s.startswith("10.1109/") else None)
            or (_try_wiley(doi) if s.startswith("10.1002/") else None)
            or _try_semantic_scholar(doi)
            or _try_core(doi)
            or _try_direct(doi))


def _paper_candidates(paper: Optional[dict]) -> List[DownloadCandidate]:
    if not paper:
        return []
    locations = [paper.get("best_oa_location") or {},
                 *(paper.get("oa_locations") or paper.get("locations") or [])]
    candidates = []
    for location in locations:
        if not isinstance(location, dict):
            continue
        if location.get("pdf_url"):
            candidates.append(DownloadCandidate("openalex", location["pdf_url"]))
        if location.get("landing_page_url"):
            candidates.append(DownloadCandidate(
                "openalex_landing", location["landing_page_url"], "landing"))
    for key in ("pdf_url", "fulltext_url", "oa_url"):
        if paper.get(key):
            candidates.append(DownloadCandidate("paper_metadata", paper[key]))
    return candidates


def get_pdf_candidates(doi: str, paper: Optional[dict] = None) -> List[DownloadCandidate]:
    """Discover all candidate URLs; a bad first URL must not block fallback."""
    from .download import download_api, download_free, download_other
    s = doi.lower()
    providers = [("doi_resolver", lambda: _try_direct(doi))]
    candidates = []
    for discover, args in (
        (download_free.discover, (doi, paper)),
        (download_api.discover, (doi,)),
        (download_other.discover, (doi,)),
    ):
        try:
            candidates.extend(discover(*args))
        except Exception:
            continue
    for provider, discover in providers:
        if not _CIRCUIT_BREAKER.available(provider):
            continue
        try:
            url = discover()
        except Exception:
            url = None
        if url:
            candidates.append(DownloadCandidate(provider, url))
    return unique_candidates(candidates)


class _PdfMetaParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        name = values.get("name", "").lower()
        if tag.lower() == "meta" and name in {
                "citation_pdf_url", "bepress_citation_pdf_url"} and values.get("content"):
            self.urls.append(values["content"])
        if tag.lower() == "link" and values.get("type", "").lower() == "application/pdf" and values.get("href"):
            self.urls.append(values["href"])


def _landing_page_pdf_urls(content: bytes, base_url: str) -> List[str]:
    from urllib.parse import urljoin
    parser = _PdfMetaParser()
    parser.feed(content.decode("utf-8", errors="ignore"))
    return [urljoin(base_url, url) for url in parser.urls]


# ============================================================
# 下载 PDF
# ============================================================
def _download_pdf_by_doi_legacy(doi: str, output_dir: Path, filename: Optional[str] = None) -> Optional[Path]:
    """
    根据 DOI 下载论文 PDF。

    参数:
        doi: 论文 DOI（如 "10.1016/j.jmat.2024.01.001"）
        output_dir: 输出目录
        filename: 输出文件名（不含扩展名），默认取 DOI 的合法化形式

    返回:
        保存的 PDF 文件路径，失败则返回 None
    """
    doi = doi.strip()
    output_dir.mkdir(parents=True, exist_ok=True)
    if filename is None:
        filename = _safe_name(doi)
    pdf_path = output_dir / f"{filename}.pdf"

    # 解析 PDF 链接（带重试）
    pdf_url = None
    for attempt in range(1, MAX_RETRIES + 1):
        pdf_url = get_pdf_url(doi)
        if pdf_url:
            break
        if attempt < MAX_RETRIES:
            wait = 3 * attempt
            print(f"      [Warn] 未找到 PDF 链接，{wait}s 后重试 {attempt}/{MAX_RETRIES} ...")
            time.sleep(wait)
    if not pdf_url:
        print(f"[FAIL] 无法解析到可下载的 PDF: {doi}")
        return None

    # 下载 PDF 文件
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(pdf_url, headers=HEADERS, stream=True, timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                print(f"      [Warn] 下载返回 {resp.status_code}，重试 {attempt}/{MAX_RETRIES}")
                time.sleep(2 * attempt)
                continue
            # 若重定向后是 HTML 而非 PDF，视为失败
            ctype = (resp.headers.get("Content-Type") or "").lower()
            if ctype and "pdf" not in ctype and "octet-stream" not in ctype:
                print(f"      [Warn] 内容类型非 PDF ({ctype})，重试 {attempt}/{MAX_RETRIES}")
                time.sleep(2 * attempt)
                continue
            with open(pdf_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
            size = pdf_path.stat().st_size
            if size < 10_000:
                print(f"      [Warn] 文件过小 ({size} B)，可能非有效 PDF，重试 ...")
                time.sleep(2 * attempt)
                continue
            print(f"[OK] 下载成功: {doi} -> {pdf_path.name} ({size/1024:.0f} KB)")
            return pdf_path
        except requests.exceptions.Timeout:
            print(f"[Timeout] 下载超时: {doi}，重试 {attempt}/{MAX_RETRIES}")
            time.sleep(5)
        except Exception as e:
            print(f"[ERROR] 下载异常 ({doi}): {type(e).__name__}: {e}")
            return None

    print(f"[ERROR] 最终失败（已重试 {MAX_RETRIES} 次）: {doi}")
    return None


def download_pdf_by_doi(doi: str, output_dir: Path, filename: Optional[str] = None,
                        audit: Optional[DownloadAudit] = None,
                        paper: Optional[dict] = None) -> Optional[Path]:
    """Try all discovered locations and accept only a validated PDF payload."""
    doi = doi.strip()
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = output_dir / f"{filename or _safe_name(doi)}.pdf"
    candidates = get_pdf_candidates(doi, paper=paper)
    if not candidates:
        if audit:
            audit.record(doi=doi, provider="discovery", outcome="failed",
                         failure_class="no_candidate", retryable=False)
        print(f"[FAIL] No downloadable PDF candidate: {doi}")
        return None

    candidate_index = 0
    while candidate_index < len(candidates):
        candidate = candidates[candidate_index]
        candidate_index += 1
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = requests.get(candidate.url, headers=HEADERS, stream=True,
                                        allow_redirects=True, timeout=REQUEST_TIMEOUT)
                failure_class, retryable = classify_response(
                    response.status_code, response.headers.get("Content-Type", ""))
                if response.status_code != 200:
                    if audit:
                        audit.record(doi=doi, provider=candidate.provider, url=candidate.url,
                                     status_code=response.status_code, outcome="failed",
                                     failure_class=failure_class, retryable=retryable)
                    _CIRCUIT_BREAKER.record(candidate.provider, retryable)
                    if retryable and attempt < MAX_RETRIES:
                        retry_after = response.headers.get("Retry-After", "")
                        delay = int(retry_after) if retry_after.isdigit() else 2 ** attempt
                        time.sleep(min(delay, 30))
                        continue
                    break
                content = b"".join(response.iter_content(chunk_size=65536))
                ctype = response.headers.get("Content-Type", "").lower()
                if candidate.kind == "landing" or "html" in ctype:
                    base_url = getattr(response, "url", candidate.url)
                    urls = _landing_page_pdf_urls(content, base_url)
                    candidates.extend(unique_candidates(
                        DownloadCandidate(candidate.provider + "_meta", url) for url in urls))
                    if audit:
                        audit.record(doi=doi, provider=candidate.provider, url=candidate.url,
                                     status_code=200, outcome="failed",
                                     failure_class="landing_page_parsed", retryable=False,
                                     discovered_urls=len(urls))
                    break
                valid, validation, pages = validate_pdf_bytes(content)
                if not valid:
                    if audit:
                        audit.record(doi=doi, provider=candidate.provider, url=candidate.url,
                                     status_code=200, outcome="failed",
                                     failure_class=validation, retryable=False)
                    break
                size = len(content)
                if size < 10_000:
                    if audit:
                        audit.record(doi=doi, provider=candidate.provider, url=candidate.url,
                                     outcome="failed", failure_class="pdf_too_small", retryable=False)
                    break
                temp_path = pdf_path.with_suffix(".pdf.tmp")
                temp_path.write_bytes(content)
                os.replace(temp_path, pdf_path)
                _CIRCUIT_BREAKER.success(candidate.provider)
                if audit:
                    audit.record(doi=doi, provider=candidate.provider, url=candidate.url,
                                 status_code=200, bytes=size, pages=pages, outcome="success")
                print(f"[OK] Downloaded: {doi} -> {pdf_path.name} ({size/1024:.0f} KB)")
                return pdf_path
            except requests.exceptions.Timeout:
                _CIRCUIT_BREAKER.record(candidate.provider, True)
                if audit:
                    audit.record(doi=doi, provider=candidate.provider, url=candidate.url,
                                 outcome="failed", failure_class="timeout", retryable=True)
                if attempt < MAX_RETRIES:
                    time.sleep(2 ** attempt)
            except Exception as exc:
                if audit:
                    audit.record(doi=doi, provider=candidate.provider, url=candidate.url,
                                 outcome="failed", failure_class="network_error", retryable=True,
                                 error=f"{type(exc).__name__}: {exc}")
                break
    print(f"[ERROR] All PDF candidates failed: {doi}")
    return None


def _try_txt(doi: str) -> Optional[dict]:
    """文本全文兜底：CORE fulltext 字段 / Europe PMC 全文。返回 {text} 或 None。"""
    # CORE fulltext
    if CORE_API_KEY:
        try:
            resp = requests.post(
                CORE_URL,
                headers={"Authorization": f"Bearer {CORE_API_KEY}",
                         "Content-Type": "application/json", **HEADERS},
                json={"q": f'doi:"{doi}"', "limit": 3}, timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code == 200:
                results = (resp.json() or {}).get("results", [])
                for r in results:
                    ft = r.get("fulltext") or ""
                    if len(str(ft)) > 200:
                        print(f"      [TXT] CORE 全文兜底 ({len(str(ft))} 字符)")
                        return {"text": str(ft)}
        except Exception as e:
            print(f"      [TXT] CORE 失败: {type(e).__name__}: {e}")
    # Europe PMC
    try:
        resp = requests.get(
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
            params={"query": f'DOI:"{doi}"', "resultType": "core", "format": "json"},
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code == 200:
            results = (resp.json() or {}).get("resultList", {}).get("result", [])
            if results:
                r0 = results[0]
                src, pid = r0.get("source"), r0.get("id")
                if src and pid:
                    xr = requests.get(
                        f"https://www.ebi.ac.uk/europepmc/webservices/rest/{src}/{pid}/fullTextXML",
                        timeout=REQUEST_TIMEOUT)
                    if xr.status_code == 200:
                        print(f"      [TXT] EuropePMC 全文兜底 ({len(xr.text)} 字符)")
                        return {"text": xr.text, "raw_ext": ".xml"}
    except Exception as e:
        print(f"      [TXT] EuropePMC 失败: {type(e).__name__}: {e}")
    return None


def _try_elsevier_xml(doi: str) -> Optional[dict]:
    """Elsevier Article Retrieval XML 兜底（复用 fulltext 逻辑，保留 XML 原文）。"""
    if not ELSEVIER_API_KEY or not doi.lower().startswith("10.1016/"):
        return None
    try:
        resp = requests.get(
            ELSEVIER_URL.format(doi=doi),
            headers={"X-ELS-APIKey": ELSEVIER_API_KEY,
                     "Accept": "application/xml", **HEADERS},
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code != 200:
            return None
        if len(resp.text) > 100:
            print(f"      [XML] Elsevier XML 兜底 ({len(resp.text)} 字符)")
            return {"text": resp.text, "raw_ext": ".xml"}
    except Exception as e:
        print(f"      [XML] Elsevier 失败: {type(e).__name__}: {e}")
    return None


def download_any_format_by_doi(doi: str, output_dir: Path,
                               format_root: Path = None) -> Optional[Path]:
    """按 DOI 下载论文（格式优先）。

    顺序:
        1. fulltext 链（arXiv LaTeX / Elsevier XML / CORE / Europe PMC → markdowns/ + 原文）
        2. PDF 链（原有 download_pdf_by_doi）
        3. txt/xml 兜底（CORE fulltext / Elsevier XML）

    output_dir: PDF 输出目录（通常批次根/pdfs）。
    format_root: 格式目录（xmls/txts/texs）根；缺省用 output_dir.parent（即批次根）。

    返回保存的源文件或已处理文件路径，全部失败返回 None。
    """
    doi = doi.strip()
    output_dir.mkdir(parents=True, exist_ok=True)
    root = format_root or output_dir.parent

    # 1. fulltext 链（优先，Markdown 入 markdowns/，原文入对应格式目录）
    from litdiscovery.agent.filter_agent_pipeline.fulltext import fetch_fulltext_by_doi
    r = fetch_fulltext_by_doi(doi, output_dir.parent / "end_mds", format_root=root)
    if r.get("status") != "failed" and r.get("path"):
        print(f"[OK] 全文获取: {doi} -> {r['path']}")
        return Path(r["path"])

    # 2. txt/xml 兜底（保留原始文本，写 <批次根>/xmls|txts/<doi>.<ext>）
    from litdiscovery.agent.filter_agent_pipeline.fulltext import _format_subdir
    for name, fetcher in (("XML", _try_elsevier_xml), ("TXT", _try_txt)):
        res = fetcher(doi)
        if not res or not res.get("text"):
            continue
        ext = res.get("raw_ext") or ".txt"
        subdir = root / _format_subdir(ext)
        subdir.mkdir(parents=True, exist_ok=True)
        raw_path = subdir / f"{_safe_name(doi)}{ext}"
        raw_path.write_text(res["text"], encoding="utf-8")
        print(f"[OK] {name} 兜底: {doi} -> {subdir.name}/{raw_path.name} ({len(res['text'])} 字符)")
        return raw_path

    # 3. PDF 链（原有）
    pdf_path = download_pdf_by_doi(doi, output_dir)
    return pdf_path


def download_batch(doi_list: List[str], output_dir: Path, skip_existing: bool = True) -> tuple:
    """批量下载论文（全格式：fulltext→PDF→txt/xml 兜底）。返回 (成功数量, 失败数量)。

    每篇处理完输出一行实时进度（总数/已下载/失败/格式分布）。
    """
    success, failed = 0, 0
    total = len(doi_list)
    stats = DownloadStats(total)

    print(f"\n[Download] 开始批量下载 {total} 篇论文（全格式优先）到 {output_dir}/")
    print(f"   策略: arXiv/Elsevier/CORE/EuropePMC 全文 → PDF → txt/xml 兜底")
    print(f"   速率限制: 间隔 {RATE_LIMIT_DELAY[0]}~{RATE_LIMIT_DELAY[1]}s\n")

    for i, doi in enumerate(doi_list, 1):
        doi = doi.strip()
        if not doi or doi.startswith("#"):
            continue

        filename = _safe_name(doi)
        root = output_dir.parent
        pdf_path = output_dir / f"{filename}.pdf"
        # 已处理全文、Markdown 源、原始格式或 PDF 任一存在均可跳过下载。
        existing = (
            (root / "end_mds" / filename / "fulltext.md").exists()
            or (root / "markdowns" / f"{filename}.md").exists()
            or pdf_path.exists()
            or any(
                any((root / sub).glob(f"{filename}.*"))
                for sub in ("xmls", "txts", "texs", "others")
                if (root / sub).is_dir()
            )
        )
        if skip_existing and existing:
            print(f"[Skip] 已存在，跳过 [{i}/{total}]: {doi}")
            success += 1
            stats.record(skipped=True)
            print(stats.render())
            continue

        print(f"[{i}/{total}] 下载: {doi}")
        result = download_any_format_by_doi(doi, output_dir)

        if result:
            success += 1
            stats.record(True, format_from_path(result))
        else:
            failed += 1
            stats.record(False)
        print(stats.render())

        if i < total:
            time.sleep(random.uniform(*RATE_LIMIT_DELAY))

    return success, failed
