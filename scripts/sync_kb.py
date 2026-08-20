"""
同步脚本：PCS参数表 V1.6.2.xlsx → Chroma 向量库（故障代码）

薄包装：委托 doc_mgr.engine 处理，保持向后兼容。
"""

import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("sync_kb")

if sys.platform == "win32":
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")

from scripts.doc_mgr.engine import process_file
from scripts.paths import FAULT_CODES_DIR

EXCEL_FILE = str(FAULT_CODES_DIR / "PCS参数表 V1.6.2.xlsx")


def sync():
    logger.info("=" * 50)
    logger.info("  恩特能源 - 知识库同步（故障代码）")
    logger.info("=" * 50)

    doc = process_file(EXCEL_FILE)
    logger.info(f"  状态: {doc.status}")
    logger.info(f"  新增: {doc.chunk_count} 条故障代码")
    logger.info(f"  消息: {doc.message}")
    logger.info("=" * 50)
    logger.info("  同步完成!")
    logger.info("=" * 50)


if __name__ == "__main__":
    sync()
