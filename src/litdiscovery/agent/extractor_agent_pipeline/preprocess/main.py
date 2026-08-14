"""
全文预处理主运行脚本（extractor_agent_pipeline.preprocess 的 CLI 入口）。

流水线阶段:
    Stage 1: Markdown 解析  → 从 Markdown 论文中提取全文 + 表格
    Stage 2: 标题清洗        → 移除表格标题中的数据行污染
    Stage 3: 全文清理+Token  → 删除章节/图片垃圾、统计 token
    Stage 4: HTML 转换       → 存量 HTML 表格转为统一 CSV
    Stage 5: CSV 清理        → 删除稀疏列

默认输入/输出:
    - 输入: 自动定位最新批次根中的 markdowns/ 目录
    - 输出: 与 pdfs 同级的 end_mds/ 目录
    - 可通过 --input / --output 指定自定义路径

用法:
    python -m litdiscovery.agent.extractor_agent_pipeline.preprocess.main              # 运行全部阶段
    python -m litdiscovery.agent.extractor_agent_pipeline.preprocess.main --stage 1    # 仅运行 Markdown 解析
    python -m litdiscovery.agent.extractor_agent_pipeline.preprocess.main --stage 3    # 仅运行章节删除与 Token 统计
    python -m litdiscovery.agent.extractor_agent_pipeline.preprocess.main --stages 1,2,3  # 运行指定阶段
    python -m litdiscovery.agent.extractor_agent_pipeline.preprocess.main --force         # 强制重新解析所有 Markdown
    python -m litdiscovery.agent.extractor_agent_pipeline.preprocess.main --dry-run       # 仅列出将处理的 Markdown
    python -m litdiscovery.agent.extractor_agent_pipeline.preprocess.main --input my_md --output my_output  # 自定义目录
"""

import argparse
import os
import sys

from .md_parser import process_md_directory, process_xml_directory
from .caption_cleaner import clean_table_captions
from .sentence_filter import count_total_tokens
from .reference_truncator import (
    remove_introduction_and_references,
    remove_image_artifacts,
)
from .html_table_converter import convert_html_tables_to_csv
from .csv_cleaner import drop_sparse_columns

from litdiscovery.paths import latest_batch


def resolve_default_paths():
    """解析默认输入输出路径。

    最新批次目录由 paths.latest_batch 统一定位（单一事实源），
    这里只拼接 markdowns/（输入）与 end_mds/（输出）两个子目录。

    返回:
        (input_dir, output_dir)
    """
    latest_folder = latest_batch(require_end_mds=False)
    input_dir = os.path.join(latest_folder, "markdowns")
    output_dir = os.path.join(latest_folder, "end_mds")
    return input_dir, output_dir


def run_stage_1(input_dir: str, output_dir: str,
                force: bool = False, dry_run: bool = False) -> None:
    """Stage 1: Markdown/XML → fulltext.md + table*.csv（增量解析，兼容 md + xml）"""
    print("\n" + "=" * 60)
    print("  STAGE 1: Markdown/XML 解析与表格提取")
    print("=" * 60)
    process_md_directory(
        input_md_dir=input_dir,
        output_root_dir=output_dir,
        force=force,
        dry_run=dry_run,
    )
    # 兼容 xml 输入（同一输入目录下的 .xml/.xml.gz）
    process_xml_directory(
        input_xml_dir=input_dir,
        output_root_dir=output_dir,
        force=force,
        dry_run=dry_run,
    )


def run_stage_2(output_dir: str) -> None:
    """Stage 2: 表格标题清洗"""
    print("\n" + "=" * 60)
    print("  STAGE 2: 表格标题清洗")
    print("=" * 60)
    clean_table_captions(output_dir)


def run_stage_3(output_dir: str) -> None:
    """Stage 3: 清理全文 + 统计 token

    依次执行：
        1. 删除 Introduction/References 章节
        2. 删除从图片错误提取的垃圾内容（HTML 注释、坐标轴数字/标签、面板标记）
        3. 统计各论文 token 数，写入 token_count.txt
    """
    print("\n" + "=" * 60)
    print("  STAGE 3: 全文清理与 Token 统计")
    print("=" * 60)
    remove_introduction_and_references(output_dir)
    remove_image_artifacts(output_dir)
    count_total_tokens(output_dir)


def run_stage_4(html_dir: str) -> None:
    """Stage 4: HTML 表格 → CSV 转换。"""
    print("\n" + "=" * 60)
    print("  STAGE 4: HTML 表格转换")
    print("=" * 60)
    convert_html_tables_to_csv(html_dir)


def run_stage_5(output_dir: str) -> None:
    """Stage 5: CSV 稀疏列清理"""
    print("\n" + "=" * 60)
    print("  STAGE 5: CSV 稀疏列清理")
    print("=" * 60)
    drop_sparse_columns(output_dir)



STAGE_MAP = {
    1: ("Markdown/XML 解析与表格提取", run_stage_1),
    2: ("表格标题清洗", run_stage_2),
    3: ("全文清理与Token统计", run_stage_3),
    4: ("HTML表格转换", run_stage_4),
    5: ("CSV稀疏列清理", run_stage_5),
}


def main(argv: "list[str] | None" = None):
    """主入口。argv 缺省时读取 sys.argv（CLI 直接运行），
    也可传入参数列表（供 download 自动转换链以函数方式调用）。"""
    parser = argparse.ArgumentParser(
        description=" Markdown 文献预处理流水线",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python -m litdiscovery.agent.extractor_agent_pipeline.preprocess.main               # 运行全部 5 个阶段
  python -m litdiscovery.agent.extractor_agent_pipeline.preprocess.main --stage 1     # 仅运行 Markdown 解析
  python -m litdiscovery.agent.extractor_agent_pipeline.preprocess.main --stages 1,2,3   # 运行前三个阶段
  python -m litdiscovery.agent.extractor_agent_pipeline.preprocess.main --input my_md --output my_output
        """
    )
    parser.add_argument(
        "--stage", type=int, choices=range(1, 6), default=None,
        help="仅运行指定阶段 (1-5)"
    )
    parser.add_argument(
        "--stages", type=str, default=None,
        help="运行指定阶段列表，逗号分隔 (如 1,2,3)"
    )
    parser.add_argument(
        "--input", type=str, default=None,
        help="Markdown 输入目录（默认: 最新批次根的 markdowns/）"
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="主要输出目录（默认: 最新批次根的 end_mds/）"
    )
    parser.add_argument(
        "--html-dir", type=str, default="filtered_abstracts",
        help="HTML 表格目录（默认: filtered_abstracts）"
    )
    parser.add_argument(
        "--force", action="store_true",
        help="强制重新处理已存在的 Markdown 解析输出（默认跳过已完成文件）"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="仅列出将处理的 Markdown 文件，不实际执行（仅对 Stage 1 生效）"
    )


    args = parser.parse_args(argv)

    # 确定要运行的阶段
    if args.stages:
        selected_stages = [int(s.strip()) for s in args.stages.split(",")]
    elif args.stage:
        selected_stages = [args.stage]
    else:
        selected_stages = list(range(1, 6))

    # 解析默认输入/输出路径
    if not args.input or not args.output:
        try:
            default_input, default_output = resolve_default_paths()
        except FileNotFoundError as e:
            print(f"\n❌ {e}")
            print("   请通过 --input / --output 指定路径。")
            sys.exit(1)

        if not args.input:
            args.input = default_input
        if not args.output:
            args.output = default_output

    print(f"\n[*] Will run stages: {selected_stages}")
    print(f"   Markdown 输入目录: {args.input}")
    print(f"   主要输出目录:      {args.output}")

    # 逐阶段执行
    for stage_num in sorted(selected_stages):
        name, func = STAGE_MAP[stage_num]
        print(f"\n{'=' * 30}  Stage {stage_num}: {name}  {'=' * 30}")

        try:
            if stage_num == 1:
                func(args.input, args.output, args.force, args.dry_run)
            elif stage_num in (2, 3, 5):
                func(args.output)
            elif stage_num == 4:
                func(args.html_dir)
        except Exception as e:
            print(f"\n❌ Stage {stage_num} 失败: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)

    print("\n" + "=" * 60)
    print("  [OK] Pipeline complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
