"""
通用聊天技能（托底功能）
当问题与故障无关时，直接与 DeepSeek 对话
"""

import logging
import os
import sys

logger = logging.getLogger("chat")

# 确保 scripts/ 在模块搜索路径中
_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

import httpx

from config import DEEPSEEK_API_KEY

# 共享 HTTP 客户端（复用连接，避免每次建新连接）
_HTTP_CLIENT = httpx.Client(timeout=60)
from skills import BaseSkill, register

# ===== 系统提示词 =====
SYSTEM_PROMPT = (
    "你是恩特小助手，恩特能源（天津恩特能源科技有限公司，品牌 ENTAR）内部使用的 AI 助手。"
    "使用你的人都是公司内部同事（测试、售后、研发、生产等岗位），不是外部产品用户。\n\n"
    "你的核心功能是帮助同事查询 PCS 设备的故障代码（如 d4-1、de-6 等）。"
    "如果同事提到故障代码或相关关键词，系统内部有专门的故障知识库在处理，"
    "你只需要引导他们直接输入故障代码或描述即可。\n\n"
    "对于非故障类的提问（天气、闲聊、常识问答、公司制度、内部流程、日常工作问题等），"
    "你可以直接回答。你是公司内部工具，说话不用太正式，像同事间交流一样自然就好。\n\n"
    "⚠️ 重要约束：不知道的事不要编。"
    "你没有公司内部人员的信息（员工姓名、岗位、联系方式等），"
    "没有公司食堂/周边餐饮的信息，"
    "没有公司非公开资料。"
    "如果同事问到你不知道的，直接说「这个我不清楚，建议问一下相关负责人」。"
    "宁可说不知道，也不要编造同事说过的话或虚构公司内部信息。\n\n"
    "如果同事问的是关于公司产品参数、技术规格、型号等专业问题，"
    "你了解多少就说多少，不清楚的不要瞎编，直接说暂时不知道即可。"
)


def _handle_impl(query: str, user_id: str = "") -> dict:
    """处理通用聊天，返回 {answer, source}

    Args:
        query: 用户问题
        user_id: 用户标识（可选，用于注入记忆上下文）
    """
    q = query.strip()
    if not q:
        return {"answer": "请输入你想问的问题", "source": "chat"}

    if not DEEPSEEK_API_KEY:
        logger.warning("DeepSeek API 未配置，聊天不可用")
        return {"answer": "DeepSeek API 未配置，无法回答此问题。请先在 local_config.py 中设置 DEEPSEEK_API_KEY。", "source": "chat"}

    logger.info(f"聊天问题: {q[:60]}")

    # 构建消息列表（系统提示词 + 可选的历史记忆）
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    # 如果有用户 ID，注入最近对话记忆
    if user_id:
        try:
            from skills import memory
            context = memory.format_context(user_id, max_content=300)
            if context:
                # 把历史对话加在系统提示词后面
                messages[0]["content"] += context
                logger.info(f"已注入 {user_id} 的记忆上下文")
        except Exception as e:
            logger.warning(f"注入记忆失败: {e}")

    messages.append({"role": "user", "content": q})

    try:
        r = _HTTP_CLIENT.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
            json={
                "model": "deepseek-v4-flash",
                "messages": messages,
                "temperature": 0.7,
                "max_tokens": 4000,
            },
        )
        if r.status_code == 200:
            body = r.json()
            if body and body.get("choices"):
                answer = body["choices"][0]["message"]["content"].strip()
                return {"answer": answer, "source": "chat"}
        else:
            logger.warning(f"DeepSeek 聊天返回非 200: {r.status_code}")
            return {"answer": f"抱歉，大模型暂时无响应（状态码：{r.status_code}）", "source": "chat"}
    except Exception as e:
        logger.error(f"聊天调用 DeepSeek 失败: {e}")
        return {"answer": f"抱歉，连接大模型时出错了：{e}", "source": "chat"}

    return {"answer": "抱歉，我暂时无法回答这个问题。", "source": "chat"}


# ===== 注册技能类 =====
@register
class ChatSkill(BaseSkill):
    """通用聊天技能：非故障问题与 DeepSeek 直接对话（托底）"""
    name = "通用聊天"
    description = "非故障问题时与 DeepSeek 直接对话（托底技能，始终匹配）"
    priority = 0  # 最低优先级，作为兜底

    @classmethod
    def match(cls, query: str) -> bool:
        """始终匹配 — 作为兜底技能"""
        return True

    @classmethod
    def handle(cls, query: str, user_id: str = "") -> dict:
        return _handle_impl(query, user_id=user_id)


# 向后兼容：保留模块级 handle 函数
handle = _handle_impl
