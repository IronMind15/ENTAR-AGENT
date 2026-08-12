"""
MinerU 输出批量同步脚本

扫描 data/standards/mineru_output/ 目录，
找到每个文件夹的 full.md，处理后入库到 Chroma。

用法：
    python scripts/sync_mineru.py              # 处理全部
    python scripts/sync_mineru.py --list       # 仅列出待处理文件
    python scripts/sync_mineru.py --file "EN50178"  # 处理指定文件
"""

import os
import sys
import io
import logging
import argparse
from pathlib import Path

# 修复 Windows 控制台编码
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# 添加 scripts 目录到 path
sys.path.insert(0, str(Path(__file__).parent))

from doc_mgr.engine import process_file
from doc_mgr.storage import get_store

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("sync_mineru")

# MinerU 输出目录
MINERU_OUTPUT_DIR = Path(__file__).parent.parent / "data" / "standards" / "mineru_output"


def find_markdown_files() -> list[dict]:
    """扫描 mineru_output 目录，找到所有 full.md 文件

    Returns:
        [{"md_path": "...", "images_dir": "...", "folder_name": "..."}, ...]
    """
    results = []

    if not MINERU_OUTPUT_DIR.exists():
        logger.warning(f"目录不存在: {MINERU_OUTPUT_DIR}")
        return results

    for folder in MINERU_OUTPUT_DIR.iterdir():
        if not folder.is_dir():
            continue

        # 跳过 ZIP 文件解压后的临时目录
        if folder.name.endswith(".zip"):
            continue

        # 审查 Critical 3：跳过 MinerU 缓存目录（{name}-mineru-cache），
        # 避免把缓存中间产物当独立文档重复入库
        if folder.name.endswith("-mineru-cache"):
            continue

        md_path = folder / "full.md"
        images_dir = folder / "images"

        if md_path.exists():
            results.append({
                "md_path": str(md_path),
                "images_dir": str(images_dir) if images_dir.exists() else None,
                "folder_name": folder.name,
            })

    return results


def process_single(md_path: str, folder_name: str, store) -> dict:
    """处理单个 MinerU 输出文件夹"""
    logger.info(f"处理: {folder_name}")

    try:
        doc = process_file(
            md_path,
            file_name=folder_name,
            target_collection="standards",
        )
        return {
            "folder": folder_name,
            "status": doc.status,
            "std_id": doc.std_id,
            "std_title": doc.std_title,
            "chunk_count": doc.chunk_count,
            "message": doc.message,
        }
    except Exception as e:
        logger.error(f"处理失败: {e}")
        return {
            "folder": folder_name,
            "status": "error",
            "message": str(e),
        }


def main():
    parser = argparse.ArgumentParser(description="MinerU 输出批量同步到 Chroma")
    parser.add_argument("--list", action="store_true", help="仅列出待处理文件")
    parser.add_argument("--file", type=str, help="处理指定文件夹（模糊匹配）")
    args = parser.parse_args()

    # 扫描文件
    files = find_markdown_files()
    logger.info(f"找到 {len(files)} 个 MinerU 输出文件夹")

    if args.list:
        for f in files:
            logger.info(f"  📄 {f['folder_name']}")
            logger.info(f"     MD: {f['md_path']}")
            logger.info(f"     图片: {f['images_dir'] or '无'}")
        return

    # 过滤指定文件
    if args.file:
        files = [f for f in files if args.file.lower() in f["folder_name"].lower()]
        if not files:
            logger.error(f"未找到匹配的文件夹: {args.file}")
            return

    # 初始化 store（提前加载 embedding 模型）
    logger.info("初始化向量数据库...")
    store = get_store()

    # 逐个处理
    results = []
    for f in files:
        result = process_single(f["md_path"], f["folder_name"], store)
        results.append(result)
        logger.info("")

    # 汇总
    success = sum(1 for r in results if r["status"] == "done")
    total_chunks = sum(r.get("chunk_count", 0) for r in results)

    logger.info("=" * 60)
    logger.info(f"📊 处理完成: {success}/{len(results)} 成功")
    logger.info(f"   总切块数: {total_chunks}")
    logger.info(f"   standards collection: {store.count('standards')} 条")
    logger.info("=" * 60)

    # 显示详细结果
    for r in results:
        status_icon = "✅" if r["status"] == "done" else "❌"
        logger.info(f"  {status_icon} {r['folder']}")
        if r.get("std_id"):
            logger.info(f"     标准: {r['std_id']} {r.get('std_title', '')[:30]}")
        logger.info(f"     切块: {r['chunk_count']} 条 | {r['message']}")


if __name__ == "__main__":
    main()
