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

logger = logging.getLogger("agent")

# 共享 HTTP 客户端（复用连接，避免每次建新连接）
_HTTP_CLIENT = httpx.Client(timeout=60)

# ===== 工具定义 =====

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_knowledge_base",
            "description": "搜索 PCS 故障知识库。当用户问 PCS 故障代码（d4-1 等格式）、告警、停机、不启动等设备异常现象时使用。包含故障代码的名称、原因、地址、位地址等详细信息。只含 PCS 产品故障数据，不含行业标准或技术规范。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词，使用用户问题中的核心词（故障代码、现象描述、文档标题等）。"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_standards",
            "description": "搜索储能变流器/光伏行业标准。包含国家标准(GB/T、GB)、行业标准、国际标准(IEC、EN)等，覆盖安全要求、并网要求、检测方法、电气性能、绝缘配合等技术规范内容。当用户询问国家标准、行业规范、技术要求或标准编号时使用。不包含 PCS 故障代码。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词，使用用户问题中的核心词（标准编号、术语、检测项目、技术指标等）。"
                    }
                },
                "required": ["query"]
            }
        }
    }
]

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

    if fn_name == "search_knowledge_base":
        query = args.get("query", "").strip()
        if not query:
            return json.dumps({"error": "搜索关键词为空，请提供要搜索的内容"}, ensure_ascii=False)

        logger.info(f"  工具调用: search_knowledge_base(query={query})")

        # 延迟导入 error_query（避免循环导入）
        from skills.error_query import search_kb

        results = search_kb(query)
        if not results:
            return json.dumps(
                {"found": False, "results": [], "message": f"未找到与「{query}」相关的故障信息"},
                ensure_ascii=False,
            )

        # 清理内部字段（_ 开头的供内部使用，不给 LLM 看）
        clean_results = []
        for r in results:
            item = {k: v for k, v in r.items() if not k.startswith("_")}
            clean_results.append(item)

        return json.dumps({"found": True, "results": clean_results}, ensure_ascii=False)

    if fn_name == "search_standards":
        query = args.get("query", "").strip()
        if not query:
            return json.dumps({"error": "搜索关键词为空，请提供要搜索的内容"}, ensure_ascii=False)

        logger.info(f"  工具调用: search_standards(query={query})")

        # 延迟导入 standards_query
        from skills.standards_query import search_kb as search_standards_kb

        results = search_standards_kb(query)
        if not results:
            return json.dumps(
                {"found": False, "results": [], "message": f"未找到与「{query}」相关的标准信息"},
                ensure_ascii=False,
            )

        # 清理内部字段，保留 LLM 所需字段
        clean_results = []
        for r in results:
            item = {
                k: v for k, v in r.items()
                if not k.startswith("_") or k == "_content"
            }
            # LLM 需要看到摘要内容来判断相关性
            if "_content" in r:
                item["content_summary"] = r["_content"]
            clean_results.append(item)

        return json.dumps({"found": True, "results": clean_results}, ensure_ascii=False)

    logger.warning(f"未知工具调用: {fn_name}")
    return json.dumps({"error": f"未知工具: {fn_name}"}, ensure_ascii=False)


def _handle_impl(query: str, user_id: str = "") -> dict:
    """RAG Agent 处理入口

    两级策略：
      1. 快速通道：精确匹配故障代码（d4-1 等）→ 直接返回格式化结果，不走 LLM
      2. Agent 通道：LLM + search_knowledge_base 工具 → 智能 RAG 回答

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

    # 构建系统提示词，注入记忆上下文
    system_content = SYSTEM_PROMPT
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
    """RAG Agent 技能：大模型主动检索知识库，智能回答"""
    name = "智能 RAG"
    description = "LLM 主动检索 PCS 故障知识库，智能回答故障相关问题"
    priority = 50  # 高于通用聊天，低于精确故障代码

    @classmethod
    def match(cls, query: str) -> bool:
        """始终匹配 — 作为主要智能处理技能"""
        return True

    @classmethod
    def handle(cls, query: str, user_id: str = "") -> dict:
        return _handle_impl(query, user_id=user_id)


# 向后兼容：保持模块级 handle 函数
handle = _handle_impl
