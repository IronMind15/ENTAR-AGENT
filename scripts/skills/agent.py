"""
RAG Agent 技能 — 大模型 + 知识库搜索工具

当问题与故障相关时，LLM 主动调用 kb_search 工具检索本地知识库，
结合检索结果进行推理和回答。非故障问题直接聊天，无需搜索。

使用 DeepSeek API 的 function calling（tools 参数）实现原生工具调用。
V4 模型仅支持 tool_choice="auto"，由 LLM 自行判断是否调用工具。
"""

import json
import logging
import os
import re
import sys
import threading
import time

import httpx

from scripts.config import (DASHBOARD_LLM_MODEL, DEEPSEEK_API_KEY, LLM_MODEL, MAX_CONCURRENT_LLM, TESTING,
                    MAX_CONTEXT_ROUNDS, KEEP_CONTEXT_ROUNDS)
from scripts.skills import BaseSkill, register
from scripts.tools import (get_tool_definitions, execute_tool as _execute_registered_tool,
                   get_tool_display_map, is_write_tool, render_tool_prompt,
                   _TOOL_SECTION_MARKER)

logger = logging.getLogger("agent")

# 共享 HTTP 客户端（复用连接，避免每次建新连接）
_HTTP_CLIENT = httpx.Client(timeout=60, trust_env=False)


def _test_mocked_http_method(method_name: str) -> bool:
    """测试模式下仅允许 unittest.mock 替身承接 HTTP 请求。

    这样保留请求体、重试、并发等单元测试的价值，同时避免某个漏 mock 的
    测试把开发机配置或占位 key 发往真实 DeepSeek 服务。
    """
    method = getattr(_HTTP_CLIENT, method_name, None)
    return type(method).__module__.startswith("unittest.mock")

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

_OPERATION_POLICY_PROMPT = """

【工具操作安全规则】
- 查询、计算、识别、总结但不外发等只读工具可以直接执行。
- 创建、删除、修改、启停、主动外发等会改变系统或影响他人的操作，必须尊重工具返回的 confirmation_required；此时操作尚未执行，只能说明拟执行目标和影响并请用户再次确认。
- 绝不能仅凭用户自然语言或模型判断声称“已创建/已删除/已修改/已推送”。只有工具实际返回成功字段后才能报告成功；工具返回 error 时必须如实说明失败。
- 不得绕过待确认机制，也不得把“准备执行”“已记录请求”表述为“已执行”。
""".strip()


def _with_operation_policy(content: str) -> str:
    return content if _OPERATION_POLICY_PROMPT in content else content + "\n\n" + _OPERATION_POLICY_PROMPT


# v1.12.5：Agent 幻觉护栏（硬校验，prompt 约束是软的）。
# 真实写操作只能经工具执行（且带二次确认）；本轮零工具调用时，
# 回答却以「✅ 已…」「已成功…」等完成态声明执行了写操作 → 必是幻觉，
# 在回复末尾强制追加纠偏（用户是执行/确认指令的场景）。
_FAKE_EXEC_DONE_RE = re.compile(
    r"✅\s*已|已(?:成功|确认)?(?:创建|删除|移除|修改|调整|推送|发送|"
    r"开通|订阅|设置|恢复|停止|保存|切换|更新|添加|启停|"
    r"生成|转达|记录|提交|完成|执行|生效)")
# v1.13.3：将来时承诺盲区——完成态护栏（_FAKE_EXEC_DONE_RE）只拦「已…」，
# LLM 会用「将停用」「将为您调整」等将来时承诺替代完成态（实测袁会荧
# 「按之前的那个来」→ 零工具编造「将重新开通看板」，靠用户补「确认」才落地）。
# 将来时 + 写操作动词同样进纠偏；不用孤立「会」字，防「我会帮您查看」误伤。
_FAKE_EXEC_FUTURE_RE = re.compile(
    r"(?:将|马上|即将|稍后)[^。！？\n]{0,6}?(?:停用|删除|取消|修改|调整|推送|发送|"
    r"开通|恢复|创建|停止|更新|添加|移出|移除)")
# 用户下达执行/确认指令的触发词：确认/执行类 + 命令式写操作动词。
# 动词要求命令式结构（帮我删/把XX删/删掉…），孤立动词字（「我昨天删的文件」）
# 是陈述/询问历史，不算执行指令——防复述历史被误纠偏。
_ACTION_CONFIRM_RE = re.compile(r"确认|执行|开始|好的|可以|就这么办|就这样")
_ACTION_VERB_RE = re.compile(
    r"(?:帮我|给我|请|麻烦|现在|马上)[^，。！？\n]{0,10}?(?:删|改|停|建|设|换|推|发|加|移|恢复|开通|订阅)"
    r"|(?:把|将)[^，。！？\n]{0,12}?(?:删|改|停|建|设|换|推|发|加|移)"
    r"|(?:删掉|删除|移除|去掉|停掉|停用|停止|开通|恢复|换成|改成|改为|发送|推送|加个|添加)")
# 追加的纠偏说明（不得声称已执行未发生的写操作）
_FAKE_EXEC_CORRECTION = (
    "\n\n⚠️ 说明：本轮我未执行任何写操作（只做了查询/只读动作，或未调用工具）。"
    "以上如出现「已创建/已删除/已推送/已修改」等完成态表述，属错误描述。"
    "如需调整（如看板数据源、订阅等），请明确说出要调整的内容，"
    "我会通过正式确认流程执行。"
)


def _needs_fake_exec_correction(query: str, content: str,
                                write_tool_used: bool) -> bool:
    """完成态/将来时写操作声明，但本轮无写操作工具执行 → 纠偏。

    v1.12.6（B4）：原判定 `tools_used`（本轮是否调过任何工具）太宽——只读工具
    （kb_search/contact_find/dash_query 等）的调用不能证明写操作已执行。实测 LLM
    调了 contact_find（查通讯录）后继续编造「看板维护转达工单」，护栏因 tools_used
    =True 放行。改为只认「写操作工具已执行」（policy.confirm 工具 + 二次确认）：
    只调了只读工具却声称「已创建/已删除/已推送」→ 仍是幻觉，强制纠偏。

    v1.13.3：补将来时承诺盲区——完成态护栏漏掉「将停用」「将为您调整」等
    将来时承诺（实测袁会荧「按之前的那个来」→ LLM 零工具编造「将重新开通」）。
    现在完成态（_FAKE_EXEC_DONE_RE）**或**将来时承诺（_FAKE_EXEC_FUTURE_RE）
    命中即进后续判定，两道闸（has_instruction / has_emphasis_done）保持不变。

    触发条件（满足其一即可，防止复述历史被误纠偏）：
    - 用户下达了执行/确认指令（「确认修改」「帮我删掉XX」）——这是明确的写请求；
    - 回答带 ✅ 前缀强调完成态（工单幻觉场景：用户「我说选第一个」本无指令词，
      但 LLM 编造「✅ 工单已生成」——✅ 前缀 = LLM 主动强调写操作完成，同样异常）。
    """
    if write_tool_used:
        return False  # 真执行过写操作（带二次确认）→ 完成态声明有依据，不硬拦
    if not (_FAKE_EXEC_DONE_RE.search(content)
            or _FAKE_EXEC_FUTURE_RE.search(content)):
        return False
    has_instruction = (_ACTION_CONFIRM_RE.search(query)
                       or _ACTION_VERB_RE.search(query))
    has_emphasis_done = bool(re.search(r"✅", content))
    if not (has_instruction or has_emphasis_done):
        return False
    return True


# ===== 工具段归一化（v1.12.0：工具段永远由注册中心生成） =====
# 旧版 system prompt 里手写的工具段没有这个标记，无法定位删除；
# 新版统一追加 _TOOL_SECTION_MARKER 包裹的注册中心生成段，读侧按标记切掉旧段。
# v1.13.4：标记复用注册中心定义（tools/__init__.py），不再双处维护同一字面量。

# 旧工具名 → 新工具名（v1.12.0 改名映射，读侧幂等 replace）。
# 用于归一化 DB 里遗留的旧 prompt：工具段会被强制生成覆盖，规则段里残留的旧名
# 靠本表换新，避免与注册中心实际注册名（新名）不一致。幂等：新名里不会含旧名。
_TOOL_NAME_MIGRATION = {
    "search_knowledge_base": "kb_search",
    "create_knowledge_base": "kb_create",
    "manage_uploaded_file": "kb_file_manage",
    "query_dashboard": "dash_query",
    "push_dashboard": "dash_push",
    "find_employee": "contact_find",
    "describe_image": "image_describe",
    "summarize_doc": "doc_summarize",
}


def _normalize_prompt_base(raw: str) -> str:
    """归一化 prompt 基础段：丢弃旧工具段 + 旧工具名换新（幂等）。"""
    text = raw.split(_TOOL_SECTION_MARKER)[0].strip()
    for old, new in _TOOL_NAME_MIGRATION.items():
        text = text.replace(old, new)
    return text


def _load_system_prompt() -> str:
    """加载 system prompt：优先从 DB 读取 → 回退到文件 → 归一化 → 追加注册中心工具段 → 缓存

    v1.12.0：prompt = 归一化基础段（角色/规则，含旧名迁移）+ 注册中心生成的工具段。
    工具段永远由注册中心生成，DB/文件里任何旧工具段都会被 _normalize_prompt_base 丢弃。
    """
    global _prompt_cache
    if "system" in _prompt_cache:
        return _prompt_cache["system"]

    # 优先从 DB 读取基础段
    base = ""
    try:
        from scripts.user_store import get_prompt
        db_prompt = get_prompt("system")
        if db_prompt:
            base = db_prompt
    except Exception as e:
        logger.debug(f"从 DB 加载 prompt 失败（回退到文件）: {e}")

    # 回退到文件
    if not base:
        try:
            with open(_PROMPT_FILE, "r", encoding="utf-8") as f:
                base = f.read()
        except FileNotFoundError:
            logger.warning(f"System prompt 文件不存在: {_PROMPT_FILE}，使用默认提示词")
            base = "你是恩特小助手，恩特能源内部使用的 AI 助手。"

    base = _normalize_prompt_base(base)
    content = (base + "\n\n" + render_tool_prompt()).strip()
    _prompt_cache["system"] = content
    return content


def reload_system_prompt() -> str:
    """清除缓存并重新加载 system prompt（供后台管理调用）"""
    global _prompt_cache
    _prompt_cache.pop("system", None)
    return _load_system_prompt()


SYSTEM_PROMPT = _load_system_prompt()


def call_deepseek(prompt: str, max_tokens: int = 4000) -> str:
    """公开包装 _call_deepseek：传入单个用户 prompt，返回纯文本内容。

    供看板 LLM 组装等非对话场景使用（不参与前缀缓存结构）。失败返回空串，
    由调用方兜底（看板组装失败走规则模板）。
    """
    try:
        resp = _call_deepseek([{"role": "user", "content": prompt}],
                              max_tokens=max_tokens)
        if resp and resp.get("content"):
            return resp["content"]
    except Exception as e:
        logger.warning(f"call_deepseek 失败: {e}")
    return ""


def call_deepseek_json(prompt: str, max_tokens: int = 4000) -> str:
    """结构化提炼专用调用：关闭思考模式，避免推理 token 挤占 JSON 正文。"""
    try:
        resp = _call_deepseek([{"role": "user", "content": prompt}],
                              max_tokens=max_tokens, thinking=False,
                              json_output=True, timeout_seconds=100)
        if resp and resp.get("content"):
            return resp["content"]
    except Exception as e:
        logger.warning(f"call_deepseek_json 失败: {e}")
    return ""


def call_dashboard_json(prompt: str, max_tokens: int = 4000) -> str:
    """看板专用的一次性结构化调用。

    它不进入主 Agent 对话历史，也不共享主 Agent 的 system prompt；模型名
    通过 DASHBOARD_LLM_MODEL 单独配置。默认值与旧行为相同，便于渐进切换。
    """
    try:
        logger.info("[看板执行模型] 调用 model=%s payload_chars=%s（独立于主 Agent 对话）",
                    DASHBOARD_LLM_MODEL, len(prompt or ""))
        resp = _call_deepseek([{"role": "user", "content": prompt}],
                              max_tokens=max_tokens, thinking=False,
                              json_output=True, timeout_seconds=100,
                              model=DASHBOARD_LLM_MODEL)
        if resp and resp.get("content"):
            return resp["content"]
    except Exception as e:
        logger.warning(f"call_dashboard_json 失败: {e}")
    return ""


def _call_deepseek(
    messages: list[dict],
    tools: list | None = None,
    max_tokens: int = 8000,
    thinking: bool = True,
    json_output: bool = False,
    timeout_seconds: float = 60,
    model: str | None = None,
) -> dict | None:
    """调用 DeepSeek API（通用封装）

    Args:
        messages: 消息列表（OpenAI 格式）
        tools: 工具定义列表（可选）
        max_tokens: 最大输出 token 数

    Returns:
        response message dict，或 None（调用失败时）
    """
    if model is None:
        model = LLM_MODEL
    if not DEEPSEEK_API_KEY:
        logger.warning("DEEPSEEK_API_KEY 未配置")
        return None
    if TESTING and not _test_mocked_http_method("post"):
        logger.warning("测试模式禁止未 mock 的 DeepSeek 网络调用")
        return None

    _t0 = time.time()

    body = {
        "model": model,
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": max_tokens,
    }
    body["thinking"] = {"type": "enabled" if thinking else "disabled"}
    if json_output:
        body["response_format"] = {"type": "json_object"}
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
                    timeout=timeout_seconds,
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
    if TESTING and not _test_mocked_http_method("stream"):
        logger.warning("测试模式禁止未 mock 的 DeepSeek 流式网络调用")
        return None

    _t0 = time.time()

    body = {
        "model": LLM_MODEL,
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
                                    # v1.12.0：显示映射由注册中心生成（display 字段），
                                    # 不再在本文件手写清单
                                    display = get_tool_display_map().get(fn_name, f"🔍 {fn_name}...")
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
    from scripts.skills.error_query import extract_fault_code, _exact_match_by_code, format_exact_result

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
    from scripts.skills.standards_query import extract_standard_id, exact_match_by_std_id, format_exact_result as format_std_exact_result

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
            from scripts.user_store import get_store
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
                    from scripts.center_config import get_center_name
                    center_names = [get_center_name(c) for c in user_centers]
                    user_info_lines.append(f"归属中心：{'、'.join(center_names)}")
                    # 设置工具上下文，实现按中心隔离查询
                    from scripts.tools import set_user_centers
                    set_user_centers(user_centers)
        except Exception as e:
            logger.warning(f"获取用户信息失败（不影响主流程）: {e}")

    # 构建 messages：静态 system + 历史独立轮次 + 动态上下文末尾
    # （system 保持纯静态 → 前缀缓存稳定；历史作为独立轮次回放 → 追加式增长）
    messages = [{"role": "system", "content": _with_operation_policy(_load_system_prompt())}]

    # 历史轮次：从窗口滚动点取最近对话（窗口 50 轮，满时释放 30 保留 20），
    # 作为独立 user/assistant 消息回放，不再拼进 system；
    # 字数不设上限（以 DeepSeek 1M 上下文为最高限制）
    window_msgs = []
    if user_id:
        try:
            from scripts.user_store import get_store as get_user_store
            window_msgs = get_user_store().get_window_context(
                user_id, MAX_CONTEXT_ROUNDS,
                keep_rounds=KEEP_CONTEXT_ROUNDS)
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
            from scripts.skills import memory
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
    # v1.12.6（B4）：本轮实际调用的工具名集合（护栏据此判断是否执行过写操作）。
    # 只调只读工具（kb_search/contact_find 等）不算「写操作有依据」。
    tools_called: set[str] = set()

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
                # v1.12.6（B4）：本轮未执行写操作工具（只读工具或零工具）却声称
                # 「已执行写操作」→ 幻觉，强制追加纠偏。
                write_tool_used = any(
                    is_write_tool(name) for name in tools_called)
                if _needs_fake_exec_correction(q, content, write_tool_used):
                    final_answer = content + _FAKE_EXEC_CORRECTION
                    logger.warning(
                        f"  Agent 幻觉护栏：本轮未执行写操作工具却声称已执行写操作，"
                        f"已追加纠偏（{q[:30]}… tools={sorted(tools_called)}）")
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
                fn_name = tc.get("function", {}).get("name", "")
                tools_called.add(fn_name)
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
    def match(cls, query: str, user_id: str = "") -> bool:
        """始终匹配 — 作为主要智能处理技能"""
        return True

    @classmethod
    def handle(cls, query: str, user_id: str = "", on_chunk=None, **kwargs) -> dict:
        return _handle_impl(query, user_id=user_id, on_chunk=on_chunk)


# 向后兼容：保持模块级 handle 函数
handle = _handle_impl
