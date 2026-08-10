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

# DeepSeek 并发请求上限（多人同时问 LLM 时限制并发，防费用失控 / 429）
MAX_CONCURRENT_LLM: int = 20

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

# 通讯录查询：敏感字段（手机号/邮箱）审核人白名单（staff_id，逗号/空格/分号分隔）
# 留空 = 无人可查联系方式，所有用户只返回姓名/部门/职位/工号
CONTACT_ADMIN_STAFF_IDS: str = _get_config("CONTACT_ADMIN_STAFF_IDS")
# 通讯录全量缓存 TTL（秒）。进程内短缓存，规避钉钉 QPS 限流，不做磁盘持久化。
# 钉钉侧通讯录变更最多延迟 TTL 秒可见；设小接近实时但每次查询都会重拉全量。
CONTACT_CACHE_TTL_SECONDS: int = int(_get_config("CONTACT_CACHE_TTL_SECONDS") or 60)

# 存储后端：sqlite（推荐）| json（回退）
MEMORY_BACKEND: str = "sqlite"

# 短期窗口轮数上限（一问一答算 1 轮）。窗口 20 轮 + 释放 1/2：
# 满 20 轮时一次滚掉前 10 轮、保留最近 10 轮（批量滚动，KV Cache 友好）
MAX_CONTEXT_ROUNDS: int = 20

# 短记忆注入总预算（字数，超出从最早丢弃）
# 为「窗口 20 轮 + 静态 system」改造预留：20 轮约 8400 字 + 系统提示/档案/长记忆，留足冗余
MEMORY_BUDGET_TOKENS: int = 20000

# 管理员密码（空 = 不开启密码保护）
ADMIN_PASSWORD: str = _get_config("ADMIN_PASSWORD")

# 钉钉管理员模式口令（v1.10.2）：用户在钉钉聊天发该口令即进入管理员模式，
# 可「查看全部文件」并删改任意用户的上传文件。明文口令、会话为内存态，
# 属内部工具轻量方案；未来上 SSO 可信身份后可移除。
ADMIN_MASTER_CODE: str = _get_config("ADMIN_MASTER_CODE") or "ENTARBOSS"

# MinerU API Token（精准解析）
MINERU_TOKEN: str = _get_config("MINERU_TOKEN")

# 阿里云百炼 DashScope（千问视觉识图）
DASHSCOPE_API_KEY: str = _get_config("DASHSCOPE_API_KEY")
VISION_MODEL: str = _get_config("VISION_MODEL") or "qwen3.7-flash"

# ── 双层记忆（长期记忆）配置 ───────────────────────────
LONG_TERM_MEMORY_ENABLED: bool = True   # 长期记忆总开关（False 时行为与升级前一致）
SESSION_TIMEOUT_MINUTES: int = 30       # 距上条消息超过该分钟数 → 新会话（仅用于 session_id 打标）
MAX_SESSION_ROUNDS: int = 12            # 兼容保留（压缩现由「滚动点前有未压缩对话」驱动，不再按该轮数触发）
COMPRESS_BATCH_ROUNDS: int = 10         # 每次压缩的对话轮数（对应释放 1/2 滚掉的 10 轮）
LONG_TERM_MAX_ITEMS: int = 10            # 注入 system prompt 的长期条目上限（原 8）
LONG_TERM_MAX_PER_USER: int = 200        # 每用户长期条目上限（原 50，防长对话用户不够用；超限先淘汰 summary）
LONG_TERM_ITEM_MAX_CONTENT: int = 200    # 单条长期记忆注入截断长度（原 150）
