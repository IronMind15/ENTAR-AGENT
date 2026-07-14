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

logger = logging.getLogger("dingtalk_bot")


class ErrorQueryHandler(ChatbotHandler):
    """处理钉钉机器人消息，支持文本/文件/图片"""

    async def process(self, message: CallbackMessage):
        """
        接收钉钉回调消息 → 根据类型处理 → 回复
        支持：文本、文件、图片
        """
        # 从回调消息数据中解析 ChatbotMessage
        raw_data = message.data
        if not raw_data or not isinstance(raw_data, dict):
            return AckMessage.STATUS_OK, "ok"

        bot_msg = ChatbotMessage.from_dict(raw_data)
        sender = bot_msg.sender_nick or "未知"
        conv_title = bot_msg.conversation_title or "单聊"
        user_id = str(bot_msg.sender_id or sender)

        # ===== 处理文件消息 =====
        if bot_msg.message_type == "file":
            logger.info(f"收到文件 [{conv_title}] {sender}")
            return self._handle_file_message(bot_msg, user_id, sender)

        # ===== 处理图片消息 =====
        if bot_msg.message_type == "picture":
            logger.info(f"收到图片 [{conv_title}] {sender}")
            return self._handle_image_message(bot_msg, user_id, sender)

        # ===== 处理文本消息（原有逻辑） =====
        if bot_msg.message_type != "text" or not bot_msg.text or not bot_msg.text.content:
            return AckMessage.STATUS_OK, "ok"

        text = bot_msg.text.content.strip()
        logger.info(f"收到消息 [{conv_title}] {sender}: {text}")

        if not text:
            return AckMessage.STATUS_OK, "ok"

        # ---- 通过技能注册中心路由 ----
        from skills import get_matched_skill

        skill_cls = get_matched_skill(text)
        if skill_cls:
            logger.info(f"  → {skill_cls.name}: {text[:40]}")
            result = skill_cls.handle(text, user_id=user_id)
        else:
            logger.info(f"  → 备用处理: {text[:40]}")
            result = {"answer": f"抱歉，我暂时无法处理这个问题。", "source": "fallback"}

        answer = result.get("answer", "") or "抱歉，我没有找到相关信息。"

        # 回复 Markdown
        try:
            self.reply_markdown(
                title="恩特小助手",
                text=answer,
                incoming_message=bot_msg,
            )
            logger.info(f"回复成功: {answer[:50]}...")

            # 记录到会话记忆
            try:
                from skills import memory
                memory.add(user_id, "user", text[:500])
                memory.add(user_id, "assistant", answer[:500])
            except Exception as mem_err:
                logger.warning(f"记录记忆失败: {mem_err}")

        except Exception as e:
            logger.error(f"回复钉钉消息失败: {e}")

        return AckMessage.STATUS_OK, "ok"

    def _handle_file_message(self, bot_msg, user_id, sender):
        """处理文件消息"""
        try:
            # 获取文件信息（从 extensions 中提取）
            content = bot_msg.extensions.get("content", {})
            file_name = content.get("fileName", "unknown_file")
            download_code = content.get("downloadCode")
            file_size = content.get("fileSize", 0)

            if not download_code:
                logger.error(f"文件消息缺少 downloadCode: {bot_msg.extensions}")
                self.reply_text("文件接收失败：缺少下载码", bot_msg)
                return AckMessage.STATUS_OK, "ok"

            logger.info(f"  文件: {file_name}, 大小: {file_size} bytes")

            # 下载并保存文件
            from file_handler import download_and_save_file, format_file_received_message, should_auto_process

            result = download_and_save_file(
                download_code=download_code,
                file_name=file_name,
                user_id=user_id,
                chatbot_handler=self,
                user_name=sender,
            )

            # 构建回复消息
            answer = format_file_received_message(result, auto_process=True)

            # 如果是可处理的文件类型，提示用户
            if result["success"] and should_auto_process(file_name):
                answer += f"\n💡 如需将文件入库到知识库，请告诉我。"

            # 回复用户
            self.reply_markdown(
                title="恩特小助手 - 文件接收",
                text=answer,
                incoming_message=bot_msg,
            )
            logger.info(f"文件接收确认已发送: {file_name}")

            # 记录到会话记忆
            try:
                from skills import memory
                if result["success"]:
                    memory.add(user_id, "user", f"[发送文件] {file_name}")
                    memory.add(user_id, "assistant", f"已接收文件 {file_name}")
            except Exception as mem_err:
                logger.warning(f"记录记忆失败: {mem_err}")

        except Exception as e:
            logger.error(f"处理文件消息失败: {e}")
            try:
                self.reply_text("文件处理异常，请稍后重试", bot_msg)
            except:
                pass

        return AckMessage.STATUS_OK, "ok"

    def _handle_image_message(self, bot_msg, user_id, sender):
        """处理图片消息"""
        try:
            # 获取图片下载码列表
            image_list = bot_msg.get_image_list()
            if not image_list:
                logger.error(f"图片消息缺少 downloadCode")
                self.reply_text("图片接收失败：缺少下载码", bot_msg)
                return AckMessage.STATUS_OK, "ok"

            logger.info(f"  收到 {len(image_list)} 张图片")

            # 下载并保存图片
            from file_handler import download_and_save_file, get_upload_dir

            saved_files = []
            for i, download_code in enumerate(image_list):
                file_name = f"image_{i+1}.png"
                result = download_and_save_file(
                    download_code=download_code,
                    file_name=file_name,
                    user_id=user_id,
                    chatbot_handler=self,
                    user_name=sender,
                )
                if result["success"]:
                    saved_files.append(result["file_name"])

            # 构建回复消息
            if saved_files:
                answer = f"✅ 已收到 {len(saved_files)} 张图片\n\n"
                for name in saved_files:
                    answer += f"🖼️ {name}\n"
                answer += f"\n图片已保存，如需处理请告诉我。"
            else:
                answer = "图片接收失败，请重试"

            # 回复用户
            self.reply_markdown(
                title="恩特小助手 - 图片接收",
                text=answer,
                incoming_message=bot_msg,
            )
            logger.info(f"图片接收确认已发送")

        except Exception as e:
            logger.error(f"处理图片消息失败: {e}")
            try:
                self.reply_text("图片处理异常，请稍后重试", bot_msg)
            except:
                pass

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
        logger.warning("请在 local_config.py 中设置 DINGTALK_CLIENT_ID 和 DINGTALK_CLIENT_SECRET")
        return None

    client = create_bot()

    def _run():
        logger.info("钉钉机器人已启动（Stream 模式）")
        client.start_forever()

    thread = threading.Thread(target=_run, daemon=True, name="dingtalk-bot")
    thread.start()
    return thread
