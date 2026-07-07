"""
通用聊天技能（托底功能）
当问题与故障无关时，直接与 DeepSeek 对话
"""

import os
import sys

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
    "你可以直接回答。你是公司内部工具，说话不用太正式，像同事间交流一样自然就好，"
    "但回答仍要有内容，把知道的信息尽量分享出来，每条回答至少写三四句话。\n\n"
    "如果同事问的是关于公司产品参数、技术规格、型号等专业问题，"
    "你了解多少就说多少，不清楚的不要瞎编，直接说暂时不知道即可。"
)


def _handle_impl(query: str) -> dict:
    """处理通用聊天，返回 {answer, source}"""
    q = query.strip()
    if not q:
        return {"answer": "请输入你想问的问题", "source": "chat"}

    if not DEEPSEEK_API_KEY:
        return {"answer": "DeepSeek API 未配置，无法回答此问题。请先在 local_config.py 中设置 DEEPSEEK_API_KEY。", "source": "chat"}

    try:
        r = _HTTP_CLIENT.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
            json={
                "model": "deepseek-v4-flash",
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": q},
                ],
                "temperature": 0.7,
                "max_tokens": 4000,  # 约 3000 中文字，足够回答绝大多数问题
            },
        )
        if r.status_code == 200:
            body = r.json()
            if body and body.get("choices"):
                answer = body["choices"][0]["message"]["content"].strip()
                return {"answer": answer, "source": "chat"}
        else:
            return {"answer": f"抱歉，大模型暂时无响应（状态码：{r.status_code}）", "source": "chat"}
    except Exception as e:
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
    def handle(cls, query: str) -> dict:
        return _handle_impl(query)


# 向后兼容：保留模块级 handle 函数
handle = _handle_impl
