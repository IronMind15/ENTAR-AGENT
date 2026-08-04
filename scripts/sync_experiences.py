"""
同步脚本：经验文档 Markdown → Chroma 向量库（experience_kb collection）

薄包装：委托 doc_mgr.engine 处理，扫描 data/experience/*.md，
显式指定 target_collection="experience_kb" 入库。

经验库与标准库共用 .md 处理链路（MarkdownChunker 按标题切块），
差异仅在目标 collection 与目录。
"""

import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("sync_experiences")

if sys.platform == "win32":
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")

from doc_mgr.engine import process_file

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
EXPERIENCE_DIR = os.path.join(_PROJECT_ROOT, "..", "data", "experience")


def sync():
    logger.info("=" * 60)
    logger.info("  恩特能源 - 经验知识库同步（experience_kb）")
    logger.info("=" * 60)

    # [1/3] 扫描经验 Markdown 文件
    logger.info("[1/3] 扫描经验文档...")
    if not os.path.isdir(EXPERIENCE_DIR):
        logger.error(f"  目录不存在: {EXPERIENCE_DIR}")
        return

    md_files = sorted([
        f for f in os.listdir(EXPERIENCE_DIR)
        if f.lower().endswith(".md")
    ])

    if not md_files:
        logger.warning("  未找到经验文档（.md）")
        return

    logger.info(f"  找到 {len(md_files)} 份经验文档: {', '.join(md_files)}")

    # [2/3] 处理每份文档
    logger.info("[2/3] 逐份处理...")
    stats = []
    for md_file in md_files:
        filepath = os.path.join(EXPERIENCE_DIR, md_file)
        logger.info(f"\n  📄 {md_file}")
        doc = process_file(filepath, target_collection="experience_kb")
        logger.info(f"    → {doc.status}: {doc.message}")
        stats.append(doc)

    # [3/3] 汇总报告
    logger.info("\n" + "=" * 60)
    logger.info("  同步完成!")
    logger.info("=" * 60)

    done = [d for d in stats if d.status == "done"]
    skipped = [d for d in stats if d.status == "skipped"]
    failed = [d for d in stats if d.status == "error"]

    logger.info(f"  成功: {len(done)} 份")
    logger.info(f"  跳过: {len(skipped)} 份")
    logger.info(f"  失败: {len(failed)} 份")

    for d in stats:
        status_icon = {"done": "✅", "skipped": "⏭", "error": "❌"}
        icon = status_icon.get(d.status, "❓")
        logger.info(f"  {icon} {d.file_name}: {d.chunk_count}块 ({d.status})")

    logger.info("=" * 60)


if __name__ == "__main__":
    sync()
