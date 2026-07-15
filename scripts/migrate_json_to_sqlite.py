"""
数据迁移工具：chat_memory.json → SQLite

将现有 data/chat_memory.json 中的对话记录导入 SQLite user_store.db。
一次性工具，运行后 JSON 文件可作为备份保留。

用法：
    python scripts/migrate_json_to_sqlite.py

可选参数：
    --json-path  指定 JSON 文件路径（默认 data/chat_memory.json）
    --dry-run    仅预览迁移数据量，不执行写入
"""

import argparse
import json
import logging
import os
import sys

# 确保 scripts/ 在模块搜索路径中
_PARENT = os.path.dirname(os.path.abspath(__file__))
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("migrate")


def main():
    parser = argparse.ArgumentParser(description="chat_memory.json → SQLite 迁移工具")
    parser.add_argument(
        "--json-path",
        default=None,
        help="chat_memory.json 路径（默认自动查找）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅预览，不写入",
    )
    args = parser.parse_args()

    # 确定 JSON 文件路径
    if args.json_path:
        json_path = args.json_path
    else:
        json_path = os.path.join(_PARENT, "..", "data", "chat_memory.json")
    json_path = os.path.abspath(json_path)

    if not os.path.exists(json_path):
        logger.error(f"JSON 文件不存在: {json_path}")
        sys.exit(1)

    # 读取 JSON
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.error(f"读取 JSON 文件失败: {e}")
        sys.exit(1)

    if not isinstance(data, dict):
        logger.error("JSON 格式错误：期望 dict[user_id, list[messages]]")
        sys.exit(1)

    # 统计
    total_users = len(data)
    total_messages = sum(len(msgs) for msgs in data.values())
    logger.info(f"发现 {total_users} 个用户，共 {total_messages} 条消息")

    for user_id, messages in data.items():
        logger.info(f"  {user_id}: {len(messages)} 条消息")

    if args.dry_run:
        logger.info("Dry-run 模式，未执行写入")
        return

    # 执行迁移
    from user_store import get_store
    store = get_store()

    imported = 0
    for user_id, messages in data.items():
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role in ("user", "assistant") and content:
                store.add_memory(user_id, role, content)
                imported += 1

    logger.info(f"迁移完成！共导入 {imported} 条消息到 SQLite")
    logger.info(f"数据位置: {os.path.join(_PARENT, '..', 'data', 'user_store.db')}")
    logger.info(f"原 JSON 文件保留未删除: {json_path}")


if __name__ == "__main__":
    main()
