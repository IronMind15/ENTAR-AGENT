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

import asyncio
import json
import os
import re
import sys
import logging
import threading
import time

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
PENDING_HINT_IMAGE_RECOGNIZE = "🖼️ 收到图片，正在识别内容，请稍候..."

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
    # v1.10.2：我的文件 / 删除 / 管理员切换为秒回；「帮我学习」「重新学习」
    # 可能走 MinerU 较慢，保留「正在处理」提示（不在此秒回）。
    if (_MY_FILES_RE.match(t) or _DELETE_RE.match(t) or _ADMIN_ENTER_RE.match(t)
            or _ADMIN_EXIT_RE.match(t) or _ADMIN_LIST_ALL_RE.match(t)):
        return True
    try:
        from skills import get_matched_skill
        skill = get_matched_skill(t)
        return bool(skill and getattr(skill, "priority", 0) > FAST_SKILL_PRIORITY)
    except Exception:
        return False


# 每用户处理锁：同一用户消息串行处理（回复不乱序），不同用户各自并行（v1.6.0）
# 存 (loop, lock) 而非裸 lock：钉钉断线重连会重建事件循环（SDK start_forever
# 每次 asyncio.run 都是新循环），旧锁若在旧循环中发生过竞争会绑定旧循环，
# 新循环复用会抛 "bound to a different event loop" —— 按循环存锁，循环变化自动重建（v1.6.1）
# 内部工具用户量小，锁字典不主动清理（单个 asyncio.Lock 内存可忽略）
_user_locks: dict[str, tuple[asyncio.AbstractEventLoop, asyncio.Lock]] = {}

# ===== v1.10.2 上传学习 / 我的文件 / 删除 / 管理员模式 =====
# 管理员会话（内存态，重启失效）。口令由 config.ADMIN_MASTER_CODE 配置，
# 默认 ENTERBOSS，可在 local_config.py 覆盖。
_admin_sessions: set[str] = set()

# 「帮我学习」触发：匹配「帮我学习 / 学习一下 / 入库 / 帮我入库 / 学习这个文件」，
# 锚定首尾，避免「我想学习英语」等带宾语场景误触。
_LEARN_RE = re.compile(
    r"^(?:帮我\s*)?(?:学习|入库)(?:\s*(?:一下|这个文件|这些文件|该文件))?\s*[!！。.]?$"
)
# 「我的文件」：查看自己上传过的文件
_MY_FILES_RE = re.compile(r"^(查看\s*)?(?:我的文件|我的上传|我上传的文件)$")
# 「删除学习 X」/「删除 X」
_DELETE_RE = re.compile(r"^(?:删除|删掉)\s*(?:学习\s*)?(.+?)\s*$")
# 「重新学习 X」/「重学 X」
_RELEARN_RE = re.compile(r"^(?:重新学习|重学)\s*(.+?)\s*$")
# 管理员：进入 / 退出 / 查看全部
_ADMIN_ENTER_RE = re.compile(r"^ENTARBOSS$")
_ADMIN_EXIT_RE = re.compile(r"^(?:退出管理员|退出管理)$")
_ADMIN_LIST_ALL_RE = re.compile(r"^(?:查看全部文件|全部文件)$")


def _is_admin(user_id: str) -> bool:
    return user_id in _admin_sessions


def _is_learn_command(text: str) -> bool:
    return bool(_LEARN_RE.match((text or "").strip()))


_STATUS_LABELS = {
    "synced": "✅ 已学习",
    "pending": "⏳ 待学习",
    "error": "❌ 失败",
}
_COLLECTION_LABELS = {
    "error_codes": "故障代码库",
    "experience_kb": "经验知识库",
    "standards": "标准文档库",
}


def _format_my_files(rows: list[dict], is_admin: bool = False) -> str:
    """格式化「我的文件」/「查看全部文件」列表 + 下一步建议"""
    if not rows:
        return "📭 当前没有文件记录。\n\n💡 发送文件给我，回复「帮我学习」即可直接入库。"
    lines = []
    for i, row in enumerate(rows, start=1):
        name = row.get("file_name", "?")
        status = _STATUS_LABELS.get(row.get("sync_status", ""),
                                    row.get("sync_status", "?"))
        line = f"{i}. {name} {status}"
        if is_admin and row.get("upload_user_name"):
            line += f"（{row['upload_user_name']}）"
        lines.append(line)
        if row.get("sync_status") == "error" and row.get("error_message"):
            lines.append(f"   └ {str(row['error_message'])[:60]}")
    head = "📁 全部文件：" if is_admin else "📁 你上传过的文件："
    lines.append(
        "\n💡 回复「帮我学习」可入库待学习文件；"
        "「重新学习 序号」强制重学；「删除学习 序号」删除（学习内容 + 源文件）。"
    )
    return head + "\n" + "\n".join(lines)


def _format_learn_result(result: dict) -> str:
    """格式化「帮我学习」/「重新学习」结果"""
    status = result.get("status")
    if status == "ok":
        coll = _COLLECTION_LABELS.get(result.get("collection", ""), "标准文档库")
        return (
            f"✅ 学习完成：{result.get('file_name', '')}\n"
            f"已入库到{coll}，共 {result.get('chunk_count', 0)} 块。\n\n"
            f"📁 回复「我的文件」可查看管理。"
        )
    if status == "no_file":
        return (f"📭 {result.get('message', '当前没有待学习的文件')}\n\n"
                f"💡 请先发送文件，再回复「帮我学习」。")
    if status == "denied":
        return f"⛔ {result.get('message', '无权操作')}"
    return f"❌ {result.get('message', '处理失败')}"


def _format_delete_result(result: dict) -> str:
    """格式化「删除学习 X」结果"""
    status = result.get("status")
    if status == "ok":
        source_note = ("源文件已删除；"
                       if result.get("source_deleted") else
                       "源文件删除失败（已保留）；")
        return (f"🗑️ 已删除：{result.get('file_name', '')}\n"
                f"知识库内容 {result.get('deleted_chunks', 0)} 块已移除，"
                f"{source_note}学习记录已清除。")
    if status == "denied":
        return f"⛔ {result.get('message', '无权操作')}"
    return f"❌ {result.get('message', '删除失败')}"


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
        # 方法内含 SQLite 查询，放线程池避免阻塞事件循环（v1.6.1）
        try:
            await asyncio.to_thread(
                self._sync_user_info, user_id, staff_id, corp_id, sender)
        except Exception as sync_err:
            logger.warning(f"同步用户信息异常（不影响主流程）: {sync_err}")

        # ===== 处理文件消息（下载/保存为慢操作，线程池放行不阻塞事件循环） =====
        if bot_msg.message_type == "file":
            logger.info(f"收到文件 [{conv_title}] {sender}")
            async with self._get_user_lock(user_id):
                return await asyncio.to_thread(
                    self._handle_file_message, bot_msg, user_id, sender)

        # ===== 处理图片消息（同上，线程池放行） =====
        if bot_msg.message_type == "picture":
            logger.info(f"收到图片 [{conv_title}] {sender}")
            async with self._get_user_lock(user_id):
                return await asyncio.to_thread(
                    self._handle_image_message, bot_msg, user_id, sender)

        # ===== 处理文本消息（原有逻辑） =====
        if bot_msg.message_type != "text" or not bot_msg.text or not bot_msg.text.content:
            return AckMessage.STATUS_OK, "ok"

        text = bot_msg.text.content.strip()
        logger.info(f"收到消息 [{conv_title}] {sender}: {text}")

        if not text:
            return AckMessage.STATUS_OK, "ok"

        # ---- 反馈指令检测：用户回复 "1" 或 "2" 作为上一条回答的反馈 ----
        if text in ("1", "2"):
            from user_store import get_last_conversation, add_feedback
            last = get_last_conversation(user_id)
            if last:
                rating = "up" if text == "1" else "down"
                add_feedback(user_id, last["query"], last["answer"][:500], "agent", rating)
                fb_text = "收到反馈，感谢！👍" if text == "1" else "收到反馈，我会继续改进！🙏"
                try:
                    await asyncio.to_thread(self.reply_text, fb_text, bot_msg)
                except Exception as fb_err:
                    logger.warning(f"发送反馈回复失败: {fb_err}")
                return AckMessage.STATUS_OK, "ok"

        # ---- 处理消息（整体捕获异常，返回错误码） ----
        # 慢操作（LLM/检索）先回一条提示，避免用户干等
        # 快速操作（故障代码/PCB）通常秒回，无需提示
        is_fast = _is_fast_operation(text)
        if not is_fast:
            try:
                await asyncio.to_thread(self.reply_text, PENDING_HINT_TEXT, bot_msg)
            except Exception:
                pass

        try:
            # 同一用户串行、不同用户并行：在该用户处理锁内放行到线程池，
            # 保证同一用户连发消息的回复不乱序，同时不影响其他用户并发。
            async with self._get_user_lock(user_id):
                # 慢操作（技能匹配 + LLM/检索/审核判断）在线程池执行，不阻塞事件循环。
                # asyncio.to_thread 自动拷贝 contextvars，中心权限隔离依然有效。
                result = await asyncio.to_thread(
                    self._process_text, text, user_id, staff_id)

                answer = result.get("answer", "") or "抱歉，我没有找到相关信息。"
                logger.info(f"处理完成: {answer[:50]}...")

                # 追加反馈提示
                answer_with_feedback = (
                    answer + "\n\n---\n回复 1 = 满意 👍 | 回复 2 = 不满意 👎")

                # 发送回复（普通 markdown）
                await asyncio.to_thread(
                    self.reply_markdown, "恩特小助手", answer_with_feedback, bot_msg)

                # 记录到会话记忆（SQLite 写入，放线程池与处理对齐；v1.6.1）
                try:
                    from skills import memory
                    await asyncio.to_thread(memory.add, user_id, "user", text)
                    await asyncio.to_thread(memory.add, user_id, "assistant", answer)
                except Exception as mem_err:
                    logger.warning(f"记录记忆失败: {mem_err}")

        except Exception as e:
            logger.exception(f"处理钉钉消息异常: {e}")
            try:
                await asyncio.to_thread(
                    self.reply_text,
                    ERROR_MESSAGE.format(code=ERROR_CODE_INTERNAL),
                    bot_msg,
                )
            except Exception:
                pass

        return AckMessage.STATUS_OK, "ok"

    def _get_user_lock(self, user_id: str) -> asyncio.Lock:
        """获取该用户的处理锁（懒创建，随事件循环重建）

        同一用户消息串行处理（回复不乱序），不同用户各自独立（并行）。
        锁在事件循环内使用，仅阻塞同用户后续消息，不影响其他用户并发。

        关键点：asyncio.Lock 在发生竞争（有 waiter）时才绑定当前事件循环。
        钉钉断线重连后 SDK 用全新事件循环，若直接复用旧锁会在竞争时抛
        RuntimeError "bound to a different event loop"。因此按 (loop, lock)
        存储：检测到当前运行循环与锁绑定循环不一致时自动重建锁。
        """
        loop = asyncio.get_running_loop()
        entry = _user_locks.get(user_id)
        if entry is not None and entry[0] is loop:
            return entry[1]
        lock = asyncio.Lock()
        _user_locks[user_id] = (loop, lock)
        return lock

    def _process_text(self, text: str, user_id: str, staff_id: str, on_chunk=None) -> dict:
        """处理文本消息（同步方法，在 to_thread 线程中执行，不阻塞事件循环）

        路由顺序：审核口令 → 审核消息 → 技能匹配 → 备用兜底。
        调用方在 process() 中用 asyncio.to_thread 放行，因此本方法可包含
        任意慢操作（DeepSeek 调用、Chroma 检索、审核判断等）。

        Args:
            on_chunk: 可选 callback(text, status)，用于 AI 卡片流式输出
        """
        # 注入当前发起者 staff_id 到工具上下文（find_employee 敏感字段权限判断用）
        # asyncio.to_thread 会拷贝当前 context，本线程内工具执行能读到
        from tools import set_current_staff_id
        set_current_staff_id(staff_id)

        # 审核口令与普通技能路由
        if text in ("查看我的审核ID", "我的审核ID", "查看我的钉钉ID"):
            answer = (
                f"你的钉钉员工 ID：{staff_id}"
                if staff_id else
                "当前消息没有携带钉钉员工 ID，请确认应用已取得通讯录基础权限。"
            )
            if on_chunk:
                on_chunk(answer, "done")
            return {"answer": answer, "source": "review_identity"}

        from knowledge_review import handle_review_message
        review_answer = handle_review_message(text, staff_id)
        if review_answer is not None:
            if on_chunk:
                on_chunk(review_answer, "done")
            return {"answer": review_answer, "source": "knowledge_review"}

        # ===== v1.10.2 上传学习 / 我的文件 / 删除 / 管理员模式 =====
        from knowledge_review import (
            delete_file_for_user, learn_file_for_user,
            list_files_for_user, relearn_file_for_user,
        )
        t = text.strip()

        # 1. 管理员模式切换（口令 ENTERBOSS，config.ADMIN_MASTER_CODE 可覆盖）
        if _ADMIN_ENTER_RE.match(t):
            _admin_sessions.add(user_id)
            answer = (
                "🔐 已进入管理员模式。\n"
                "你可「查看全部文件」，并可删改任意用户上传的文件。\n"
                "回复「退出管理员」退出。"
            )
            if on_chunk:
                on_chunk(answer, "done")
            return {"answer": answer, "source": "admin_mode"}
        if _ADMIN_EXIT_RE.match(t):
            _admin_sessions.discard(user_id)
            answer = "已退出管理员模式。"
            if on_chunk:
                on_chunk(answer, "done")
            return {"answer": answer, "source": "admin_mode"}

        is_admin = _is_admin(user_id)

        # 2. 管理员查看全部文件
        if _ADMIN_LIST_ALL_RE.match(t):
            answer = ("该功能仅管理员可用，请先发送口令 ENTERBOSS。"
                      if not is_admin else
                      _format_my_files(list_files_for_user(user_id, is_admin=True),
                                       is_admin=True))
            if on_chunk:
                on_chunk(answer, "done")
            return {"answer": answer, "source": "my_files"}

        # 3. 我的文件
        if _MY_FILES_RE.match(t):
            answer = _format_my_files(
                list_files_for_user(user_id, is_admin=is_admin),
                is_admin=is_admin,
            )
            if on_chunk:
                on_chunk(answer, "done")
            return {"answer": answer, "source": "my_files"}

        # 4. 删除学习内容 + 源文件
        m = _DELETE_RE.match(t)
        if m:
            result = delete_file_for_user(user_id, m.group(1), is_admin=is_admin)
            answer = _format_delete_result(result)
            if on_chunk:
                on_chunk(answer, "done")
            return {"answer": answer, "source": "knowledge_delete"}

        # 5. 强制重新学习
        m = _RELEARN_RE.match(t)
        if m:
            result = relearn_file_for_user(user_id, m.group(1), is_admin=is_admin)
            answer = _format_learn_result(result)
            if on_chunk:
                on_chunk(answer, "done")
            return {"answer": answer, "source": "knowledge_relearn"}

        # 6. 帮我学习 → 直接入库（v1.10.2 取消主管审核）
        if _is_learn_command(t):
            result = learn_file_for_user(user_id)
            answer = _format_learn_result(result)
            if on_chunk:
                on_chunk(answer, "done")
            return {"answer": answer, "source": "knowledge_learn"}

        from skills import get_matched_skill

        skill_cls = get_matched_skill(text)
        if skill_cls:
            logger.info(f"  → {skill_cls.name}: {text[:40]}")
            return skill_cls.handle(text, user_id=user_id, on_chunk=on_chunk)

        logger.info(f"  → 备用处理: {text[:40]}")
        fallback = "抱歉，我暂时无法处理这个问题。"
        if on_chunk:
            on_chunk(fallback, "done")
        return {"answer": fallback, "source": "fallback"}

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

            # v1.10.2：取消主管审核 —— 上传后由用户回复「帮我学习」直接入库，
            # 不再登记审核申请。审核代码保留（knowledge_review.py 未删除），
            # 未来恢复部门划分与审核流程时，取消下面注释并恢复 format_file_received_message 的审核文案即可。
            # if result["success"]:
            #     try:
            #         from knowledge_review import queue_review_for_upload
            #         review = queue_review_for_upload(result, user_id, sender)
            #         if review:
            #             result["review_id"] = review["review_id"]
            #     except Exception as review_err:
            #         logger.error(f"创建知识库审核申请失败: {review_err}")

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
        """处理图片消息（下载保存 + 识图）"""
        try:
            # 先回"收到图片"提示，保存 + 识图需要时间
            try:
                self.reply_text(PENDING_HINT_IMAGE_RECOGNIZE, bot_msg)
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
            from file_handler import download_and_save_file

            saved_files = []  # [{"name", "path"}]
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
                    saved_files.append({
                        "name": result.get("file_name", file_name),
                        "path": result.get("file_path", ""),
                    })

            # 识图：已配置 key 时逐张调 describe_image（千问视觉）
            from config import DASHSCOPE_API_KEY
            descriptions = []  # [{"name", "desc", "ok"}]
            if saved_files and DASHSCOPE_API_KEY:
                from tools import execute_tool
                for item in saved_files:
                    if not item["path"]:
                        continue
                    try:
                        raw = execute_tool(
                            "describe_image",
                            {"image_path": item["path"], "question": "请用中文详细描述这张图片的内容"},
                        )
                        parsed = json.loads(raw) if isinstance(raw, str) else (raw or {})
                        if parsed.get("description"):
                            descriptions.append({"name": item["name"], "desc": parsed["description"], "ok": True})
                        else:
                            descriptions.append({"name": item["name"], "desc": parsed.get("error", "识别失败"), "ok": False})
                    except Exception as e:
                        logger.warning(f"识图失败 {item['name']}: {e}")
                        descriptions.append({"name": item["name"], "desc": f"识别异常：{e}", "ok": False})

            # 构建回复消息
            if saved_files:
                if descriptions and any(d["ok"] for d in descriptions):
                    lines = [f"✅ 已收到 {len(saved_files)} 张图片", ""]
                    for d in descriptions:
                        lines.append(f"🖼️ {d['name']}\n📝 {d['desc']}")
                    answer = "\n\n".join(lines)
                    title = "恩特小助手 - 图片识别"
                else:
                    lines = [f"✅ 已收到 {len(saved_files)} 张图片", ""]
                    if descriptions:  # 有识图尝试但全部失败
                        for d in descriptions:
                            lines.append(f"🖼️ {d['name']}\n⚠️ {d['desc']}")
                        lines.append("")
                        lines.append("图片已保存，如需处理请告诉我。")
                    else:  # 未配置 key，未识图
                        for item in saved_files:
                            lines.append(f"🖼️ {item['name']}")
                        lines.append("")
                        lines.append("图片已保存，如需处理请告诉我。")
                    answer = "\n".join(lines)
                    title = "恩特小助手 - 图片接收"
            else:
                answer = "图片接收失败，请重试"
                title = "恩特小助手 - 图片接收"

            # 回复用户
            self.reply_markdown(
                title=title,
                text=answer,
                incoming_message=bot_msg,
            )
            ok_count = len([d for d in descriptions if d["ok"]])
            logger.info(f"图片处理完成（识别成功 {ok_count}/{len(descriptions)} 张）")

        except Exception as e:
            logger.error(f"处理图片消息失败: {e}")
            try:
                self.reply_text(
                    ERROR_MESSAGE.format(code=ERROR_CODE_INTERNAL), bot_msg
                )
            except:
                pass

        return AckMessage.STATUS_OK, "ok"

    def _sync_user_info(self, user_id: str, staff_id: str, corp_id: str, nick: str = ""):
        """同步钉钉用户信息（同步方法，由调用方放线程池执行）

        仅首次或超过 24h 才调用 API，通过 user_store 缓存。
        注意：本方法为普通同步方法（非 async），内部已自带后台线程做 API 同步；
        v1.6.1 起调用方用 asyncio.to_thread 放行外层 SQLite 查询，不再阻塞事件循环。
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
            logger.warning(f"_sync_user_info 异常: {e}")


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
