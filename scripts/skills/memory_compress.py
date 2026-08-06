"""
长期记忆压缩模块（双层记忆：长期事实 + 摘要）

后台任务入口：compress_user_history(user_id)
由 skills/memory.py 的 _maybe_schedule_compress 触发，在 task_manager 线程池中运行。

流程：取窗口外最旧一批未压缩对话 → LLM 压成 {facts, summary} → 存 long_term_memories
      → 标记该批次 compressed=1。LLM 失败只记日志，消息保持未压缩待下次重试。
"""

import json
import logging
import threading
import time

logger = logging.getLogger("skills.memory_compress")

# 防抖：正在压缩的用户不再重复调度（模块级，跨线程安全）
_pending: set[str] = set()
_pending_lock = threading.Lock()


def _load_prompt() -> str:
    """加载压缩提示词（prompts/memory_compress.txt），失败回退内置"""
    import os
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "prompts",
        "memory_compress.txt",
    )
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        logger.warning(f"加载压缩提示词失败，使用内置: {e}")
        return _DEFAULT_PROMPT


_DEFAULT_PROMPT = (
    "你是记忆整理助手。请把一段「用户与助手的中文对话」压缩为长期记忆，供机器人未来跨会话参考。\n"
    '只输出一个 JSON 对象，不要输出任何其他文字：{"facts": ["重要事实，每条不超过25字，最多4条"], '
    '"summary": "对话进展摘要，不超过60字"}\n'
    "规则：1. 只提取对未来会话有参考价值的信息，宁缺毋滥；"
    "2. 不确定用「可能/待确认」；3. summary 只概括进展与未决；"
    "4. 无重要事实时 facts 输出 []；5. 全中文。"
)


def _get_store():
    from user_store import get_store
    return get_store()


def _get_cfg(name, default):
    try:
        import importlib
        cfg = importlib.import_module("config")
        return getattr(cfg, name, default)
    except Exception:
        return default


# ===== LLM 调用 =====

def _call_llm(messages: list[dict], max_tokens: int = 1000) -> str | None:
    """极简 LLM 调用（DeepSeek，关闭 thinking，1 次重试 + 指数退避）

    必须关闭 thinking：V4 是思考型模型，压缩长对话时思考 token 会占满
    max_tokens 把 content 挤空（此前长期记忆一直「无有效输出」的根因）。
    压缩是结构化 JSON 任务，不需要思考，关闭后稳定返回 content。
    """
    try:
        import httpx
        import importlib
        cfg = importlib.import_module("config")
        api_key = getattr(cfg, "DEEPSEEK_API_KEY", "")
        if not api_key:
            logger.warning("DEEPSEEK_API_KEY 未配置，跳过长期记忆压缩")
            return None
        url = "https://api.deepseek.com/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        body = {
            "model": "deepseek-v4-flash",
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.3,
            "thinking": {"type": "disabled"},  # 关思考，防 content 被挤空
        }
        for attempt in range(2):
            try:
                with httpx.Client(timeout=60, trust_env=False) as client:
                    resp = client.post(url, json=body, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()
                    content = data["choices"][0]["message"].get("content")
                    return content or None
            except Exception as e:
                logger.warning(f"压缩 LLM 调用失败(第{attempt + 1}次): {e}")
                if attempt == 0:
                    time.sleep(1)
        return None
    except Exception as e:
        logger.warning(f"压缩 LLM 调用异常: {e}")
        return None


def _parse_llm_json(text: str | None) -> dict:
    """解析 LLM 输出的 JSON（剥 markdown 围栏、定位平衡 {}，缺字段取默认）"""
    default = {"facts": [], "summary": ""}
    if not text:
        return default
    try:
        t = text.strip()
        # 剥 ```json ... ``` 围栏
        if t.startswith("```"):
            t = t.split("\n", 1)[-1]
            if t.endswith("```"):
                t = t[:-3].rstrip()
        t = t.strip()
        # 定位平衡 {} 包裹的 JSON
        start = t.find("{")
        if start == -1:
            return default
        depth = 0
        end = -1
        for i in range(start, len(t)):
            if t[i] == "{":
                depth += 1
            elif t[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end == -1:
            return default
        data = json.loads(t[start:end])
        facts_raw = data.get("facts") or []
        if not isinstance(facts_raw, list):
            facts_raw = []
        facts = [str(f).strip()[:50] for f in facts_raw if str(f).strip()]
        summary = str(data.get("summary") or "").strip()[:200]
        return {"facts": facts, "summary": summary}
    except Exception as e:
        logger.warning(f"解析 LLM JSON 失败: {e}")
        return default


# ===== 防抖 =====

def is_pending(user_id: str) -> bool:
    with _pending_lock:
        return user_id in _pending


def add_pending(user_id: str) -> bool:
    """标记该用户在压缩中；若已存在返回 False（防抖）"""
    with _pending_lock:
        if user_id in _pending:
            return False
        _pending.add(user_id)
        return True


def remove_pending(user_id: str):
    with _pending_lock:
        _pending.discard(user_id)


# ===== 压缩主流程 =====

def compress_user_history(user_id: str) -> dict:
    """后台任务入口：压缩该用户窗口之外的最旧一批对话为长期记忆"""
    store = _get_store()
    batch_rounds = int(_get_cfg("COMPRESS_BATCH_ROUNDS", 8))
    try:
        batch = store.get_compress_batch(user_id, batch_rounds * 2)
    except Exception as e:
        logger.warning(f"读取压缩批次失败: {e}")
        return {"compressed": 0, "reason": "read_error"}

    if not batch:
        return {"compressed": 0, "reason": "no_batch"}

    # 组对话文本
    lines = []
    for msg in batch:
        speaker = "用户" if msg["role"] == "user" else "助手"
        lines.append(f"{speaker}：{msg['content']}")
    conversation = "\n".join(lines)

    prompt = _load_prompt()
    result = _call_llm([
        {"role": "system", "content": prompt},
        {"role": "user", "content": conversation},
    ])
    parsed = _parse_llm_json(result)

    if not parsed["facts"] and not parsed["summary"]:
        # LLM 无有效输出：不标记 compressed，批次保留待下次调度重试，
        # 避免「压缩失败却标记完成」导致旧对话被静默丢弃
        logger.warning(
            f"[记忆压缩] 用户 {user_id}: LLM 无有效输出，批次保留待重试"
        )
        return {"compressed": 0, "facts": 0, "summary": "", "reason": "llm_no_output"}

    # 批次全部来自同一会话时记录来源，便于追溯
    source_session = ""
    sessions = {m.get("session_id", "") for m in batch if m.get("session_id")}
    if len(sessions) == 1:
        source_session = next(iter(sessions))

    for fact in parsed["facts"]:
        store.save_long_term(user_id, "fact", fact, source_session)
    if parsed["summary"]:
        store.save_long_term(user_id, "summary", parsed["summary"], source_session)

    store.mark_compressed([m["id"] for m in batch])

    stats = store.get_long_term_stats(user_id)
    logger.info(
        f"[记忆压缩] 用户 {user_id}: 压缩 {len(batch)} 条 → "
        f"{len(parsed['facts'])} 条事实 + {len(parsed['summary'])} 字摘要 "
        f"(长期记忆累计 {stats['total']})"
    )
    return {
        "compressed": len(batch),
        "facts": len(parsed["facts"]),
        "summary_len": len(parsed["summary"]),
    }
