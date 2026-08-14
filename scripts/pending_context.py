"""统一 pending 确认上下文（v1.12.1，M3 pending 归一）

单一事实源：tools 写操作 / knowledge_review 文件入库 / dashboard 看板订阅 /
routing 操作歧义澄清——四套独立 pending 字典统一收口到这里，按 user_id 存一张表，
type 区分（tool/learn/kanban/clarify）。确认词按 pending 类型作用域化：
「入库」只在 learn 类型生效，避免全局合并确认词误确认 tool pending。

不 import 内部模块（防循环依赖）。四模块对外 API 保留为薄封装（见 B2）。
"""

import logging
import re
import threading
import time

logger = logging.getLogger(__name__)

# pending 类型常量（四模块共用）
PT_TOOL = "tool"       # 工具写操作（tools/__init__.py）
PT_LEARN = "learn"     # 上传后推荐入库（knowledge_review.py）
PT_KANBAN = "kanban"   # 看板订阅管理（dashboard/subscription_commands.py，技能层处理）
PT_CLARIFY = "clarify" # 操作歧义澄清（routing.py）

_DEFAULT_TTL = 600.0   # 10 分钟，与 tools._PENDING_TTL / routing._CLARIFY_TIMEOUT 对齐

# user_id -> {"type", "payload", "ts", "ttl"}；单用户同时只允许一个 pending，
# 跨类型新 set 覆盖旧 pending（logger.info 记录，见 _B4 行为变化说明）
_pending: dict[str, dict] = {}
_lock = threading.RLock()


def set(user_id: str, ptype: str, payload: dict, ttl: float = _DEFAULT_TTL) -> None:
    """登记 pending。跨类型覆盖旧 pending 时记录（便于排查覆盖链路）。"""
    with _lock:
        old = _pending.get(user_id)
        if old and old["type"] != ptype:
            logger.info("pending 覆盖: user=%s type %s → %s", user_id, old["type"], ptype)
        _pending[user_id] = {
            "type": ptype,
            "payload": dict(payload or {}),
            "ts": time.time(),
            "ttl": ttl,
        }


def get(user_id: str) -> dict | None:
    """取未过期 pending；过期惰性清除。返回 {"type", "payload"} 或 None。"""
    with _lock:
        entry = _pending.get(user_id)
        if not entry:
            return None
        if time.time() - entry["ts"] > entry["ttl"]:
            _pending.pop(user_id, None)
            return None
        return {"type": entry["type"], "payload": dict(entry["payload"])}


def has(user_id: str) -> bool:
    return get(user_id) is not None


def touch(user_id: str) -> None:
    """刷新 pending 时间戳（不改 payload；活动续期用）"""
    with _lock:
        entry = _pending.get(user_id)
        if entry:
            entry["ts"] = time.time()


def clear(user_id: str) -> None:
    """无条件清除（bot 统一取消用）"""
    with _lock:
        _pending.pop(user_id, None)


def clear_type(user_id: str, ptype: str) -> bool:
    """仅当当前 pending 是 ptype 才清除（模块清仓守卫，防跨类型误清）。

    例：learn 清仓时若 pending 已被 tool 覆盖，不得清掉 tool 的确认。
    """
    with _lock:
        entry = _pending.get(user_id)
        if entry and entry["type"] == ptype:
            _pending.pop(user_id, None)
            return True
        return False


def reset() -> None:
    """清空全部（测试用）"""
    with _lock:
        _pending.clear()


# ===== 确认词（类型作用域） =====
# 取消词：取消/算了/不要了/不执行/先不弄了（fullmatch 防误拦普通聊天）
_CANCEL_TEXT_RE = re.compile(r"^(?:取消|算了|不要了|不执行|先不弄了)[。！!]?$")

# 通用确认词（tool/kanban/clarify 共用的唯一确认词表）。
# v1.12.4：补自然口语确认词——「对的」是最高频确认（实测袁会荧回「对的」落
# Agent 被 LLM 谎称「已开通」），「对(?:的|呀|啊)?」覆盖 对/对的/对呀/对啊，
# 「是(?:的)?(?:呀|啊)?」覆盖 是/是的/是的呀/是呀，「好(?:的)?(?:呀|啊)?」
# 覆盖 好/好的/好呀/好啊，「可以(?:的)?(?:呀)?」覆盖 可以/可以的/可以呀。
# v1.12.5：动作后缀补「推送/发送」——dash_push 写操作确认被 LLM 反复要求
# 「再确认」死循环（实测袁会荧回「确认推送」3 次，推送始终未执行），根因是
# 「确认推送」= 主确认词「确认」+ 动作「推送」，「推送/发送」不在后缀枚举里，
# is_confirm_text 返回 False → PT_TOOL 确认路由不拦截 → 落 Agent 又调工具覆盖
# pending。补上后「确认推送」「好，推送」「执行推送」「确认发送」均被识别。
_CONFIRM_TEXT_RE = re.compile(
    r"^(?:是(?:的)?(?:呀|啊)?|对(?:的|呀|啊)?|好(?:的)?(?:呀|啊)?|"
    r"可以(?:的)?(?:呀)?|没问题|确认|确定|就这样|就这么办|执行|继续)"
    r"(?:[,， ]*(?:确认|执行|继续|推送|发送|删除|全部删除|都删除|删除全部|删掉全部))?[。！!]?$")

# learn 专属补充确认词（v1.11.0「入库」既是命令也是确认词——有 learn pending 时
# 确认识别，无 pending 时落「帮我学习」命令兜底，学习能力不丢）
_LEARN_EXTRA_CONFIRM_RE = re.compile(r"^(?:入库|入库吧|要)[。！!]?$")


def is_cancel_text(text: str) -> bool:
    """是否取消词（任何 pending 类型通用）"""
    return bool(_CANCEL_TEXT_RE.fullmatch((text or "").strip()))


def is_confirm_text(text: str, ptype: str) -> bool:
    """是否确认词；类型作用域——「入库/入库吧/要」只在 learn 类型生效。"""
    t = (text or "").strip()
    if _CONFIRM_TEXT_RE.fullmatch(t):
        return True
    if ptype == PT_LEARN and _LEARN_EXTRA_CONFIRM_RE.fullmatch(t):
        return True
    return False
