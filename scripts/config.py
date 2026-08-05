"""
统一配置读取模块

所有技能模块统一通过本模块获取配置，避免各模块各自读取 local_config.py。
读取优先级：环境变量 > local_config.py 文件
"""

import os
import logging

logger = logging.getLogger("config")

# local_config.py 所在路径
_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "local_config.py")


def _read_from_file(key: str) -> str:
    """从 local_config.py 中读取指定 key 的值"""
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                # 跳过空行和注释
                if not line or line.startswith("#"):
                    continue
                if line.startswith(key):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except FileNotFoundError:
        logger.warning("local_config.py 未找到，使用环境变量")
    except Exception as e:
        logger.warning(f"读取 local_config.py 出错: {e}")
    return ""


def _get_config(key: str) -> str:
    """获取配置值：环境变量优先，其次是 local_config.py"""
    val = os.environ.get(key, "")
    if val:
        return val
    return _read_from_file(key)


# ==================== 导出配置项 ====================

# DeepSeek API
DEEPSEEK_API_KEY: str = _get_config("DEEPSEEK_API_KEY")

# 钉钉 Stream 模式机器人凭证
DINGTALK_CLIENT_ID: str = _get_config("DINGTALK_CLIENT_ID")
DINGTALK_CLIENT_SECRET: str = _get_config("DINGTALK_CLIENT_SECRET")

# 钉钉 REST API
DINGTALK_API_BASE: str = "https://api.dingtalk.com"

# 知识库上传审核
# fixed：所有上传申请只推送给固定审核人（测试阶段）
# 后续可新增 department_manager，由审核人解析器按上传者部门选择主管
KNOWLEDGE_REVIEW_MODE: str = _get_config("KNOWLEDGE_REVIEW_MODE") or "fixed"
KNOWLEDGE_REVIEWER_STAFF_IDS: str = _get_config("KNOWLEDGE_REVIEWER_STAFF_IDS")

# 存储后端：sqlite（推荐）| json（回退）
MEMORY_BACKEND: str = "sqlite"

# 对话记忆轮数上限（一问一答算 1 轮，即 8 轮 = 16 条消息）
MAX_CONTEXT_ROUNDS: int = 8

# 管理员密码（空 = 不开启密码保护）
ADMIN_PASSWORD: str = _get_config("ADMIN_PASSWORD")

# MinerU API Token（精准解析）
MINERU_TOKEN: str = _get_config("MINERU_TOKEN")

# ── 双层记忆（长期记忆）配置 ───────────────────────────
LONG_TERM_MEMORY_ENABLED: bool = True   # 长期记忆总开关（False 时行为与升级前一致）
SESSION_TIMEOUT_MINUTES: int = 30       # 距上条消息超过该分钟数 → 新会话（仅用于 session_id 打标）
MAX_SESSION_ROUNDS: int = 12            # 未压缩消息达该轮数触发压缩（窗口 8 + 冗余 4）
COMPRESS_BATCH_ROUNDS: int = 8          # 每次压缩的对话轮数
LONG_TERM_MAX_ITEMS: int = 8            # 注入 system prompt 的长期条目上限
LONG_TERM_MAX_PER_USER: int = 50        # 每用户长期条目上限（超限先淘汰 summary）
LONG_TERM_ITEM_MAX_CONTENT: int = 150   # 单条长期记忆注入截断长度
