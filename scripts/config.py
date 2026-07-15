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

# 存储后端：sqlite（推荐）| json（回退）
MEMORY_BACKEND: str = "sqlite"

# 对话记忆轮数上限（一问一答算 1 轮，即 5 轮 = 10 条消息）
MAX_CONTEXT_ROUNDS: int = 5

# 管理员密码（空 = 不开启密码保护）
ADMIN_PASSWORD: str = _get_config("ADMIN_PASSWORD")
