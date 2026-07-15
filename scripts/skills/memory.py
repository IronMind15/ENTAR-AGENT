"""
会话记忆模块 — 统一代理层

现在作为代理层，支持两种后端：
  - sqlite: 委托给 user_store.py（推荐，Phase 1 升级目标）
  - json:   原有 JSON 文件方式（兼容回退）

通过 config.py 中 MEMORY_BACKEND 配置切换，默认 "sqlite"。
外部通过 from skills import memory 使用，接口不变。
"""

import json
import os
import logging
from typing import Optional

logger = logging.getLogger("memory")

_MEMORY_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data",
    "chat_memory.json",
)

# ===== 后端选择 =====

def _get_backend() -> str:
    """获取配置的存储后端（带默认值）"""
    try:
        # memory.py 在 scripts/skills/ 下，config 在 scripts/ 下
        import importlib
        cfg = importlib.import_module("config")
        return getattr(cfg, "MEMORY_BACKEND", "sqlite")
    except Exception:
        return "sqlite"


def _get_max_rounds() -> int:
    """获取配置的最大轮数"""
    try:
        import importlib
        cfg = importlib.import_module("config")
        return int(getattr(cfg, "MAX_CONTEXT_ROUNDS", 5))
    except Exception:
        return 5


def _get_sqlite_store():
    """懒加载 SQLite 存储"""
    from user_store import get_store
    return get_store()


# ===== 统一接口（外部代码零改动） =====

def add(user_id: str, role: str, content: str):
    """添加一条对话记录"""
    if _get_backend() == "sqlite":
        try:
            store = _get_sqlite_store()
            store.add_memory(user_id, role, content)
            return
        except Exception as e:
            logger.warning(f"SQLite 写入失败，回退 JSON: {e}")

    # JSON 后端（原有逻辑）
    _ensure_loaded()
    global _memories
    if user_id not in _memories:
        _memories[user_id] = []
    if _memories[user_id] and _memories[user_id][-1].get("role") == role \
            and _memories[user_id][-1].get("content") == content:
        return
    _memories[user_id].append({"role": role, "content": content})
    max_items = _MAX_ROUNDS * 2
    if len(_memories[user_id]) > max_items:
        _memories[user_id] = _memories[user_id][-max_items:]
    _save()


def get_context(user_id: str) -> list[dict]:
    """获取用户最近对话"""
    if _get_backend() == "sqlite":
        try:
            store = _get_sqlite_store()
            max_rounds = _get_max_rounds()
            return store.get_context(user_id, max_rounds)
        except Exception as e:
            logger.warning(f"SQLite 读取失败，回退 JSON: {e}")

    _ensure_loaded()
    return _memories.get(user_id, [])


def format_context(user_id: str, max_content: int = 200) -> str:
    """格式化最近对话为文字（供拼进 system prompt）"""
    if _get_backend() == "sqlite":
        try:
            store = _get_sqlite_store()
            max_rounds = _get_max_rounds()
            return store.format_context(user_id, max_rounds, max_content)
        except Exception as e:
            logger.warning(f"SQLite 格式化失败，回退 JSON: {e}")

    _ensure_loaded()
    context = _memories.get(user_id, [])
    if not context:
        return ""
    lines = ["\n\n## 最近的对话历史"]
    for msg in context:
        speaker = "用户" if msg["role"] == "user" else "助手"
        content = msg["content"][:max_content]
        lines.append(f"{speaker}：{content}")
    return "\n".join(lines)


# ===== JSON 后端保留逻辑（兼容回退） =====

_MAX_ROUNDS = 5
_memories: dict[str, list[dict]] = {}
_loaded = False


def _ensure_loaded():
    """确保 JSON 数据已加载（懒加载）"""
    global _memories, _loaded
    if _loaded:
        return
    try:
        if os.path.exists(_MEMORY_FILE):
            with open(_MEMORY_FILE, "r", encoding="utf-8") as f:
                _memories = json.load(f)
            max_items = _MAX_ROUNDS * 2
            for uid in list(_memories.keys()):
                if len(_memories[uid]) > max_items:
                    _memories[uid] = _memories[uid][-max_items:]
            logger.info(f"[JSON] 已加载 {len(_memories)} 个用户的记忆")
    except Exception as e:
        logger.warning(f"加载记忆文件失败: {e}")
        _memories = {}
    _loaded = True


def _save():
    """写入 JSON 文件持久化"""
    try:
        os.makedirs(os.path.dirname(_MEMORY_FILE), exist_ok=True)
        with open(_MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(_memories, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"保存记忆文件失败: {e}")
