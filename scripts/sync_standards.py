"""
同步脚本：标准 PDF → Chroma 向量库（standards collection）

薄包装：委托 doc_mgr.engine 处理，保持向后兼容。

逐份 PDF 处理并输出统计报告。
扫描型 PDF 会标记为 ocr_needed 状态，不会阻止继续处理后续 PDF。
"""

import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("sync_standards")

if sys.platform == "win32":
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")

from doc_mgr.engine import process_file

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
STD_DIR = os.path.join(_PROJECT_ROOT, "..", "data", "standards")


def sync():
    logger.info("=" * 60)
    logger.info("  恩特能源 - 标准知识库同步")
    logger.info("=" * 60)

    # [1/3] 扫描 PDF 文件
    logger.info("[1/3] 扫描标准 PDF 文件...")
    if not os.path.isdir(STD_DIR):
        logger.error(f"  目录不存在: {STD_DIR}")
        return

    pdf_files = sorted([
        f for f in os.listdir(STD_DIR)
        if f.lower().endswith('.pdf')
    ])

    if not pdf_files:
        logger.warning("  未找到 PDF 文件")
        return

    logger.info(f"  找到 {len(pdf_files)} 份 PDF: {', '.join(pdf_files)}")

    # [2/3] 处理每份 PDF
    logger.info("[2/3] 逐份处理...")
    stats = []
    for pdf_file in pdf_files:
        filepath = os.path.join(STD_DIR, pdf_file)
        logger.info(f"\n  📄 {pdf_file}")
        doc = process_file(filepath)
        logger.info(f"    → {doc.status}: {doc.message}")
        stats.append(doc)

    # [3/3] 汇总报告
    logger.info("\n" + "=" * 60)
    logger.info("  同步完成!")
    logger.info("=" * 60)

    done = [d for d in stats if d.status == "done"]
    skipped = [d for d in stats if d.status == "skipped"]
    ocr = [d for d in stats if d.status == "ocr_needed"]
    failed = [d for d in stats if d.status == "error"]

    logger.info(f"  成功: {len(done)} 份")
    logger.info(f"  跳过: {len(skipped)} 份")
    logger.info(f"  需OCR: {len(ocr)} 份")
    logger.info(f"  失败: {len(failed)} 份")

    for d in stats:
        status_icon = {"done": "✅", "skipped": "⏭", "ocr_needed": "⚠", "error": "❌"}
        icon = status_icon.get(d.status, "❓")
        logger.info(f"  {icon} {d.file_name}: {d.chunk_count}块 ({d.status})")

    if ocr:
        logger.warning(f"\n  以下 {len(ocr)} 份为扫描型 PDF，需要 OCR 处理：")
        for d in ocr:
            logger.warning(f"    ⚠ {d.file_name}")

    logger.info("=" * 60)


if __name__ == "__main__":
    sync()
