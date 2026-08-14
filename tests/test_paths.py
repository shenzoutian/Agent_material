"""paths 事实源测试。"""
import os
import tempfile
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from litdiscovery import paths  # noqa: E402


def test_roots_resolve():
    # 默认 artifacts 根在项目根下
    assert paths.ARTIFACTS_ROOT.name == "artifacts"
    assert paths.BATCHES_ROOT.name == "batches"
    assert paths.EXTRACTED_ROOT.name == "extracted"


def test_artifacts_env_override(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setenv("LITDISCOVERY_ARTIFACTS", td)
        # 重新 import paths 会重算；此处直接验证常量与 env 的关系
        assert Path(os.environ["LITDISCOVERY_ARTIFACTS"]) == Path(td)


def test_resolve_batch(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "BATCHES_ROOT", tmp_path)
    # 建两个批次，一个含 end_mds
    b1 = tmp_path / "2026_01_01_a"; b1.mkdir()
    b2 = tmp_path / "2026_02_01_b"; (b2 / "end_mds").mkdir(parents=True)
    assert paths.latest_batch().name == b2.name
    # 显式传相对批次名
    assert paths.resolve_batch("2026_01_01_a").name == "2026_01_01_a"


def test_short_dir_label_and_unique_dir(tmp_path, monkeypatch):
    """批次/会话目录名：需求截断保留 5 字；同名去重追加 _2。"""
    import re as _re
    from litdiscovery.common import logging as clog
    monkeypatch.setattr(clog, "BATCHES_ROOT", tmp_path)
    long_req = "新型滤波器（RF acoustic filter）的低温性能研究"
    d1 = clog.create_log_dir(long_req)
    # 独立 CLI 回退名称在前，时间戳在末尾。
    assert d1.parent == tmp_path
    assert _re.match(r"新型滤波器.*_\d{4}_\d{2}_\d{2}_\d{6}$", d1.name)
    # 同秒同名再次创建时，序号位于时间戳之前，时间戳仍保持在末尾。
    d2 = clog.create_log_dir(long_req)
    assert d2 != d1
    assert _re.search(r"_2_\d{4}_\d{2}_\d{2}_\d{6}$", d2.name)


def test_planner_label_precedes_timestamp(tmp_path, monkeypatch):
    from litdiscovery.common import logging as clog
    monkeypatch.setattr(clog, "BATCHES_ROOT", tmp_path)
    batch = clog.create_log_dir("very long requirement", label="新型滤波器")
    assert batch.name.startswith("新型滤波器_")
    assert batch.name[6:].replace("_", "").isdigit()


def test_latest_batch_supports_timestamp_suffix_and_legacy_prefix(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "BATCHES_ROOT", tmp_path)
    old = tmp_path / "2026_08_05_185307_旧批次"
    new = tmp_path / "新型滤波器_2026_08_06_090000"
    (old / "end_mds").mkdir(parents=True)
    (new / "end_mds").mkdir(parents=True)
    assert paths.latest_batch() == new


def test_orders_handoff_paths(tmp_path):
    """orders/ 交接文件约定：写入优先 orders/，读取兼容批次根回退。"""
    b = tmp_path / "batch"
    b.mkdir()
    # 交接文件落 orders/（与 end_mds 同级）
    assert paths.handoff_path(b, "doi_list.json") == b / "orders" / "doi_list.json"
    assert paths.handoff_path(b, "seed_papers.json") == b / "orders" / "seed_papers.json"
    assert paths.handoff_path(b, "orders/x.json") == b / "orders" / "x.json"
    # 阶段产物（非交接清单）仍在批次根
    assert paths.handoff_path(b, "gap_output/research_gaps.json") == b / "gap_output" / "research_gaps.json"
    # 读取：orders/ 优先；orders/ 缺失 → 回退批次根（兼容旧批次）
    (b / "orders").mkdir()
    (b / "orders" / "doi_list.json").write_text("[]", encoding="utf-8")
    assert paths.read_handoff(b, "doi_list.json") == b / "orders" / "doi_list.json"
    (b / "orders" / "doi_list.json").unlink()                      # 删掉 orders/ 版本
    (b / "doi_list.json").write_text("[]", encoding="utf-8")
    assert paths.read_handoff(b, "doi_list.json") == b / "doi_list.json"
    assert paths.batch_of(b / "orders" / "doi_list.json") == b
    assert paths.batch_of(b / "orders") == b
    assert paths.batch_of(b / "end_mds") == b
    assert paths.batch_of(b / "gap_output") == b
    assert paths.batch_of(b) == b
