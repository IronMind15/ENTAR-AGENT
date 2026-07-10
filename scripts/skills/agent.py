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
            "description": "搜索 PCS 故障知识库，每条结果包含：故障代码(d4-1)、名称、原因、地址(0x3004)、位地址(bit0)、数据类型、备注等级(一级/二级/三级)、说明。当用户询问故障代码含义、设备异常告警、故障原因排查时使用此工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词，如故障代码（d4-1）、故障名称（急停告警）、现象描述（逆变器不启动）、故障原因等。使用用户提到的原词或精简后的关键词。"
                    }
                },
                "required": ["query"]
            }
        }
    }
]

# ===== System Prompt =====

SYSTEM_PROMPT = (
    "你是恩特小助手，恩特能源（天津恩特能源科技有限公司，品牌 ENTAR）内部使用的 AI 助手。"
    "使用你的人都是公司内部同事（测试、售后、研发、生产等岗位），不是外部产品用户。\n\n"
    "你有一个 search_knowledge_base 工具，可以搜索公司内部的 PCS 故障知识库。\n\n"
    "== 什么时候该搜索，什么时候不搜索 ==\n\n"
    "以下交互示例可以帮助你判断：\n\n"
    "示例1 - 需要搜索（用户问故障代码）：\n"
    "  用户：d4-1 是什么故障\n"
    "  你：调用 search_knowledge_base(query=\"d4-1\") → 拿到结果后，用自然语言解释\n\n"
    "示例2 - 需要搜索（用户描述故障现象）：\n"
    "  用户：逆变器不启动了，报了个急停告警\n"
    "  你：调用 search_knowledge_base(query=\"急停告警\") → 拿到结果后解释原因\n\n"
    "示例3 - 不需要搜索（闲聊）：\n"
    "  用户：今天天气怎么样\n"
    "  你：直接回答\"抱歉，我无法查询实时天气，建议看看手机上的天气App\"\n\n"
    "示例4 - 不需要搜索（公司制度）：\n"
    "  用户：咱们公司食堂几点开门\n"
    "  你：直接回答\"抱歉，这个我不清楚，建议问一下行政部门\"\n\n"
    "== 搜索关键词规则 ==\n"
    "- 提取用户问题中的核心故障相关词，不要修改用户原词\n"
    "- 一次只传一个最关键的关键词即可（如故障代码、故障名称）\n"
    "- 如果用户说法模糊，不确定是否要搜，不如先搜了再说\n\n"
    "== 回答格式 ==\n"
    "- 先给出结论（1~2句总结）\n"
    "- 再展开故障代码、原因、地址、处理建议等关键信息\n"
    "- 用自然语言解释，不要直接丢原始数据\n"
    "- 如果找到多条结果，说明各条的区别\n"
    "- 如果没有找到，直接说没找到，不编造\n"
    "- 回答最后可以问一句是否还需要进一步帮助\n\n"
    "== 绝对不能编造 ==\n"
    "你没有公司内部人员的信息（员工姓名、岗位、联系方式等），"
    "没有公司食堂/周边餐饮的信息，没有公司非公开资料。"
    "如果同事问到你不知道的，直接说'这个我不清楚，建议问一下相关负责人'。"
    "宁可说不知道，也不要编造信息。"
)


def _call_deepseek(
    messages: list[dict],
    tools: list | None = None,
    max_tokens: int = 4000,
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
    }
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

    # 第一轮：发送消息 + 工具定义，让 LLM 决定是否调用工具
    assistant_msg = _call_deepseek(messages, tools=TOOLS)

    if not assistant_msg:
        return {"answer": "抱歉，大模型暂时无响应，请稍后再试。", "source": "agent"}

    final_answer = ""

    # 处理工具调用
    if assistant_msg.get("tool_calls"):
        logger.info(f"  LLM 调用了 {len(assistant_msg['tool_calls'])} 个工具")

        # 将 assistant 消息加入上下文（含 tool_calls）
        messages.append({
            "role": "assistant",
            "content": assistant_msg.get("content") or "",
            "tool_calls": assistant_msg["tool_calls"],
        })

        # 逐个执行工具
        for tc in assistant_msg["tool_calls"]:
            if tc.get("type") == "function":
                tool_result = _execute_tool(tc)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": tool_result,
                })

        logger.info("  工具执行完毕，请求 LLM 生成最终回答...")

        # 第二轮：带工具结果的 LLM 调用
        final_msg = _call_deepseek(messages, max_tokens=4000)
        if final_msg and final_msg.get("content"):
            final_answer = final_msg["content"].strip()

    else:
        # 没有工具调用：直接返回 LLM 回答（闲聊、非故障问题等）
        content = assistant_msg.get("content", "").strip()
        if content:
            logger.info(f"  无工具调用，直接回答")
            final_answer = content

    if final_answer:
        source_tag = "agent(RAG)" if assistant_msg.get("tool_calls") else "agent(chat)"
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
