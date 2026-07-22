"""
RAG Agent 技能 — 大模型 + 知识库搜索工具

当问题与故障相关时，LLM 主动调用 search_knowledge_base 工具检索本地知识库，
结合检索结果进行推理和回答。非故障问题直接聊天，无需搜索。

使用 DeepSeek API 的 function calling（tools 参数）实现原生工具调用。
V4 模型仅支持 tool_choice="auto"，由 LLM 自行判断是否调用工具。
"""

import json
import logging
import os
import sys

_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

import httpx

from config import DEEPSEEK_API_KEY
from skills import BaseSkill, register
from tools import get_tool_definitions, execute_tool as _execute_registered_tool

logger = logging.getLogger("agent")

# 共享 HTTP 客户端（复用连接，避免每次建新连接）
_HTTP_CLIENT = httpx.Client(timeout=60)

# ===== 工具定义（从注册中心自动获取，新增工具无需改本文件） =====
TOOLS = get_tool_definitions()

# ===== System Prompt（从文件加载） =====

_PROMPT_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts", "system_prompt.txt")


def _load_system_prompt() -> str:
    """从文件加载 system prompt"""
    try:
        with open(_PROMPT_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        logger.warning(f"System prompt 文件不存在: {_PROMPT_FILE}，使用默认提示词")
        return "你是恩特小助手，恩特能源内部使用的 AI 助手。"


SYSTEM_PROMPT = _load_system_prompt()


def _call_deepseek(
    messages: list[dict],
    tools: list | None = None,
    max_tokens: int = 8000,
) -> dict | None:
    """调用 DeepSeek API（通用封装）

    Args:
        messages: 消息列表（OpenAI 格式）
        tools: 工具定义列表（可选）
        max_tokens: 最大输出 token 数

    Returns:
        response message dict，或 None（调用失败时）
    """
    if not DEEPSEEK_API_KEY:
        logger.warning("DEEPSEEK_API_KEY 未配置")
        return None

    body = {
        "model": "deepseek-v4-flash",
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": max_tokens,
        "thinking": {"type": "enabled"},
    }
    # 注意：thinking mode 启用后 temperature/top_p 等采样参数自动失效
    if tools:
        body["tools"] = tools
        # V4 模型 thinking mode 仅支持 tool_choice="auto"
        body["tool_choice"] = "auto"

    try:
        r = _HTTP_CLIENT.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
            json=body,
        )
        if r.status_code == 200:
            data = r.json()
            if data.get("choices"):
                return data["choices"][0]["message"]
            logger.warning("DeepSeek 返回空 choices")
            return None
        else:
            logger.warning(f"DeepSeek API 返回 {r.status_code}: {r.text[:200]}")
            return None
    except httpx.TimeoutException:
        logger.warning("DeepSeek API 超时")
        return None
    except httpx.RequestError as e:
        logger.warning(f"DeepSeek API 请求失败: {e}")
        return None
    except Exception as e:
        logger.warning(f"DeepSeek API 未知错误: {e}")
        return None


def _execute_tool(tool_call: dict) -> str:
    """执行工具调用，返回 JSON 字符串结果

    通过工具注册中心路由到对应的处理函数，无需手动 if-elif 分派。
    新增工具只需在 tools/ 下新建模块并用 @register 注册，无需改本文件。

    Args:
        tool_call: API 返回的 tool_call 对象
            { "id": "...", "type": "function", "function": { "name": "...", "arguments": "..." } }

    Returns:
        JSON 字符串，包含工具执行结果
    """
    fn_name = tool_call.get("function", {}).get("name", "")
    try:
        args = json.loads(tool_call.get("function", {}).get("arguments", "{}"))
    except json.JSONDecodeError:
        logger.warning(f"工具参数解析失败: {tool_call.get('function', {}).get('arguments', '')}")
        return json.dumps({"error": "工具参数解析失败，请重试"}, ensure_ascii=False)

    return _execute_registered_tool(fn_name, args)


def _handle_impl(query: str, user_id: str = "") -> dict:
    """RAG Agent 处理入口

    三级策略：
      1. 快速通道：精确匹配故障代码（d4-1 等）→ 直接返回格式化结果，不走 LLM
      2. 快速通道：精确匹配标准编号（GB/T 34133 等）→ 直接返回，不走 LLM
      3. Agent 通道：LLM + 知识库工具 → 智能 RAG 回答或直接聊天

    记忆融合：
      - 有 user_id 时，主动注入该用户最近对话记录（5轮）作为上下文
      - 实现跨轮对话连贯性（如先问故障再追问处理方法）

    Args:
        query: 用户问题
        user_id: 用户标识（可选，用于注入记忆上下文）

    Returns:
        {"answer": str, "source": str}
    """
    q = query.strip()
    if not q:
        return {"answer": "请输入问题", "source": "agent"}

    # ===== 第 1 关：快速通道（精确故障代码，毫秒级） =====
    from skills.error_query import extract_fault_code, _exact_match_by_code, format_exact_result

    code = extract_fault_code(q)
    if code:
        match = _exact_match_by_code(code)
        if match:
            answer = format_exact_result(match["metadata"])
            row = match["metadata"].get("row_num", "")
            return {"answer": answer, "source": f"遥信（DI）表 第{row}行"}

    # ===== 第 1.5 关：快速通道（精确标准编号，毫秒级） =====
    from skills.standards_query import extract_standard_id, exact_match_by_std_id, format_exact_result as format_std_exact_result

    std_id = extract_standard_id(q)
    if std_id:
        match = exact_match_by_std_id(std_id)
        if match:
            answer = format_std_exact_result(match["metadata"])
            return {"answer": answer, "source": f"标准编号快速匹配"}

    # ===== 第 2 关：Agent 通道（LLM + 工具调用） =====
    if not DEEPSEEK_API_KEY:
        logger.warning("DEEPSEEK_API_KEY 未配置，Agent 不可用")
        return {"answer": "DeepSeek API 未配置，无法处理此问题。请先在 local_config.py 中设置 DEEPSEEK_API_KEY。", "source": "agent"}

    # ---- 查询当前用户信息，注入认知上下文 ----
    user_info_lines = []
    user_centers = None
    if user_id:
        try:
            from user_store import get_store
            store = get_store()
            user = store.get_user(user_id)
            if user:
                # 姓名
                nick = user.get("nick") or user.get("nickname", "")
                if nick:
                    user_info_lines.append(f"当前用户：{nick}")
                # 职务
                title = user.get("title", "")
                if title:
                    user_info_lines.append(f"职务：{title}")
                # 钉钉部门名（JSON 数组字符串 → 中文列表）
                dept_raw = user.get("department_names", "")
                if dept_raw:
                    try:
                        dept_list = json.loads(dept_raw) if isinstance(dept_raw, str) else dept_raw
                        if isinstance(dept_list, list) and dept_list:
                            user_info_lines.append(f"钉钉部门：{'、'.join(dept_list)}")
                    except (json.JSONDecodeError, TypeError):
                        if str(dept_raw).strip() and str(dept_raw) not in ("[]", ""):
                            user_info_lines.append(f"钉钉部门：{dept_raw}")
                # 归属中心
                raw_centers = user.get("centers", "[]")
                try:
                    centers = json.loads(raw_centers) if isinstance(raw_centers, str) else list(raw_centers)
                    user_centers = [c for c in centers if c] if isinstance(centers, list) else None
                except (json.JSONDecodeError, TypeError):
                    user_centers = None
                if user_centers:
                    from center_config import get_center_name
                    center_names = [get_center_name(c) for c in user_centers]
                    user_info_lines.append(f"归属中心：{'、'.join(center_names)}")
                    # 设置工具上下文，实现按中心隔离查询
                    from tools import set_user_centers
                    set_user_centers(user_centers)
        except Exception as e:
            logger.warning(f"获取用户信息失败（不影响主流程）: {e}")

    # 构建系统提示词，注入用户认知 + 记忆上下文
    system_content = SYSTEM_PROMPT

    # 注入当前用户信息（让助手知道在跟谁说话）
    if user_info_lines:
        system_content += (
            "\n\n【当前用户信息】\n"
            + "\n".join(user_info_lines)
            + "\n（用户问及身份、部门、中心时可参考上述信息回答；知识库查询时自动按归属中心过滤）"
        )
        logger.info(f"已注入用户档案 ({len(user_info_lines)} 条)")

    # 注入最近对话记忆
    context = ""
    if user_id:
        try:
            from skills import memory
            context = memory.format_context(user_id, max_content=500)
            if context:
                system_content += (
                    "\n\n【最近对话记录 - 请仔细参考】\n"
                    "以下是你与当前用户最近的对话历史，用户的追问通常基于上文，请务必结合历史来理解当前问题：\n"
                    + context
                )
                logger.info(f"已注入 {user_id} 的记忆上下文 ({len(context)}字)")
        except Exception as e:
            logger.warning(f"注入记忆失败: {e}")

    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": q},
    ]

    logger.info(f"Agent 处理: {q[:80]}" + (f" [{user_id}]" if user_id else ""))

    # Agent 循环：允许 LLM 多次调用工具（搜不到自动降级到钉钉知识库）
    MAX_AGENT_LOOPS = 5  # 安全上限，防止死循环
    final_answer = ""
    source_tag = "agent"

    for loop_i in range(MAX_AGENT_LOOPS):
        assistant_msg = _call_deepseek(messages, tools=TOOLS)

        if not assistant_msg:
            final_answer = "抱歉，大模型暂时无响应，请稍后再试。"
            break

        # 没有工具调用 → 这就是最终回答
        if not assistant_msg.get("tool_calls"):
            content = assistant_msg.get("content", "").strip()
            if content:
                final_answer = content
                source_tag = "agent(chat)" if loop_i == 0 else "agent(RAG)"
                logger.info(f"  Agent 第 {loop_i + 1} 轮：无工具调用，直接回答")
            break

        # 有工具调用 → 执行并追加结果
        logger.info(f"  Agent 第 {loop_i + 1} 轮：LLM 调用了 {len(assistant_msg['tool_calls'])} 个工具")

        messages.append({
            "role": "assistant",
            "content": assistant_msg.get("content") or "",
            "tool_calls": assistant_msg["tool_calls"],
        })

        for tc in assistant_msg["tool_calls"]:
            if tc.get("type") == "function":
                tool_result = _execute_tool(tc)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": tool_result,
                })

        logger.info(f"  工具执行完毕，进入第 {loop_i + 2} 轮...")

    else:
        # 循环正常结束（达到 MAX_AGENT_LOOPS 还没出结果）
        if not final_answer:
            final_answer = "抱歉，我没能查到相关信息，建议换个问法试试。"

    if final_answer:
        return {"answer": final_answer, "source": source_tag}

    return {"answer": "抱歉，我没理解您的问题。", "source": "agent"}


# ===== 注册技能类 =====

@register
class RAGAgentSkill(BaseSkill):
    """RAG Agent 技能：大模型主动检索故障和标准知识库，或直接回答。"""
    name = "智能 RAG"
    description = "LLM 主动检索故障/标准知识库，并处理通用对话"
    priority = 50  # 低于精确故障代码；其余问题由本技能统一兜底

    @classmethod
    def match(cls, query: str) -> bool:
        """始终匹配 — 作为主要智能处理技能"""
        return True

    @classmethod
    def handle(cls, query: str, user_id: str = "") -> dict:
        return _handle_impl(query, user_id=user_id)


# 向后兼容：保持模块级 handle 函数
handle = _handle_impl
