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


# ===== 注册中心（v1.12.0：工具唯一事实源） =====
# 值：6 元组 (definition, handler, policy, sector, user_desc, display)
_tool_registry: dict[str, tuple[dict, Callable[[dict], str], dict, str, str, str]] = {}

# 板块元信息（render_tool_prompt / 能力清单用）
SECTOR_ORDER = ("kb", "calc", "dash", "contact", "image", "doc")
SECTOR_LABELS = {
    "kb": "📚 知识库",
    "calc": "🧮 计算",
    "dash": "📊 项目看板",
    "contact": "👥 通讯录",
    "image": "🖼️ 图片",
    "doc": "📄 文档",
}

# system prompt 工具段标记：agent._normalize_prompt_base 用它切掉旧工具段，
# 保证「工具段永远由注册中心生成」（v1.12.0）
_TOOL_SECTION_MARKER = "===== 工具能力（由注册中心自动生成，勿手动编辑）====="


def register(definition: dict, policy: dict | None = None, *,
             sector: str = "", user_desc: str = "", display: str = "") -> Callable:
    """装饰器：注册工具（v1.12.0：name 只从 definition["name"] 读取）

    Args:
        definition: 工具定义 dict（须含 "name"；不含外层 {"type":"function",...} 壳）
        policy: 操作治理策略 {confirm, risk, summary}
        sector: 板块 key（见 SECTOR_LABELS，如 "kb"/"calc"）
        user_desc: 给 LLM 看的工具说明（多行文本，render_tool_prompt 生成工具段）
        display: 流式/展示名（如 "🔍 搜索知识库..."，agent 流式显示映射）

    用法（v1.12.0）:
        @register(DEFINITION, sector="kb", display="🔍 搜索知识库...",
                  user_desc="...")
        def execute(args: dict) -> str:
            ...
    """
    if isinstance(definition, str):
        raise TypeError(
            "register 旧签名已废弃：请改 @register(DEFINITION, policy=..., "
            "sector=..., display=..., user_desc=...)，"
            "name 只从 definition['name'] 读取。")
    name = (definition or {}).get("name") or ""
    if not name:
        raise ValueError("register 要求 definition 包含 'name' 字段")

    def wrapper(func: Callable[[dict], str]) -> Callable:
        if name in _tool_registry:
            logger.warning(f"工具 [{name}] 重复注册，覆盖旧定义")
        _tool_registry[name] = (definition, func, dict(policy or {}),
                                sector, user_desc or "", display or "")
        return func
    return wrapper


def get_tool_definitions() -> list[dict]:
    """获取所有工具定义列表（供 DeepSeek API 的 tools 参数使用）"""
    return [
        {"type": "function", "function": defn}
        for defn, *_ in _tool_registry.values()
    ]


def get_tool_registry() -> dict:
    """只读工具注册表视图（v1.12.0，供能力清单/元数据消费）"""
    return dict(_tool_registry)


def get_tool_display_map() -> dict[str, str]:
    """流式显示映射（v1.12.0：由注册中心生成，agent 不再手写 _TOOL_DISPLAY）"""
    return {name: (entry[5] or f"🔍 {name}...")
            for name, entry in _tool_registry.items()}


def get_tool_metadata() -> list[dict]:
    """全部工具的结构化元数据（v1.12.0，供 render_tool_prompt / 能力清单）"""
    return [
        {
            "name": name,
            "sector": entry[3],
            "policy": entry[2],
            "confirm": bool((entry[2] or {}).get("confirm")),
            "description": (entry[0] or {}).get("description", ""),
            "display": entry[5],
            "user_desc": entry[4],
        }
        for name, entry in _tool_registry.items()
    ]


def render_tool_prompt() -> str:
    """生成 LLM system prompt 的工具段（v1.12.0：由注册中心自动生成）。

    按 SECTOR_ORDER + 工具名排序确定性输出（防前缀缓存抖动）；
    confirm 工具加「需二次确认」提示行。
    """
    by_sector: dict[str, list[dict]] = {}
    for meta in get_tool_metadata():
        by_sector.setdefault(meta["sector"] or "_", []).append(meta)

    lines = [_TOOL_SECTION_MARKER]
    for sector in SECTOR_ORDER:
        metas = by_sector.get(sector)
        if not metas:
            continue
        metas.sort(key=lambda m: m["name"])
        lines.append(f"【{SECTOR_LABELS.get(sector, sector)}】")
        for i, meta in enumerate(metas, 1):
            head = f"{i}. {meta['name']}"
            if meta["display"]:
                head += f"（{meta['display']}）"
            if meta["confirm"]:
                head += "（写操作：执行前须用户确认）"
            lines.append(head)
            for line in (meta["user_desc"] or "").splitlines():
                line = line.strip()
                if line:
                    lines.append(f"   - {line}")
    return "\n".join(lines)


def get_tool_names() -> list[str]:
    """获取所有已注册工具名称"""
    return list(_tool_registry.keys())


def is_write_tool(name: str) -> bool:
    """工具是否写操作（policy 含 confirm → 执行前须用户二次确认）

    v1.12.6（B4 幻觉护栏）：区分只读/写操作工具。只读工具（kb_search、
    contact_find、dash_query 等）的调用**不能**作为「已执行写操作」的依据——
    护栏据此判定「只调了只读工具却声称完成写操作」仍是幻觉。
    """
    entry = _tool_registry.get(name)
    if entry is None:
        return False
    return bool((entry[2] or {}).get("confirm"))


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
    definition, handler, policy, *_ = entry

    require = policy.get("confirm", False)
    if callable(require):
        require = bool(require(args))
    user_id = get_current_user_id()
    if require:
        if not user_id:
            return json.dumps({"error": "该操作需要用户身份和二次确认，当前无法执行"},
                              ensure_ascii=False)
        # v1.12.1（M3）：pending 统一收口到 pending_context（type=tool）
        from pending_context import PT_TOOL, set as pc_set
        payload = {
            "tool": name, "args": dict(args or {}),
            "summary": policy.get("summary") or definition.get("description", name),
            "risk": policy.get("risk", "write"),
        }
        pc_set(user_id, PT_TOOL, payload)
        return json.dumps({
            "confirmation_required": True,
            "operation": name,
            "summary": payload["summary"],
            "arguments": args,
            "message": "操作尚未执行。请向用户清楚说明目标和影响，并要求再次确认。",
        }, ensure_ascii=False)

    try:
        return handler(args)
    except Exception as e:
        logger.exception(f"工具 [{name}] 执行异常: {e}")
        return json.dumps({"error": f"工具执行失败: {e}"}, ensure_ascii=False)


def get_pending_operation(user_id: str) -> dict | None:
    """取未过期工具 pending（type=tool 才返回 payload）"""
    from pending_context import PT_TOOL, get as pc_get
    entry = pc_get(user_id)
    if entry and entry["type"] == PT_TOOL:
        return dict(entry["payload"])
    return None


def cancel_pending_operation(user_id: str) -> bool:
    """取消工具 pending（仅当当前还是 tool 类型才清）"""
    from pending_context import PT_TOOL, clear_type
    return clear_type(user_id, PT_TOOL)


def confirm_pending_operation(user_id: str) -> str:
    """确认后执行一次真实 handler；只有得到 handler 结果才允许回报成功。"""
    pending = get_pending_operation(user_id)
    if not pending:
        return json.dumps({"error": "没有待确认或待确认操作已过期"}, ensure_ascii=False)
    entry = _tool_registry.get(pending["tool"])
    if not entry:
        cancel_pending_operation(user_id)
        return json.dumps({"error": "待确认工具已不可用"}, ensure_ascii=False)
    _, handler, *_ = entry
    try:
        result = handler(pending["args"])
    except Exception as exc:
        logger.exception("确认工具 [%s] 执行异常: %s", pending["tool"], exc)
        result = json.dumps({"error": f"工具执行失败: {exc}"}, ensure_ascii=False)
    cancel_pending_operation(user_id)
    return result


# ===== 自动导入工具模块（确保 @register 装饰器执行） =====
# 注：search_standards / search_experience_kb 两个旧查询工具 v1.11.5 起不再注册，
# 统一由 kb_search（通用查询，可按知识库选择）替代，文件保留作参考。
# 注：v1.12.0 工具名带板块前缀（kb/calc/dash/contact/image/doc），文件随名 rename。
from . import kb_search          # noqa: E402, F811 — 通用知识库查询（v1.11.5 多库，v1.12.0 改名）
from . import kb_create          # noqa: E402, F811 — 创建知识库（v1.11.5 多库，v1.12.0 改名）
from . import calc_pcb_trace     # noqa: E402, F811 — PCB 走线计算（IPC-2221）
from . import calc_copper_busbar # noqa: E402, F811 — 铜排/母线载流（v1.6.0）
from . import contact_find       # noqa: E402, F811 — 钉钉通讯录员工查询（v1.7.0，v1.12.0 改名）
from . import image_describe     # noqa: E402, F811 — 图片识别（千问视觉，v1.10.0，v1.12.0 改名）
from . import dash_query         # noqa: E402, F811 — 看板实时查询（v1.11.0，v1.12.0 改名）
from . import dash_push          # noqa: E402, F811 — 看板主动推送（v1.11.0，v1.12.0 改名）
from . import doc_summarize      # noqa: E402, F811 — 钉钉文档总结（v1.11.3，v1.12.0 改名）
from . import kb_file_manage     # noqa: E402, F811 — 文件删除/重学（统一二次确认，v1.12.0 改名）
