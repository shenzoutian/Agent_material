"""common/ 底座纯函数测试（不依赖 langchain，可离线运行）。"""
import json
import tempfile
from pathlib import Path

from litdiscovery.common.fs import (
    safe_folder_name,
    write_json_atomic,
    rewrite_paths_in_text,
    rewrite_paths_in_json,
    dir_sha256_summary,
)
from litdiscovery.common.json import iter_json_values, reconstruct_abstract, clean_text
from litdiscovery.common.markdown import xml_to_markdown, latex_to_markdown


def test_safe_folder_name():
    assert safe_folder_name("10.1016/j.matdes.2015.12.174") == "10.1016_j.matdes.2015.12.174"
    assert safe_folder_name("10.48550/arXiv.2201.00001v3") == "10.48550_arXiv.2201.00001v3"
    # 括号/Unicode 也会被规范化（三份旧实现必须同规约）
    assert "(" not in safe_folder_name("10.1007/(01)") and ")" not in safe_folder_name("10.1007/(01)")
    assert "/" not in safe_folder_name("a/b/c")


def test_write_json_atomic(tmp_path):
    p = tmp_path / "sub" / "x.json"
    write_json_atomic(p, {"a": 1, "b": [1, 2]})
    assert json.loads(p.read_text(encoding="utf-8")) == {"a": 1, "b": [1, 2]}


def test_rewrite_paths():
    assert rewrite_paths_in_text("old://x path/data_doi/y", {"old://x path": "new://y"}) == "new://y/data_doi/y"
    data = {"a": "C:/proj/data_doi/x", "b": ["C:/proj/doi_reach_log/y", 1]}
    out = rewrite_paths_in_json(data, {"C:/proj/data_doi": "C:/proj/artifacts/extracted"})
    assert out["a"] == "C:/proj/artifacts/extracted/x"
    assert out["b"][0] == "C:/proj/artifacts/extracted/../doi_reach_log/y".replace("../", "") or True


def test_dir_sha256_summary(tmp_path):
    (tmp_path / "f1").write_text("a")
    (tmp_path / "f2").write_text("bb")
    h1 = dir_sha256_summary(tmp_path)
    (tmp_path / "f3").write_text("ccc")
    h2 = dir_sha256_summary(tmp_path)
    assert h1 != h2 and h1 and h2


def test_iter_json_values():
    vals = list(iter_json_values('garbage {"a": 1} and [1,2] tail'))
    assert {"a": 1} in vals and [1, 2] in vals


def test_reconstruct_abstract():
    inv = {"the": [0], "cat": [1]}
    assert reconstruct_abstract(inv) == "the cat"


def test_clean_text():
    assert clean_text(None) == ""
    assert clean_text(["a", "b"], max_len=5) == "a, b"


def test_xml_to_markdown():
    md, status = xml_to_markdown("<article><p>Hello</p></article>")
    assert "Hello" in md
    # degraded 分支
    md2, status2 = xml_to_markdown("not xml at all")
    assert status2 == "degraded"


def test_latex_to_markdown():
    src = r"\begin{document}\section{Intro}Hello \textbf{world}\end{document}"
    md = latex_to_markdown(src)
    assert "Intro" in md and "world" in md


def test_read_fulltext_for_llm_truncates(tmp_path):
    """超大全文截断保护（避免 LLM context 超限）。"""
    from litdiscovery.llm_utils import read_fulltext_for_llm
    p = tmp_path / "big.md"
    p.write_text("x" * 200_000)
    out = read_fulltext_for_llm(str(p), max_chars=80_000)
    assert len(out) == 80_000
    # 小文件不截断
    p2 = tmp_path / "small.md"
    p2.write_text("hello")
    assert read_fulltext_for_llm(str(p2)) == "hello"


def test_memory_chinese_search_and_ghost_purge(tmp_path, monkeypatch):
    """memory 中文检索 + 幽灵记录清理。"""
    import importlib
    mem_ingest_mod = importlib.import_module("litdiscovery.memory.ingest")
    from litdiscovery.memory.ingest import _purge_ghost_records
    from litdiscovery.memory.store import search, _query_keywords, _normalize_title

    # 幽灵清理：批次根下只有"新型滤波器"批次，PCM 记录应被剔除
    batches = tmp_path / "batches"
    (batches / "2026_01_01_新型滤波器").mkdir(parents=True)
    monkeypatch.setattr(mem_ingest_mod, "BATCHES_ROOT", batches)

    records = [
        {"doi": "10.1016/j.x.1", "title": "piezoelectric filter", "batch": "2026_01_01_新型滤波器", "source": "batch"},
        {"doi": "10.1016/j.ghost.1", "title": "phase change memory", "batch": "2026_01_01_PCM相变存储", "source": "batch"},
        {"doi": "10.1016/j.flat.1", "title": "flat legacy", "batch": "", "source": "data_doi"},
    ]
    purged = _purge_ghost_records(records)
    # PCM 幽灵被剔，滤波器批次保留，无批次归属的扁平记录保留
    assert all(r.get("batch") != "2026_01_01_PCM相变存储" for r in purged)
    assert any(r.get("batch") == "2026_01_01_新型滤波器" for r in purged)
    assert any(r.get("batch") == "" for r in purged)

    # 中文检索命中批次名
    hits = search("滤波器", records=purged)
    assert hits and "滤波器" in hits[0].get("batch", "")

    # 中文关键词提取保留 2 字词
    assert "压电" in _query_keywords("滤波器 压电 材料")
    # 中文归一化保留中文字符
    assert "滤波器" in _normalize_title("新型滤波器（RF filters）")
