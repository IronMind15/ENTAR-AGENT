"""
恩特小助手 — 工具注册中心

每个工具是一个独立模块，导出：
    DEFINITION: dict   — 工具定义（OpenAI 格式，用于 LLM function calling）
    execute(args: dict) → str  — 工具执行函数，返回 JSON 字符串

新增工具只需：
    1. 在 tools/ 下新建 .py 文件
    2. 定义 DEFINITION + execute(args) 函数
    3. 用 @register 装饰 execute，或在底部 from . import 新模块

路由开销约 60~150 纳秒（Python dict 哈希查找），相比 LLM API 调用（秒级）可忽略。
"""

import json
import logging
import threading
import time
from typing import Any, Callable

logger = logging.getLogger("tools")

# ===== 用户上下文（Agent 执行前注入，工具读取） =====
# 使用 contextvars 保证异步安全，每次 Agent 请求独立
import contextvars
_current_user_centers: contextvars.ContextVar[list[str] | None] = \
    contextvars.ContextVar("tool_user_centers", default=None)


def set_user_centers(centers: list[str] | None) -> None:
    """设置当前请求的用户归属中心列表（Agent 入口调用）"""
    _current_user_centers.set(centers)


def get_user_centers() -> list[str] | None:
    """获取当前请求的用户归属中心列表（工具执行时调用）"""
    return _current_user_centers.get()


# 当前请求发起者的钉钉员工 ID（staff_id），用于敏感字段（手机号/邮箱）权限判断。
# 钉钉入口在 _process_text 中设置；Web 入口不设置 → 默认空串 → 非审核人 → 只返回基础信息。
_current_staff_id: contextvars.ContextVar[str] = \
    contextvars.ContextVar("tool_staff_id", default="")


def set_current_staff_id(staff_id: str) -> None:
    """设置当前请求发起者的钉钉员工 ID（入口调用）"""
    _current_staff_id.set(staff_id or "")


def get_current_staff_id() -> str:
    """获取当前请求发起者的钉钉员工 ID（工具执行时调用）"""
    return _current_staff_id.get()


# 当前请求发起者的用户 ID（user_id，文档候选表 owner 键，v1.11.3）。
# 与 staff_id 不同：user_id 是内部会话用户标识（memory/candidate 主键）。
_current_user_id: contextvars.ContextVar[str] = \
    contextvars.ContextVar("tool_user_id", default="")


def set_current_user_id(user_id: str) -> None:
    """设置当前请求发起者的用户 ID（入口调用）"""
    _current_user_id.set(user_id or "")


def get_current_user_id() -> str:
    """获取当前请求发起者的用户 ID（工具执行时调用）"""
    return _current_user_id.get()


# ===== 注册中心 =====
# {(name, definition, handler)}
_tool_registry: dict[str, tuple[dict, Callable[[dict], str], dict]] = {}
_pending_operations: dict[str, dict] = {}
_pending_lock = threading.RLock()
_PENDING_TTL = 10 * 60


def register(name: str, definition: dict, policy: dict | None = None) -> Callable:
    """装饰器：注册工具

    Args:
        name: 工具名称（与 LLM function calling 的 name 一致）
        definition: 工具定义 dict（不含外层 {"type":"function","function":...} 壳）

    用法:
        @register("search_foo", {"name": "search_foo", "description": "...", "parameters": {...}})
        def execute(args: dict) -> str:
            ...
    """
    def wrapper(func: Callable[[dict], str]) -> Callable:
        if name in _tool_registry:
            logger.warning(f"工具 [{name}] 重复注册，覆盖旧定义")
        _tool_registry[name] = (definition, func, dict(policy or {}))
        return func
    return wrapper


def get_tool_definitions() -> list[dict]:
    """获取所有工具定义列表（供 DeepSeek API 的 tools 参数使用）"""
    return [
        {"type": "function", "function": defn}
        for defn, _, _ in _tool_registry.values()
    ]


def get_tool_names() -> list[str]:
    """获取所有已注册工具名称"""
    return list(_tool_registry.keys())


def execute_tool(name: str, args: dict) -> str:
    """执行工具调用，返回 JSON 字符串结果

    Args:
        name: 工具名称
        args: 参数字典

    Returns:
        JSON 字符串，统一包含结果字段
    """
    entry = _tool_registry.get(name)
    if not entry:
        logger.warning(f"未知工具调用: {name}")
        return json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False)
    definition, handler, policy = entry

    require = policy.get("confirm", False)
    if callable(require):
        require = bool(require(args))
    user_id = get_current_user_id()
    if require:
        if not user_id:
            return json.dumps({"error": "该操作需要用户身份和二次确认，当前无法执行"},
                              ensure_ascii=False)
        with _pending_lock:
            _pending_operations[user_id] = {
                "tool": name, "args": dict(args or {}), "created_at": time.time(),
                "summary": policy.get("summary") or definition.get("description", name),
                "risk": policy.get("risk", "write"),
            }
        return json.dumps({
            "confirmation_required": True,
            "operation": name,
            "summary": _pending_operations[user_id]["summary"],
            "arguments": args,
            "message": "操作尚未执行。请向用户清楚说明目标和影响，并要求再次确认。",
        }, ensure_ascii=False)

    try:
        return handler(args)
    except Exception as e:
        logger.exception(f"工具 [{name}] 执行异常: {e}")
        return json.dumps({"error": f"工具执行失败: {e}"}, ensure_ascii=False)


def get_pending_operation(user_id: str) -> dict | None:
    with _pending_lock:
        pending = _pending_operations.get(user_id)
        if pending and time.time() - pending["created_at"] <= _PENDING_TTL:
            return dict(pending)
        _pending_operations.pop(user_id, None)
    return None


def cancel_pending_operation(user_id: str) -> bool:
    with _pending_lock:
        return _pending_operations.pop(user_id, None) is not None


def confirm_pending_operation(user_id: str) -> str:
    """确认后执行一次真实 handler；只有得到 handler 结果才允许回报成功。"""
    pending = get_pending_operation(user_id)
    if not pending:
        return json.dumps({"error": "没有待确认或待确认操作已过期"}, ensure_ascii=False)
    entry = _tool_registry.get(pending["tool"])
    if not entry:
        cancel_pending_operation(user_id)
        return json.dumps({"error": "待确认工具已不可用"}, ensure_ascii=False)
    _, handler, _ = entry
    try:
        result = handler(pending["args"])
    except Exception as exc:
        logger.exception("确认工具 [%s] 执行异常: %s", pending["tool"], exc)
        result = json.dumps({"error": f"工具执行失败: {exc}"}, ensure_ascii=False)
    cancel_pending_operation(user_id)
    return result


# ===== 自动导入工具模块（确保 @register 装饰器执行） =====
# 注：search_standards / search_experience_kb 两个旧查询工具 v1.11.5 起不再注册，
# 统一由 search_knowledge_base（通用查询，可按知识库选择）替代，文件保留作参考。
from . import search_knowledge_base  # noqa: E402, F811 — 通用知识库查询（v1.11.5 多库）
from . import create_knowledge_base  # noqa: E402, F811 — 创建知识库（v1.11.5 多库）
from . import calc_pcb_trace         # noqa: E402, F811 — PCB 走线计算（IPC-2221）
from . import calc_copper_busbar     # noqa: E402, F811 — 铜排/母线载流（v1.6.0）
from . import find_employee          # noqa: E402, F811 — 钉钉通讯录员工查询（v1.7.0）
from . import describe_image         # noqa: E402, F811 — 图片识别（千问视觉，v1.10.0）
from . import query_dashboard        # noqa: E402, F811 — 看板实时查询（v1.11.0）
from . import push_dashboard         # noqa: E402, F811 — 看板主动推送（v1.11.0）
from . import summarize_doc          # noqa: E402, F811 — 钉钉文档总结（v1.11.3）
from . import manage_uploaded_file   # noqa: E402, F811 — 文件删除/重学（统一二次确认）
