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

# ===== 用户体验：处理中即时反馈 + 错误码 =====
# 慢操作（走 Agent/LLM 或文件下载）先回一条提示，避免用户干等
PENDING_HINT_TEXT = "⏳ 收到，正在处理中，请稍候..."
PENDING_HINT_FILE = "📎 收到文件，正在处理，请稍候..."
PENDING_HINT_IMAGE = "🖼️ 收到图片，正在保存，请稍候..."

# 错误码体系（出现问题时返回，方便定位排查）
ERROR_CODE_INTERNAL = 1000   # 内部错误
ERROR_CODE_LLM = 1001        # LLM / DeepSeek 调用失败
ERROR_MESSAGE = "❌ 处理出错了（错误码：{code}）。请稍后重试，或联系管理员排查。"

# 高于此优先级的技能为"秒回"快速技能（纯本地，无需"正在处理"提示）
# 故障查询(100)、PCB计算(90) 都是毫秒级；RAG Agent(50) 走 LLM 需要提示
FAST_SKILL_PRIORITY = 50


def _is_fast_operation(text: str) -> bool:
    """判断是否为秒回操作（无需"正在处理"提示）

    秒回 = 审核指令 / 审核口令 / 快速技能（故障精确匹配、PCB计算等纯本地毫秒级）
    慢操作 = 走 RAG Agent（DeepSeek 判断 + 检索 + 生成）
    """
    t = (text or "").strip()
    if t in ("查看我的审核ID", "我的审核ID", "查看我的钉钉ID"):
        return True
    if t.startswith(("同意同步", "拒绝同步")):
        return True
    try:
        from skills import get_matched_skill
        skill = get_matched_skill(t)
        return bool(skill and getattr(skill, "priority", 0) > FAST_SKILL_PRIORITY)
    except Exception:
        return False


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
        staff_id = str(bot_msg.sender_staff_id or "")
        corp_id = str(bot_msg.sender_corp_id or "")

        # ---- 后台同步用户信息（首次或 24h 过期后自动更新） ----
        try:
            self._sync_user_info_async(user_id, staff_id, corp_id, sender)
        except Exception as sync_err:
            logger.warning(f"同步用户信息异常（不影响主流程）: {sync_err}")

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

        # ---- 判断是否为"秒回"操作（无需"正在处理"提示） ----
        is_fast = _is_fast_operation(text)

        # 慢操作（走 Agent/LLM）先回提示，避免用户干等
        if not is_fast:
            try:
                self.reply_text(PENDING_HINT_TEXT, bot_msg)
                logger.info(f"已发送处理提示: {text[:40]}")
            except Exception as hint_err:
                logger.warning(f"发送处理提示失败: {hint_err}")

        # ---- 处理消息（整体捕获异常，返回错误码） ----
        try:
            # 审核口令与普通技能路由
            if text in ("查看我的审核ID", "我的审核ID", "查看我的钉钉ID"):
                answer = (
                    f"你的钉钉员工 ID：{staff_id}"
                    if staff_id else
                    "当前消息没有携带钉钉员工 ID，请确认应用已取得通讯录基础权限。"
                )
                result = {"answer": answer, "source": "review_identity"}
            else:
                from knowledge_review import handle_review_message
                review_answer = handle_review_message(text, staff_id)
                if review_answer is not None:
                    result = {"answer": review_answer, "source": "knowledge_review"}
                else:
                    from skills import get_matched_skill

                    skill_cls = get_matched_skill(text)
                    if skill_cls:
                        logger.info(f"  → {skill_cls.name}: {text[:40]}")
                        result = skill_cls.handle(text, user_id=user_id)
                    else:
                        logger.info(f"  → 备用处理: {text[:40]}")
                        result = {
                            "answer": "抱歉，我暂时无法处理这个问题。",
                            "source": "fallback",
                        }

            answer = result.get("answer", "") or "抱歉，我没有找到相关信息。"

            # 回复 Markdown
            self.reply_markdown(
                title="恩特小助手",
                text=answer,
                incoming_message=bot_msg,
            )
            logger.info(f"回复成功: {answer[:50]}...")

            # 记录到会话记忆
            try:
                from skills import memory
                memory.add(user_id, "user", text)
                memory.add(user_id, "assistant", answer)
            except Exception as mem_err:
                logger.warning(f"记录记忆失败: {mem_err}")

        except Exception as e:
            logger.exception(f"处理钉钉消息异常: {e}")
            try:
                self.reply_text(
                    ERROR_MESSAGE.format(code=ERROR_CODE_INTERNAL),
                    bot_msg,
                )
            except Exception:
                pass

        return AckMessage.STATUS_OK, "ok"

    def _handle_file_message(self, bot_msg, user_id, sender):
        """处理文件消息"""
        try:
            # 先回"收到文件"提示，下载需时间
            try:
                self.reply_text(PENDING_HINT_FILE, bot_msg)
            except Exception:
                pass

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
            from file_handler import download_and_save_file, format_file_received_message

            result = download_and_save_file(
                download_code=download_code,
                file_name=file_name,
                user_id=user_id,
                chatbot_handler=self,
                user_name=sender,
            )

            # 固定审核人测试模式：登记申请并后台主动推送，不阻塞上传回执
            if result["success"]:
                try:
                    from knowledge_review import queue_review_for_upload
                    review = queue_review_for_upload(result, user_id, sender)
                    if review:
                        result["review_id"] = review["review_id"]
                except Exception as review_err:
                    logger.error(f"创建知识库审核申请失败: {review_err}")

            # 构建回复消息（已包含待处理提示）
            answer = format_file_received_message(result, auto_process=True)

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
                self.reply_text(
                    ERROR_MESSAGE.format(code=ERROR_CODE_INTERNAL), bot_msg
                )
            except:
                pass

        return AckMessage.STATUS_OK, "ok"

    def _handle_image_message(self, bot_msg, user_id, sender):
        """处理图片消息"""
        try:
            # 先回"收到图片"提示，保存需时间
            try:
                self.reply_text(PENDING_HINT_IMAGE, bot_msg)
            except Exception:
                pass

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
                self.reply_text(
                    ERROR_MESSAGE.format(code=ERROR_CODE_INTERNAL), bot_msg
                )
            except:
                pass

        return AckMessage.STATUS_OK, "ok"

    def _sync_user_info_async(self, user_id: str, staff_id: str, corp_id: str, nick: str = ""):
        """后台线程同步钉钉用户信息（不阻塞消息处理）

        仅首次或超过 24h 才调用 API，通过 user_store 缓存。
        """
        if not user_id:
            return

        try:
            from user_store import get_store
            store = get_store()

            # 检查是否需要同步（无 staff_id / 超过 24h 未更新）
            user = store.get_user(user_id)
            need_sync = False

            if not user:
                need_sync = True  # 新用户
            elif staff_id and not user.get("staff_id"):
                need_sync = True  # 有 staff_id 但还没同步过
            else:
                # 检查 updated_at 是否超过 24h
                updated = user.get("updated_at", "")
                if updated:
                    try:
                        from datetime import datetime
                        updated_time = datetime.strptime(
                            updated, "%Y-%m-%d %H:%M:%S"
                        )
                        age = (datetime.now() - updated_time).total_seconds()
                        if age > 86400:  # 24h
                            need_sync = True
                    except (ValueError, TypeError):
                        need_sync = True
                else:
                    need_sync = True

            if not need_sync:
                return

            if not staff_id:
                logger.info(f"用户 {user_id[:20]}... 无 staff_id，跳过钉钉同步")
                # 至少存一条基本记录
                store.get_or_create_user(user_id, nick="")
                return

            # 后台线程执行同步（不阻塞消息回复）
            def _do_sync():
                try:
                    store.sync_user_from_dingtalk(user_id, staff_id, nick=nick)
                except Exception as e:
                    logger.warning(f"钉钉同步线程异常: {e}")

            import threading
            t = threading.Thread(target=_do_sync, daemon=True,
                                 name=f"sync-user-{user_id[:8]}")
            t.start()
            logger.info(f"已启动钉钉同步: user={user_id[:20]}... staff={staff_id}")

        except Exception as e:
            logger.warning(f"_sync_user_info_async 异常: {e}")


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
