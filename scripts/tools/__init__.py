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


# ===== 注册中心 =====
# {(name, definition, handler)}
_tool_registry: dict[str, tuple[dict, Callable[[dict], str]]] = {}


def register(name: str, definition: dict) -> Callable:
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
        _tool_registry[name] = (definition, func)
        return func
    return wrapper


def get_tool_definitions() -> list[dict]:
    """获取所有工具定义列表（供 DeepSeek API 的 tools 参数使用）"""
    return [
        {"type": "function", "function": defn}
        for defn, _ in _tool_registry.values()
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
    _, handler = entry

    try:
        return handler(args)
    except Exception as e:
        logger.exception(f"工具 [{name}] 执行异常: {e}")
        return json.dumps({"error": f"工具执行失败: {e}"}, ensure_ascii=False)


# ===== 自动导入工具模块（确保 @register 装饰器执行） =====
from . import search_knowledge_base  # noqa: E402, F811
from . import search_standards       # noqa: E402, F811
from . import calc_pcb_trace         # noqa: E402, F811 — PCB 走线计算（IPC-2221）
