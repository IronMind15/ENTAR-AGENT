"""
轻量会话记忆模块

以 JSON 文件持久化存储用户最近 N 轮对话。
故障查询不碰记忆，只给聊天（chat.py）用。
"""

import json
import os
import logging

logger = logging.getLogger("memory")

# 记忆文件路径：data/chat_memory.json
_MEMORY_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data",
    "chat_memory.json",
)

_MAX_ROUNDS = 5  # 最多记住 5 轮对话（一问一答算 1 轮）

# 内存缓存（程序运行期间读写内存，启动/关闭时读写文件）
_memories: dict[str, list[dict]] = {}


def _load():
    """启动时从 JSON 文件加载记忆"""
    global _memories
    try:
        if os.path.exists(_MEMORY_FILE):
            with open(_MEMORY_FILE, "r", encoding="utf-8") as f:
                _memories = json.load(f)
            # 加载后立即裁剪，避免旧数据累积超过 5 轮
            max_items = _MAX_ROUNDS * 2
            for uid in list(_memories.keys()):
                if len(_memories[uid]) > max_items:
                    _memories[uid] = _memories[uid][-max_items:]
            logger.info(f"已加载 {len(_memories)} 个用户的记忆")
    except Exception as e:
        logger.warning(f"加载记忆文件失败: {e}")
        _memories = {}


def _save():
    """写入 JSON 文件持久化"""
    try:
        os.makedirs(os.path.dirname(_MEMORY_FILE), exist_ok=True)
        with open(_MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(_memories, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"保存记忆文件失败: {e}")


def add(user_id: str, role: str, content: str):
    """添加一条对话记录

    Args:
        user_id: 用户唯一标识（钉钉 sender_id）
        role: "user" 或 "assistant"
        content: 对话内容
    """
    if user_id not in _memories:
        _memories[user_id] = []

    # 防重复：如果最后一条同 role 的消息内容一样，跳过
    if _memories[user_id] and _memories[user_id][-1].get("role") == role and _memories[user_id][-1].get("content") == content:
        return

    _memories[user_id].append({"role": role, "content": content})

    # 只保留最近 _MAX_ROUNDS 轮（一问一答 = 2 条）
    max_items = _MAX_ROUNDS * 2
    if len(_memories[user_id]) > max_items:
        _memories[user_id] = _memories[user_id][-max_items:]

    _save()


def get_context(user_id: str) -> list[dict]:
    """获取用户最近对话（直接从内存读，无磁盘 I/O）"""
    return _memories.get(user_id, [])


def format_context(user_id: str, max_content: int = 200) -> str:
    """把最近对话格式化成文字（供拼进系统提示词）

    Args:
        user_id: 用户标识
        max_content: 单条内容最大字符数（防刷 token）

    Returns:
        格式化的对话历史文字，如无则返回空字符串
    """
    context = get_context(user_id)
    if not context:
        return ""

    lines = ["\n\n## 最近的对话历史"]
    for msg in context:
        speaker = "用户" if msg["role"] == "user" else "助手"
        content = msg["content"][:max_content]
        lines.append(f"{speaker}：{content}")
    return "\n".join(lines)


# ==== 模块加载时自动读取 ====
_load()
