"""
钉钉 Stream 模式机器人
接收单聊/群聊消息 → 故障查询 → Markdown 回复

用法：在 main.py 中 import 并调用 start_bot()
需要先在 local_config.py 配置 DINGTALK_CLIENT_ID 和 DINGTALK_CLIENT_SECRET

Stream 模式说明：
  - 我们主动连钉钉（WebSocket 长连接），不需要公网 IP
  - ChatbotHandler.process() 是 async def
  - 但 reply_markdown() / reply_text() 是同步方法，不可 await
"""

import os
import sys
import logging
import threading

# 确保 scripts/ 在模块搜索路径中
_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from dingtalk_stream import (
    DingTalkStreamClient,
    Credential,
    ChatbotHandler,
    ChatbotMessage,
    AckMessage,
)
from dingtalk_stream.frames import CallbackMessage

from config import DINGTALK_CLIENT_ID, DINGTALK_CLIENT_SECRET
from skills.error_query import FAULT_CODE_PATTERN, FAULT_KEYWORDS

logger = logging.getLogger("dingtalk_bot")


class ErrorQueryHandler(ChatbotHandler):
    """处理钉钉机器人消息，调用故障查询"""

    async def process(self, message: CallbackMessage):
        """
        接收钉钉回调消息 → 提取文本 → 故障查询 → 回复 Markdown
        """
        # 从回调消息数据中解析 ChatbotMessage
        raw_data = message.data
        if not raw_data or not isinstance(raw_data, dict):
            return AckMessage.STATUS_OK, "ok"

        bot_msg = ChatbotMessage.from_dict(raw_data)

        # 只处理文本消息
        if bot_msg.message_type != "text" or not bot_msg.text or not bot_msg.text.content:
            return AckMessage.STATUS_OK, "ok"

        # 提取文本
        text = bot_msg.text.content.strip()
        sender = bot_msg.sender_nick or "未知"
        conv_title = bot_msg.conversation_title or "单聊"
        logger.info(f"收到消息 [{conv_title}] {sender}: {text}")

        if not text:
            return AckMessage.STATUS_OK, "ok"

        # ---- 意图路由（与 main.py 保持一致，常量从 error_query import） ----
        from skills import error_query, chat

        has_fault_code = bool(FAULT_CODE_PATTERN.search(text))
        has_fault_kw = any(kw in text.lower() for kw in FAULT_KEYWORDS)

        if has_fault_code or has_fault_kw:
            result = error_query.handle(text)
            logger.info(f"  → error_query: {text[:40]}")
        else:
            result = chat.handle(text)
            logger.info(f"  → chat: {text[:40]}")

        answer = result.get("answer", "") or "抱歉，我没有找到相关信息。"

        # 回复 Markdown（注意：reply_markdown 是同步方法，不要 await）
        self.reply_markdown(
            title="恩特小助手",
            text=answer,
            incoming_message=bot_msg,
        )
        logger.info(f"回复成功: {answer[:50]}...")

        return AckMessage.STATUS_OK, "ok"


def create_bot() -> DingTalkStreamClient:
    """创建并返回钉钉 Stream 客户端（已注册回调）"""
    credential = Credential(
        client_id=DINGTALK_CLIENT_ID,
        client_secret=DINGTALK_CLIENT_SECRET,
    )
    client = DingTalkStreamClient(credential, logger=logger)

    # 注册机器人消息处理器
    handler = ErrorQueryHandler()
    client.register_callback_handler(ChatbotMessage.TOPIC, handler)

    return client


def start_bot() -> threading.Thread | None:
    """后台线程启动钉钉机器人，返回线程对象"""
    if not DINGTALK_CLIENT_ID or not DINGTALK_CLIENT_SECRET:
        logger.warning("钉钉凭证未配置，跳过机器人启动")
        print("  ⚠️  钉钉未配置：请在 local_config.py 中设置 DINGTALK_CLIENT_ID 和 DINGTALK_CLIENT_SECRET")
        return None

    client = create_bot()

    def _run():
        logger.info("钉钉机器人已启动（Stream 模式）")
        print("  ✅ 钉钉机器人已启动（Stream 模式）")
        client.start_forever()

    thread = threading.Thread(target=_run, daemon=True, name="dingtalk-bot")
    thread.start()
    return thread
