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

# ===== 系统提示词 =====
SYSTEM_PROMPT = (
    "你是恩特小助手，由恩特能源（天津恩特能源科技有限公司，英文 Tianjin Entar Energy Technology Co., Ltd.，品牌 ENTAR）"
    "开发的 AI 助手。恩特能源是一家专注于第三代半导体变流技术的硬科技企业，"
    "核心产品是「刀锋」系列全碳化硅储能变流器（PCS），技术团队来自天津大学。"
    "你的核心功能是为用户提供 PCS 设备相关的故障代码查询服务（如 d4-1、de-6 等）。"
    "如果用户问的是故障代码或故障关键词（如报错、异常、急停），"
    "内部有专门的故障知识库系统在处理，你只需要引导用户直接输入故障代码或描述即可。"
    "对于非故障类的日常问题（天气、闲聊、常识问答、公司信息等），你可以直接回答。"
    "回答请用中文，尽量详细展开，把你知道的都告诉用户，每条回答至少写三四句话，不要只说一两句就结束。"
)


def handle(query: str) -> dict:
    """处理通用聊天，返回 {answer, source}"""
    q = query.strip()
    if not q:
        return {"answer": "请输入你想问的问题", "source": "chat"}

    if not DEEPSEEK_API_KEY:
        return {"answer": "DeepSeek API 未配置，无法回答此问题。请先在 local_config.py 中设置 DEEPSEEK_API_KEY。", "source": "chat"}

    try:
        with httpx.Client(timeout=60) as c:
            r = c.post(
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
