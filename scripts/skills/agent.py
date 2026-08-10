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
import threading
import time

_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

import httpx

from config import (DEEPSEEK_API_KEY, MAX_CONCURRENT_LLM,
                    MAX_CONTEXT_ROUNDS, MEMORY_BUDGET_TOKENS)
from skills import BaseSkill, register
from tools import get_tool_definitions, execute_tool as _execute_registered_tool

logger = logging.getLogger("agent")

# 共享 HTTP 客户端（复用连接，避免每次建新连接）
_HTTP_CLIENT = httpx.Client(timeout=60, trust_env=False)

# DeepSeek 并发限流：最多 MAX_CONCURRENT_LLM 个请求同时进行
# （多人并发时防费用失控 / API 429；acquire 阻塞发生在 to_thread 线程，
#   不阻塞 asyncio 事件循环）
_LLM_SEMAPHORE = threading.BoundedSemaphore(MAX_CONCURRENT_LLM)

# ===== 工具定义（从注册中心自动获取，新增工具无需改本文件） =====
TOOLS = get_tool_definitions()

# ===== System Prompt（从文件加载） =====

_PROMPT_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts", "system_prompt.txt")

# 内存缓存，避免每次对话都查 DB
_prompt_cache: dict[str, str] = {}


def _load_system_prompt() -> str:
    """加载 system prompt：优先从 DB 读取 → 回退到文件 → 缓存到内存"""
    global _prompt_cache
    if "system" in _prompt_cache:
        return _prompt_cache["system"]

    # 优先从 DB 读取
    try:
        from user_store import get_prompt
        db_prompt = get_prompt("system")
        if db_prompt:
            _prompt_cache["system"] = db_prompt
            return db_prompt
    except Exception as e:
        logger.debug(f"从 DB 加载 prompt 失败（回退到文件）: {e}")

    # 回退到文件
    try:
        with open(_PROMPT_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
    except FileNotFoundError:
        logger.warning(f"System prompt 文件不存在: {_PROMPT_FILE}，使用默认提示词")
        content = "你是恩特小助手，恩特能源内部使用的 AI 助手。"

    _prompt_cache["system"] = content
    return content


def reload_system_prompt() -> str:
    """清除缓存并重新加载 system prompt（供后台管理调用）"""
    global _prompt_cache
    _prompt_cache.pop("system", None)
    return _load_system_prompt()


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

    _t0 = time.time()

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

    # ISP 偶发波动 / 服务端瞬断 / 限流时自动重试（指数退避），避免全量失效
    _RETRYABLE_STATUS = (429, 500, 502, 503, 504)
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            # 并发限流：仅网络等待期间持有信号量，重试退避不占并发槽
            with _LLM_SEMAPHORE:
                r = _HTTP_CLIENT.post(
                    "https://api.deepseek.com/chat/completions",
                    headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
                    json=body,
                )
            if r.status_code == 200:
                data = r.json()
                if data.get("choices"):
                    # 记录缓存命中（DeepSeek 前缀缓存观测，验证上下文工程收益）
                    usage = data.get("usage", {}) or {}
                    hit = int(usage.get("prompt_cache_hit_tokens", 0) or 0)
                    miss = int(usage.get("prompt_cache_miss_tokens", 0) or 0)
                    hit_rate = (hit / (hit + miss) * 100) if (hit + miss) else 0
                    logger.info(
                        f"    DeepSeek 响应 {time.time() - _t0:.1f}s（本轮回合）"
                        f" 缓存命中 {hit} / 未命中 {miss}（命中率 {hit_rate:.0f}%）")
                    return data["choices"][0]["message"]
                logger.warning("DeepSeek 返回空 choices")
                return None
            if r.status_code in _RETRYABLE_STATUS and attempt < max_attempts:
                logger.warning(
                    f"DeepSeek API 返回 {r.status_code}（第 {attempt}/{max_attempts} 次），稍后重试")
                time.sleep(attempt)  # 1s → 2s 退避
                continue
            logger.warning(f"DeepSeek API 返回 {r.status_code}: {r.text[:200]}")
            return None
        except httpx.TimeoutException:
            logger.warning(f"DeepSeek API 超时（第 {attempt}/{max_attempts} 次）")
            if attempt < max_attempts:
                time.sleep(attempt)
                continue
            return None
        except httpx.RequestError as e:
            logger.warning(
                f"DeepSeek API 请求失败: {e}（第 {attempt}/{max_attempts} 次）")
            if attempt < max_attempts:
                time.sleep(attempt)
                continue
            return None
        except Exception as e:
            logger.warning(f"DeepSeek API 未知错误: {e}")
            return None
    return None


def _call_deepseek_stream(
    messages: list[dict],
    tools: list | None = None,
    on_chunk=None,
    max_tokens: int = 8000,
) -> dict | None:
    """流式调用 DeepSeek API（SSE），逐 chunk 回调 on_chunk

    Args:
        messages: 消息列表（OpenAI 格式）
        tools: 工具定义列表（可选）
        on_chunk: callback(text: str, status: str)
            status: "thinking" | "content" | "tool_call" | "done"
        max_tokens: 最大输出 token 数

    Returns:
        组装后的 message dict（兼容非流式路径），或 None（调用失败）
    """
    if not DEEPSEEK_API_KEY:
        logger.warning("DEEPSEEK_API_KEY 未配置")
        return None

    _t0 = time.time()

    body = {
        "model": "deepseek-v4-flash",
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": max_tokens,
        "thinking": {"type": "enabled"},
        "stream": True,
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"

    _RETRYABLE_STATUS = (429, 500, 502, 503, 504)
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            # 流式模式：信号量持有到流结束（整个 SSE 迭代期间不释放）
            with _LLM_SEMAPHORE:
                with _HTTP_CLIENT.stream(
                    "POST",
                    "https://api.deepseek.com/chat/completions",
                    headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
                    json=body,
                ) as r:
                    if r.status_code in _RETRYABLE_STATUS and attempt < max_attempts:
                        logger.warning(
                            f"DeepSeek 流式 API 返回 {r.status_code}（第 {attempt}/{max_attempts} 次），稍后重试")
                        r.read()  # 消费 body 避免连接泄漏
                        time.sleep(attempt)
                        continue
                    if r.status_code != 200:
                        r.read()
                        logger.warning(f"DeepSeek 流式 API 返回 {r.status_code}")
                        return None

                    # SSE 解析
                    final_content = []
                    final_tool_calls = {}  # index → {id, type, function: {name, arguments}}
                    for line in r.iter_lines():
                        if not line or not line.startswith("data: "):
                            continue
                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
                        delta = chunk.get("choices", [{}])[0].get("delta", {})

                        # thinking（推理过程，不输出到卡片）
                        reasoning = delta.get("reasoning_content", "")
                        if reasoning and on_chunk:
                            on_chunk(reasoning, "thinking")

                        # content（实际回复内容）
                        content = delta.get("content", "")
                        if content:
                            final_content.append(content)
                            if on_chunk:
                                on_chunk(content, "content")

                        # tool_calls（流式拼接参数）
                        tc_delta = delta.get("tool_calls")
                        if tc_delta:
                            for tc in tc_delta:
                                idx = tc.get("index", 0)
                                # 首次出现该 tool_call → 立即通知卡片「搜索中」
                                # 不等参数累积完，减少用户感知空白期
                                if idx not in final_tool_calls and on_chunk:
                                    fn_name = tc.get("function", {}).get("name", "")
                                    _TOOL_DISPLAY = {
                                        "search_knowledge_base": "🔍 搜索故障知识库...",
                                        "search_standards": "🔍 搜索标准文档...",
                                        "search_experience_kb": "🔍 搜索经验库...",
                                        "calc_pcb_trace": "🔧 PCB 走线计算...",
                                        "calc_copper_busbar": "🔧 铜排载流计算...",
                                        "find_employee": "👥 查询同事信息...",
                                        "describe_image": "🖼️ 识别图片内容...",
                                    }
                                    display = _TOOL_DISPLAY.get(fn_name, f"🔍 {fn_name}...")
                                    on_chunk(display, "tool_call")
                                if idx not in final_tool_calls:
                                    final_tool_calls[idx] = {
                                        "id": tc.get("id", ""),
                                        "type": "function",
                                        "function": {
                                            "name": tc.get("function", {}).get("name", ""),
                                            "arguments": "",
                                        },
                                    }
                                args_chunk = tc.get("function", {}).get("arguments", "")
                                if args_chunk:
                                    final_tool_calls[idx]["function"]["arguments"] += args_chunk

            # 流结束，组装最终 message
            _elapsed = time.time() - _t0
            full_text = "".join(final_content)
            logger.info(f"    DeepSeek 流式响应 {_elapsed:.1f}s（{len(full_text)} 字）")

            if on_chunk:
                on_chunk("", "done")

            result = {"content": full_text}
            if final_tool_calls:
                result["tool_calls"] = [
                    final_tool_calls[i] for i in sorted(final_tool_calls)
                ]
            return result

        except httpx.TimeoutException:
            logger.warning(f"DeepSeek 流式 API 超时（第 {attempt}/{max_attempts} 次）")
            if attempt < max_attempts:
                time.sleep(attempt)
                continue
            return None
        except httpx.RequestError as e:
            logger.warning(f"DeepSeek 流式 API 请求失败: {e}（第 {attempt}/{max_attempts} 次）")
            if attempt < max_attempts:
                time.sleep(attempt)
                continue
            return None
        except Exception as e:
            logger.warning(f"DeepSeek 流式 API 未知错误: {e}")
            return None
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


def _handle_impl(query: str, user_id: str = "", on_chunk=None) -> dict:
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
        on_chunk: 可选 callback(text, status) 用于流式输出
            status: "thinking" | "content" | "tool_call" | "done"

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
            if on_chunk:
                on_chunk(answer, "done")
            return {"answer": answer, "source": f"遥信（DI）表 第{row}行"}

    # ===== 第 1.5 关：快速通道（精确标准编号，毫秒级） =====
    from skills.standards_query import extract_standard_id, exact_match_by_std_id, format_exact_result as format_std_exact_result

    std_id = extract_standard_id(q)
    if std_id:
        match = exact_match_by_std_id(std_id)
        if match:
            answer = format_std_exact_result(match["metadata"])
            if on_chunk:
                on_chunk(answer, "done")
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

    # 构建 messages：静态 system + 历史独立轮次 + 动态上下文末尾
    # （system 保持纯静态 → 前缀缓存稳定；历史作为独立轮次回放 → 追加式增长）
    messages = [{"role": "system", "content": _load_system_prompt()}]

    # 历史轮次：从窗口滚动点取最近对话（窗口 N 轮 + 释放 1/2），
    # 作为独立 user/assistant 消息回放，不再拼进 system
    window_msgs = []
    if user_id:
        try:
            from user_store import get_store as get_user_store
            window_msgs = get_user_store().get_window_context(
                user_id, MAX_CONTEXT_ROUNDS,
                max_content_chars=MEMORY_BUDGET_TOKENS)
            logger.info(f"已回放 {user_id} 的历史轮次 ({len(window_msgs)} 条)")
        except Exception as e:
            logger.warning(f"获取窗口对话失败（不影响主流程）: {e}")
    messages.extend(window_msgs)

    # 动态上下文（用户档案 + 长期记忆）放消息末尾，与当前问题合并，
    # 不污染静态前缀（前缀 = system + 已回放历史，逐 token 稳定）
    dynamic_parts = []
    if user_info_lines:
        dynamic_parts.append(
            "【当前用户信息】\n"
            + "\n".join(user_info_lines)
            + "\n（用户问及身份、部门、中心时可参考上述信息回答；知识库查询时自动按归属中心过滤）"
        )
        logger.info(f"已注入用户档案 ({len(user_info_lines)} 条)")
    if user_id:
        try:
            from skills import memory
            long_term = memory.format_long_term(user_id)
            if long_term:
                dynamic_parts.append(
                    "【长期记忆 - 用户历史背景】\n"
                    "以下是你长期记录中关于该用户的重要事实与历史会话摘要，回答时若相关请主动引用：\n"
                    + long_term
                )
                logger.info(f"已注入 {user_id} 的长期记忆 ({len(long_term)}字)")
        except Exception as e:
            logger.warning(f"注入长期记忆失败: {e}")
    final_q = ("\n\n".join(dynamic_parts) + "\n\n" + q) if dynamic_parts else q
    messages.append({"role": "user", "content": final_q})

    logger.info(
        f"Agent 处理: {q[:80]}" + (f" [{user_id}]" if user_id else "")
        + f"（system {len(_load_system_prompt())} 字静态 + {len(window_msgs)} 条历史轮次）")

    # Agent 循环：允许 LLM 多次调用工具（搜不到自动降级到钉钉知识库）
    MAX_AGENT_LOOPS = 5  # 安全上限，防止死循环
    final_answer = ""
    source_tag = "agent"

    for loop_i in range(MAX_AGENT_LOOPS):
        if on_chunk:
            assistant_msg = _call_deepseek_stream(
                messages, tools=TOOLS, on_chunk=on_chunk)
        else:
            assistant_msg = _call_deepseek(messages, tools=TOOLS)

        if not assistant_msg:
            final_answer = "抱歉，大模型暂时无响应，请稍后再试。"
            break

        # 没有工具调用 → 这就是最终回答
        if not assistant_msg.get("tool_calls"):
            # content 可能为 None（LLM 只返回 thinking/拒绝回答），需兼容
            content = (assistant_msg.get("content") or "").strip()
            if content:
                final_answer = content
                source_tag = "agent(chat)" if loop_i == 0 else "agent(RAG)"
                logger.info(f"  Agent 第 {loop_i + 1} 轮：无工具调用，直接回答")
            break

        # 有工具调用 → 通知卡片「搜索中」+ 执行并追加结果
        logger.info(f"  Agent 第 {loop_i + 1} 轮：LLM 调用了 {len(assistant_msg['tool_calls'])} 个工具")

        messages.append({
            "role": "assistant",
            "content": assistant_msg.get("content") or "",
            "tool_calls": assistant_msg["tool_calls"],
        })

        for tc in assistant_msg["tool_calls"]:
            if tc.get("type") == "function":
                # 工具显示已在 _call_deepseek_stream 流式阶段触发（首次出现 tool_call 时）
                # 此处不再重复显示
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
            # 轮数超限 ≠ 真没查到：保留原兜底文案，但末尾追加「可能不完整」提示；
            # 并在日志显式标记轮数耗尽，避免排查时误判为检索无结果
            logger.warning(
                f"Agent 达到轮数上限 {MAX_AGENT_LOOPS} 仍无最终答案，返回兜底文案")
            final_answer = (
                "抱歉，我没能查到相关信息，建议换个问法试试。"
                "（提示：本次处理因复杂度较高被截断，以上结果可能不完整、"
                "不代表真实情况，仅供参考）"
            )

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
    def handle(cls, query: str, user_id: str = "", on_chunk=None, **kwargs) -> dict:
        return _handle_impl(query, user_id=user_id, on_chunk=on_chunk)


# 向后兼容：保持模块级 handle 函数
handle = _handle_impl
