"""
管理后台「系统设置」配置覆盖层

把 local_config.py 中的敏感配置搬到 /admin「系统设置」页面手动输入，
写入 data/admin_config.json（运行时数据，已 gitignore），**不修改
local_config.py 原文件**（保留注释与兜底，写坏风险隔离）。

读取优先级（见 config.py）：
    环境变量 > admin_config.json（网页填的）> local_config.py

生效时机：
  - ADMIN_PASSWORD：保存时 setattr 热更新 config 模块属性，**立即生效**
    （鉴权每次 getattr 现读）；
  - 其余密钥：程序启动时读一次、各模块 import 快照，**保存后需重启服务生效**。

安全：
  - 敏感值掩码回显（get_status 只给前 6…后 4），浏览器历史/截图不泄密；
  - 保存接口仅接受白名单内 key，未知 key 静默忽略；
  - 原子写（tmp + os.replace），写失败不破坏旧值。
"""

import json
import logging
import os
import threading

logger = logging.getLogger("admin_config")

# ===== 配置项白名单（分类对齐 /admin「系统设置」页面） =====

CONFIG_ITEMS: dict[str, dict] = {
    # ── 🧠 大语言模型（聊天 / Agent） ──
    "LLM_MODEL": {
        "group": "llm", "label": "大语言模型名",
        "secret": False,
        "hint": (
            "DeepSeek 三选一：flash（快·纯文本）/ pro（旗舰·纯文本）/ "
            "flash-vision-exp（视觉·能看图）。选 vision 时识图自动复用主模型，"
            "不走下面的识图模型。重启生效"
        ),
    },
    "DEEPSEEK_API_KEY": {
        "group": "llm", "label": "大语言模型 API Key",
        "secret": True, "hint": "DeepSeek 开放平台 https://platform.deepseek.com",
    },
    # ── 📨 钉钉机器人 ──
    "DINGTALK_CLIENT_ID": {
        "group": "dingtalk", "label": "钉钉 Client ID",
        "secret": True, "hint": "钉钉开发者后台，Stream 机器人凭证",
    },
    "DINGTALK_CLIENT_SECRET": {
        "group": "dingtalk", "label": "钉钉 Client Secret",
        "secret": True, "hint": "钉钉开发者后台，Stream 机器人凭证",
    },
    # ── 🔑 管理后台密码 ──
    "ADMIN_PASSWORD": {
        "group": "admin", "label": "管理后台密码",
        "secret": True, "hint": "保存后立即生效（需用新密码重新登录）",
        "immediate": True,
    },
    # ── 📄 MinerU（扫描 PDF 识别） ──
    "MINERU_TOKEN": {
        "group": "mineru", "label": "MinerU API Token",
        "secret": True, "hint": "https://mineru.net/apiManage",
    },
    # ── 👤 通讯录敏感字段 ──
    "CONTACT_ADMIN_STAFF_IDS": {
        "group": "contact", "label": "通讯录敏感字段审核人 staff_id",
        "secret": False, "hint": "留空 = 无人可查联系方式（一般默认不填）",
    },
    # ── 🖼️ 识图模型（阿里云百炼千问） ──
    "DASHSCOPE_API_KEY": {
        "group": "vision", "label": "识图 API Key",
        "secret": True,
        "hint": "阿里云百炼 https://bailian.console.aliyun.com/；仅主对话未选 DeepSeek vision 时用，选了可留空",
    },
    "VISION_MODEL": {
        "group": "vision", "label": "识图模型名",
        "secret": False,
        "hint": "默认 qwen3.7-flash；仅主对话未选 DeepSeek vision 时用（选了则识图自动复用主模型）",
    },
}

# 页面分组展示顺序与标题
GROUPS: dict[str, dict] = {
    "llm":      {"icon": "🧠", "title": "大语言模型（聊天 / Agent）"},
    "dingtalk": {"icon": "📨", "title": "钉钉机器人"},
    "admin":    {"icon": "🔑", "title": "管理后台密码"},
    "mineru":   {"icon": "📄", "title": "MinerU（扫描 PDF 识别）"},
    "contact":  {"icon": "👤", "title": "通讯录敏感字段"},
    "vision":   {"icon": "🖼️", "title": "识图模型（阿里云百炼千问）"},
}


def overrides_path() -> str:
    """覆盖配置文件路径（运行时数据，gitignore）。

    用 RUNTIME_DIR 而非 DATA_ROOT：测试进程运行时落在 data/_test_runtime/
    下，与生产 data/admin_config.json 隔离（paths.py 的运行时隔离约定）。
    """
    from scripts.paths import RUNTIME_DIR
    return os.path.join(str(RUNTIME_DIR), "admin_config.json")


_lock = threading.Lock()


def load_overrides() -> dict:
    """读取现有覆盖配置（网页已保存的值）。"""
    try:
        with open(overrides_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.warning(f"读取 admin_config.json 出错: {e}")
        return {}


def _write_overrides(overrides: dict) -> None:
    """原子写覆盖配置（tmp + os.replace），写失败不破坏旧值。"""
    p = overrides_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = f"{p}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(overrides, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def mask_value(value: str) -> str:
    """敏感值掩码：前 6 … 后 4；过短只显示「已设置」。"""
    if not value:
        return ""
    if len(value) <= 12:
        return "已设置"
    return f"{value[:6]}……{value[-4:]}"


def save_config(values: dict) -> dict:
    """保存配置覆盖。

    Args:
        values: {配置项 key: 值}。空字符串 = 不修改该项；未知 key 静默忽略。

    Returns:
        {"updated": [已更新的 key], "immediate": [立即生效的 key]}
    """
    overrides = load_overrides()
    updated: list[str] = []
    immediate: list[str] = []

    # 延迟导入避免循环依赖（admin_config ← config）
    import scripts.config as config_module

    for key, raw in values.items():
        if key not in CONFIG_ITEMS:
            continue  # 白名单校验
        value = (raw or "").strip()
        if value == "":
            continue  # 留空 = 保留原值
        overrides[key] = value
        updated.append(key)
        # 热更新 config 模块属性（尽力而为）：对每次现读的项生效
        # （ADMIN_PASSWORD 鉴权；其余模块 import 快照不受影响，需重启）
        if hasattr(config_module, key):
            setattr(config_module, key, value)
        if CONFIG_ITEMS[key].get("immediate"):
            immediate.append(key)

    if updated:
        _write_overrides(overrides)
        logger.info(f"[配置] 管理员保存配置项: {updated}（立即生效: {immediate}）")

    return {"updated": updated, "immediate": immediate}


def get_status() -> dict:
    """返回各配置项当前状态（掩码回显），供 /admin「系统设置」页渲染。"""
    import scripts.config as config_module

    result: dict = {}
    for key, meta in CONFIG_ITEMS.items():
        val = getattr(config_module, key, "") or ""
        result[key] = {
            "key": key,
            "group": meta["group"],
            "label": meta["label"],
            "secret": meta["secret"],
            "hint": meta.get("hint", ""),
            "immediate": meta.get("immediate", False),
            "set": bool(val),
            "masked": mask_value(val) if meta["secret"] else val,
        }
    return result
