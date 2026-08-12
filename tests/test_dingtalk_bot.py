"""钉钉机器人体验改进测试：秒回判断 / 提示文案 / 错误码"""

import asyncio
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from skills.dingtalk_bot import (
    ErrorQueryHandler,
    _is_fast_operation, PENDING_HINT_TEXT, ERROR_MESSAGE, ERROR_CODE_INTERNAL,
)


class FastOperationTests(unittest.TestCase):
    """秒回判断：快速操作不提示，慢操作（Agent）提示"""

    def test_fault_code_is_fast(self):
        """精确故障代码 → 秒回，不提示"""
        self.assertTrue(_is_fast_operation("d4-1"))
        self.assertTrue(_is_fast_operation("查一下 df-8"))

    def test_pcb_calc_is_fast(self):
        """PCB 计算 → 秒回，不提示"""
        self.assertTrue(_is_fast_operation("3A 1oz 外层走线要多宽"))
        self.assertTrue(_is_fast_operation("50Ω 微带 阻抗多少"))

    def test_review_commands_are_fast(self):
        """审核指令 / 审核口令 → 秒回"""
        self.assertTrue(_is_fast_operation("查看我的审核ID"))
        self.assertTrue(_is_fast_operation("同意同步 ABC12345"))
        self.assertTrue(_is_fast_operation("拒绝同步 ABC12345 内容不对"))

    def test_agent_chat_is_slow(self):
        """普通聊天 / 复杂问题 → 走 Agent，需要提示"""
        self.assertFalse(_is_fast_operation("你好"))
        self.assertFalse(_is_fast_operation("今天天气怎么样"))
        self.assertFalse(_is_fast_operation("我这个逆变器报错了怎么回事"))

    def test_empty_is_not_fast(self):
        self.assertFalse(_is_fast_operation(""))

    def test_v1102_commands_are_fast(self):
        """v1.10.2 我的文件/删除/管理员切换为秒回；学习保留提示（可能走 MinerU）"""
        self.assertTrue(_is_fast_operation("我的文件"))
        self.assertTrue(_is_fast_operation("查看我的文件"))
        self.assertTrue(_is_fast_operation("删除学习 1"))
        self.assertTrue(_is_fast_operation("删掉 技术协议.pdf"))
        self.assertTrue(_is_fast_operation("ENTARBOSS"))
        self.assertTrue(_is_fast_operation("退出管理员"))
        self.assertTrue(_is_fast_operation("查看全部文件"))
        self.assertFalse(_is_fast_operation("帮我学习"))
        self.assertFalse(_is_fast_operation("重新学习 1"))


class HintConstantsTests(unittest.TestCase):
    """提示文案与错误码存在性"""

    def test_hint_texts_defined(self):
        self.assertTrue(PENDING_HINT_TEXT)
        self.assertIn("请稍候", PENDING_HINT_TEXT)

    def test_error_message_format(self):
        msg = ERROR_MESSAGE.format(code=ERROR_CODE_INTERNAL)
        self.assertIn("1000", msg)
        self.assertIn("错误码", msg)


def _make_text_msg(text: str, user_id: str = "u1", staff_id: str = "s1") -> mock.MagicMock:
    """构造一条钉钉文本消息（mock CallbackMessage，只用到 .data）"""
    msg = mock.MagicMock()
    msg.data = {
        "senderNick": "测试",
        "conversationTitle": "单聊",
        "senderId": user_id,
        "senderStaffId": staff_id,
        "senderCorpId": "c1",
        "msgtype": "text",
        "text": {"content": text},
    }
    return msg


class ProcessTextRoutingTests(unittest.TestCase):
    """_process_text 同步方法的路由逻辑（审核口令 / 审核消息 / 技能 / 兜底）"""

    def setUp(self):
        self.handler = ErrorQueryHandler()

    def test_review_identity(self):
        """审核口令 → review_identity 分支"""
        result = self.handler._process_text("查看我的审核ID", "u1", "staff1")
        self.assertEqual(result["source"], "review_identity")
        self.assertIn("staff1", result["answer"])

    def test_review_identity_no_staff_id(self):
        result = self.handler._process_text("我的审核ID", "u1", "")
        self.assertEqual(result["source"], "review_identity")
        self.assertIn("没有携带", result["answer"])

    @mock.patch("knowledge_review.handle_review_message")
    def test_review_message_branch(self, mock_review):
        """审核消息 → knowledge_review 分支"""
        mock_review.return_value = "已同意同步 ABC123"
        result = self.handler._process_text("同意同步 ABC123", "u1", "s1")
        self.assertEqual(result["source"], "knowledge_review")
        self.assertEqual(result["answer"], "已同意同步 ABC123")

    @mock.patch("skills.get_matched_skill")
    def test_skill_routing(self, mock_get_skill):
        """技能匹配 → 调用对应 skill.handle 并透传 user_id"""
        fake = mock.MagicMock()
        fake.name = "测试技能"
        fake.handle.return_value = {"answer": "技能回复", "source": "fake"}
        mock_get_skill.return_value = fake
        result = self.handler._process_text("随便问问", "u1", "s1")
        self.assertEqual(result["answer"], "技能回复")
        fake.handle.assert_called_once_with("随便问问", user_id="u1", on_chunk=None)

    @mock.patch("skills.get_matched_skill")
    def test_fallback(self, mock_get_skill):
        """无匹配技能 → fallback 兜底"""
        mock_get_skill.return_value = None
        result = self.handler._process_text("xxx", "u1", "s1")
        self.assertEqual(result["source"], "fallback")
        self.assertIn("无法处理", result["answer"])

    # ===== v1.10.2 上传学习 / 我的文件 / 删除 / 管理员模式 =====

    def test_learn_regex_positive_and_negative(self):
        """「帮我学习」正则：锚定排除带宾语场景"""
        from skills.dingtalk_bot import _is_learn_command
        for ok in ("帮我学习", "学习", "学习一下", "入库", "帮我入库",
                   "学习这个文件", "帮我学习！"):
            self.assertTrue(_is_learn_command(ok), ok)
        for no in ("我想学习英语", "学习记录查询", "我的学习",
                   "学习计划怎么做", "帮我学习一下PCB", "今天学习了吗"):
            self.assertFalse(_is_learn_command(no), no)

    @mock.patch("knowledge_review.learn_file_for_user")
    def test_learn_command_routes_to_knowledge_learn(self, mock_learn):
        """「帮我学习」→ knowledge_learn 分支，且必须优先于技能"""
        mock_learn.return_value = {"status": "ok", "file_name": "a.pdf",
                                   "collection": "standards", "chunk_count": 5}
        with mock.patch("skills.get_matched_skill") as mock_skill:
            fake = mock.MagicMock()
            fake.name = "fake"
            fake.handle.return_value = {"answer": "技能", "source": "fake"}
            mock_skill.return_value = fake
            result = self.handler._process_text("帮我学习", "u1", "s1")
        self.assertEqual(result["source"], "knowledge_learn")
        self.assertIn("学习完成", result["answer"])
        mock_learn.assert_called_once_with("u1")
        fake.handle.assert_not_called()

    @mock.patch("knowledge_review.learn_file_for_user")
    def test_learn_no_file_hint(self, mock_learn):
        """没有待学习文件 → 引导先发文件"""
        mock_learn.return_value = {"status": "no_file",
                                   "message": "当前没有待学习的文件"}
        result = self.handler._process_text("帮我学习", "u1", "s1")
        self.assertIn("请先发送文件", result["answer"])

    @mock.patch("knowledge_review.list_files_for_user")
    def test_my_files_routes(self, mock_list):
        """「我的文件」→ my_files 分支，限本人文件"""
        mock_list.return_value = [{"file_name": "a.pdf", "sync_status": "synced"}]
        result = self.handler._process_text("我的文件", "u1", "s1")
        self.assertEqual(result["source"], "my_files")
        self.assertIn("a.pdf", result["answer"])
        self.assertIn("已学习", result["answer"])
        mock_list.assert_called_once_with("u1", is_admin=False)

    @mock.patch("knowledge_review.list_files_for_user")
    def test_list_all_requires_admin(self, mock_list):
        """「查看全部文件」非管理员 → 拒绝且不查询"""
        result = self.handler._process_text("查看全部文件", "u1", "s1")
        self.assertEqual(result["source"], "my_files")
        self.assertIn("仅管理员", result["answer"])
        mock_list.assert_not_called()

    @mock.patch("knowledge_review.delete_file_for_user")
    def test_delete_command_routes(self, mock_delete):
        """「删除学习 序号」→ 先建立待确认操作，不直接删除"""
        mock_delete.return_value = {"status": "ok", "file_name": "a.pdf",
                                    "deleted_chunks": 2, "source_deleted": True}
        result = self.handler._process_text("删除学习 1", "u1", "s1")
        self.assertEqual(result["source"], "knowledge_delete")
        self.assertIn("回复「确认」", result["answer"])
        mock_delete.assert_not_called()
        from tools import cancel_pending_operation
        cancel_pending_operation("u1")

    @mock.patch("knowledge_review.relearn_file_for_user")
    def test_relearn_command_routes(self, mock_relearn):
        """「重新学习 X」→ 先建立待确认操作，不直接覆盖索引"""
        mock_relearn.return_value = {"status": "ok", "file_name": "a.pdf",
                                     "collection": "standards", "chunk_count": 3}
        result = self.handler._process_text("重新学习 1", "u1", "s1")
        self.assertEqual(result["source"], "knowledge_relearn")
        self.assertIn("回复「确认」", result["answer"])
        mock_relearn.assert_not_called()
        from tools import cancel_pending_operation
        cancel_pending_operation("u1")

    def test_admin_mode_enter_and_exit(self):
        """ENTARBOSS 口令进入管理员模式，可任意删改；退出后失效"""
        from skills.dingtalk_bot import _admin_sessions, _is_admin
        _admin_sessions.discard("admin1")
        try:
            result = self.handler._process_text("ENTARBOSS", "admin1", "s1")
            self.assertEqual(result["source"], "admin_mode")
            self.assertIn("管理员", result["answer"])
            self.assertTrue(_is_admin("admin1"))
            self.assertFalse(_is_admin("nobody"))

            result2 = self.handler._process_text("退出管理员", "admin1", "s1")
            self.assertEqual(result2["source"], "admin_mode")
            self.assertFalse(_is_admin("admin1"))
        finally:
            _admin_sessions.discard("admin1")


class ProcessToThreadTests(unittest.IsolatedAsyncioTestCase):
    """process() 将慢操作放行到 asyncio.to_thread，不阻塞事件循环"""

    def _handler(self):
        handler = ErrorQueryHandler()
        # 屏蔽网络/SDK/记忆副作用
        handler._sync_user_info = mock.MagicMock()
        handler.reply_text = mock.MagicMock()
        handler.reply_markdown = mock.MagicMock()
        return handler

    def _to_thread_calls(self, m, target):
        """从 to_thread 调用记录中筛出放行指定方法（可调用对象）的调用"""
        return [
            c for c in m.call_args_list
            if c.args and c.args[0] == target
        ]

    async def test_text_message_runs_in_to_thread(self):
        handler = self._handler()
        msg = _make_text_msg("你好")
        with mock.patch(
            "asyncio.to_thread",
            new=mock.AsyncMock(return_value={"answer": "回复", "source": "x"}),
        ) as m, mock.patch("skills.memory.add"):
            code, status = await handler.process(msg)
            # 处理逻辑放行到 to_thread（_process_text 是主要放行对象）
            proc_calls = self._to_thread_calls(m, handler._process_text)
            self.assertEqual(len(proc_calls), 1)
            self.assertEqual(proc_calls[0].args[1], "你好")
        self.assertEqual(str(code), "200")

    async def test_text_message_to_thread_passes_ids(self):
        handler = self._handler()
        msg = _make_text_msg("问题", user_id="web_x", staff_id="staff9")
        with mock.patch(
            "asyncio.to_thread",
            new=mock.AsyncMock(return_value={"answer": "ok", "source": "x"}),
        ) as m, mock.patch("skills.memory.add"):
            await handler.process(msg)
        proc_calls = self._to_thread_calls(m, handler._process_text)
        self.assertEqual(len(proc_calls), 1)
        args = proc_calls[0].args
        self.assertEqual(args[1], "问题")
        self.assertEqual(args[2], "web_x")
        self.assertEqual(args[3], "staff9")

    async def test_text_reply_and_memory_also_in_to_thread(self):
        """文本回复与记忆写入也放线程池，避免阻塞事件循环（v1.6.1 → 普通 markdown 回复）"""
        handler = self._handler()

        msg = _make_text_msg("你好")
        with mock.patch(
            "asyncio.to_thread",
            new=mock.AsyncMock(return_value={"answer": "回复", "source": "x"}),
        ) as m, mock.patch("skills.memory.add") as mem_add:
            await handler.process(msg)
        # 普通 markdown 回复：reply_markdown 应通过 to_thread 调用
        md_calls = self._to_thread_calls(m, handler.reply_markdown)
        self.assertEqual(len(md_calls), 1)
        self.assertEqual(md_calls[0].args[1], "恩特小助手")  # title
        self.assertIn("回复", md_calls[0].args[2])  # answer content
        # memory.add 作为可调用对象传入 to_thread（不直接执行；to_thread 被 patch 时不真正执行函数）
        # to_thread 调用结构：(func, user_id, role, content)
        mem_calls = self._to_thread_calls(m, mem_add)
        self.assertEqual(len(mem_calls), 2)
        self.assertEqual(mem_calls[0].args[2], "user")  # 角色
        self.assertEqual(mem_calls[0].args[3], "你好")  # user 消息内容

    async def test_file_message_runs_in_to_thread(self):
        handler = self._handler()
        msg = mock.MagicMock()
        msg.data = {
            "senderNick": "测试", "conversationTitle": "单聊",
            "senderId": "u1", "senderStaffId": "s1", "senderCorpId": "c1",
            "msgtype": "file",
            "extensions": {"content": {"fileName": "a.pdf", "downloadCode": "x"}},
        }
        with mock.patch(
            "asyncio.to_thread",
            new=mock.AsyncMock(return_value=("200", "ok")),
        ) as m:
            code, status = await handler.process(msg)
            file_calls = self._to_thread_calls(m, handler._handle_file_message)
            self.assertEqual(len(file_calls), 1)
        self.assertEqual(code, "200")


class LLMConcurrencyTests(unittest.TestCase):
    """agent.py 的 DeepSeek 并发限流（v1.6.0 多人并发）"""

    def test_semaphore_capacity_equals_config(self):
        """信号量容量 = MAX_CONCURRENT_LLM（默认 20）"""
        from skills import agent
        self.assertIsInstance(agent._LLM_SEMAPHORE, threading.BoundedSemaphore)
        cap = 0
        while agent._LLM_SEMAPHORE.acquire(blocking=False):
            cap += 1
        for _ in range(cap):
            agent._LLM_SEMAPHORE.release()
        self.assertEqual(cap, agent.MAX_CONCURRENT_LLM)

    def test_concurrent_llm_calls_limited(self):
        """并发调用 _call_deepseek 时，同时进行的请求数 ≤ 信号量容量"""
        from skills import agent
        old_sem, old_post, old_key = (
            agent._LLM_SEMAPHORE, agent._HTTP_CLIENT.post, agent.DEEPSEEK_API_KEY,
        )
        agent._LLM_SEMAPHORE = threading.BoundedSemaphore(2)
        agent.DEEPSEEK_API_KEY = "test-key"
        try:
            active, peak, lock = 0, 0, threading.Lock()

            def fake_post(*a, **k):
                nonlocal active, peak
                with lock:
                    active += 1
                    peak = max(peak, active)
                time.sleep(0.2)
                with lock:
                    active -= 1
                return mock.MagicMock(
                    status_code=200,
                    json=lambda: {"choices": [{"message": {"content": "hi"}}]},
                )

            agent._HTTP_CLIENT.post = mock.MagicMock(side_effect=fake_post)

            results = []
            def worker():
                results.append(
                    agent._call_deepseek([{"role": "user", "content": "x"}])
                )

            threads = [threading.Thread(target=worker) for _ in range(6)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            # 6 路并发，同时进行的请求峰值被限制在 2
            self.assertLessEqual(peak, 2)
            self.assertEqual(len(results), 6)  # 全部正常返回，无报错
        finally:
            agent._LLM_SEMAPHORE = old_sem
            agent._HTTP_CLIENT.post = old_post
            agent.DEEPSEEK_API_KEY = old_key


class _SlowSkill:
    """模拟慢操作技能（LLM/检索耗时场景），sleep 0.5s"""
    name = "慢技能"
    priority = 0

    @classmethod
    def handle(cls, text, **kw):
        time.sleep(0.5)
        return {"answer": "慢回复", "source": "slow"}


class PerUserSerializationTests(unittest.IsolatedAsyncioTestCase):
    """同一用户串行处理、不同用户并行处理（回复不乱序）"""

    def setUp(self):
        # 清空每用户锁，避免跨测试复用绑定旧事件循环的锁
        from skills.dingtalk_bot import _user_locks
        _user_locks.clear()

    def _handler(self):
        handler = ErrorQueryHandler()
        handler._sync_user_info = mock.MagicMock()
        handler.reply_text = mock.MagicMock()
        handler.reply_markdown = mock.MagicMock()
        return handler

    async def test_same_user_serialized(self):
        """同一用户两条慢消息并发提交 → 串行（总耗时 ≥ 2×0.5s）"""
        handler = self._handler()
        t0 = time.time()

        # 注意：patch 必须在 gather 外层统一做一次——并发协程内各自 patch
        # 同一属性会因恢复顺序竞态泄漏 mock（见 v1.6.0 修复记录）
        with mock.patch("skills.get_matched_skill", return_value=_SlowSkill), \
             mock.patch("knowledge_review.handle_review_message", return_value=None), \
             mock.patch("skills.memory.add"):
            async def run(text):
                await handler.process(_make_text_msg(text, user_id="same_user"))

            await asyncio.gather(run("慢1"), run("慢2"))
        elapsed = time.time() - t0
        # 串行：两条各 0.5s → ≥ 0.9s（若并发放行会 ≈0.5s，回复可能乱序）
        self.assertGreaterEqual(elapsed, 0.9)

    async def test_different_users_parallel(self):
        """不同用户两条慢消息并发提交 → 并行（总耗时 ≈ 0.5s）"""
        handler = self._handler()
        t0 = time.time()

        with mock.patch("skills.get_matched_skill", return_value=_SlowSkill), \
             mock.patch("knowledge_review.handle_review_message", return_value=None), \
             mock.patch("skills.memory.add"):
            async def run(text, uid):
                await handler.process(_make_text_msg(text, user_id=uid))

            await asyncio.gather(run("慢1", "userA"), run("慢2", "userB"))
        elapsed = time.time() - t0
        # 并行：≈ 0.5s（若误串行会 ≈1.0s）
        self.assertLess(elapsed, 0.9)

    async def test_lock_rebuilt_after_event_loop_reconnect(self):
        """钉钉断线重连（事件循环重建）后，同用户锁自动重建，不再跨循环复用（v1.6.1）

        修复前：_user_locks 存裸 asyncio.Lock，旧循环中发生过竞争的锁绑定旧循环，
        重连后新循环同用户并发会抛 RuntimeError "bound to a different event loop"。
        修复后：按 (loop, lock) 存储，检测到运行循环变化自动重建锁。
        """
        from skills import dingtalk_bot
        dingtalk_bot._user_locks.clear()
        handler = self._handler()

        # 第一个事件循环：让锁发生竞争（绑定到循环 A）
        async def use_lock_in_loop_a():
            async with handler._get_user_lock("same_user"):
                await asyncio.sleep(0.2)
            async with handler._get_user_lock("same_user"):
                await asyncio.sleep(0.05)

        await use_lock_in_loop_a()
        entry_a = dingtalk_bot._user_locks.get("same_user")
        self.assertIsNotNone(entry_a, "锁应已在循环 A 创建")
        loop_a, lock_a = entry_a

        # 模拟重连：同一 IsolatedAsyncioTestCase 内循环不变，这里直接验证
        # _get_user_lock 返回的锁绑定当前运行循环（与存储的 loop 一致）
        loop_now = asyncio.get_running_loop()
        self.assertIs(loop_a, loop_now, "存储的 loop 应为当前运行循环")
        self.assertIs(lock_a, handler._get_user_lock("same_user"),
                      "同一循环内复用同一把锁")

    def test_get_user_lock_rebuilds_on_different_loop(self):
        """跨事件循环：_get_user_lock 检测到运行循环变化时重建锁（v1.6.1）

        用两个独立 asyncio.run 模拟钉钉重连（SDK start_forever 每次 asyncio.run
        都是新事件循环）。旧循环锁绑定旧循环，新循环再取锁应返回新锁。
        """
        from skills import dingtalk_bot

        def make_handler():
            h = ErrorQueryHandler()
            h._sync_user_info = mock.MagicMock()
            return h

        handler = make_handler()

        # 循环 A 使用锁（竞争 → 绑定循环 A）
        async def use_in_a():
            dingtalk_bot._user_locks.clear()
            async with handler._get_user_lock("u_cross"):
                await asyncio.sleep(0.05)
            return handler._get_user_lock("u_cross")

        lock_a = asyncio.run(use_in_a())
        entry_a = dingtalk_bot._user_locks.get("u_cross")
        self.assertEqual(entry_a[1], lock_a)

        # 循环 B：直接调用 _get_user_lock（内部用 get_running_loop 检测）
        async def use_in_b():
            return handler._get_user_lock("u_cross")

        lock_b = asyncio.run(use_in_b())
        entry_b = dingtalk_bot._user_locks.get("u_cross")
        self.assertEqual(entry_b[1], lock_b)
        # 跨循环后重建：新锁对象 ≠ 旧锁，且新锁可正常使用（不再报 bound to a different loop）
        self.assertIsNot(lock_b, lock_a, "跨事件循环应重建锁")


class ConfirmLearnBranchTests(unittest.TestCase):
    """v1.11.0 上传后「推荐入库」确认分支"""

    def setUp(self):
        self.handler = ErrorQueryHandler()

    @mock.patch("knowledge_review.get_pending_learn",
                return_value={"file_path": "/x/a.pdf", "file_name": "a.pdf"})
    @mock.patch("knowledge_review.clear_pending_learn")
    @mock.patch("knowledge_review.learn_file_path_for_user",
                return_value={"status": "ok", "file_name": "a.pdf",
                              "collection": "standards", "chunk_count": 5})
    def test_confirm_with_pending_learns(self, m_learn, m_clear, m_get):
        """有 file-pending 时「入库」→ 按路径入库 + 清除 pending"""
        result = self.handler._process_text("入库", "u1", "s1")
        self.assertEqual(result["source"], "knowledge_learn")
        self.assertIn("学习完成", result["answer"])
        m_learn.assert_called_once()
        m_clear.assert_called_once_with("u1")

    @mock.patch("knowledge_review.get_pending_learn", return_value=None)
    @mock.patch("skills.get_matched_skill", return_value=None)
    def test_confirm_without_pending_not_intercepted(self, m_skill, m_get):
        """无 pending → 确认词不被上传分支拦截（继续路由到兜底）"""
        result = self.handler._process_text("确认", "u1", "s1")
        m_get.assert_called_once_with("u1")
        self.assertIsNotNone(result)


if __name__ == "__main__":
    unittest.main()


class ImageMessageRecognitionTests(unittest.TestCase):
    """图片消息识图：成功 / 单张失败降级 / 无 key 跳过 / 未保存"""

    def setUp(self):
        self.handler = ErrorQueryHandler()

    def _image_msg(self, user_id: str = "u1") -> mock.MagicMock:
        msg = mock.MagicMock()
        msg.get_image_list.return_value = ["download-code-1"]
        return msg

    @staticmethod
    def _saved_file():
        return {
            "success": True,
            "file_path": "D:/ENTAR_AGENT/data/uploads/u1/2026-08-07/image_1.png",
            "file_name": "image_1.png",
            "file_size": 100,
            "message": "ok",
        }

    @mock.patch("tools.execute_tool")
    @mock.patch("file_handler.download_and_save_file")
    @mock.patch.object(ErrorQueryHandler, "reply_markdown")
    @mock.patch.object(ErrorQueryHandler, "reply_text")
    @mock.patch("config.DASHSCOPE_API_KEY", "sk-test")
    def test_recognize_success(self, m_reply, m_md, m_save, m_tool):
        """识图成功 → 先发提示，再发含描述的 Markdown"""
        import json
        m_save.return_value = self._saved_file()
        m_tool.return_value = json.dumps({"description": "一张电路板照片"}, ensure_ascii=False)
        self.handler._handle_image_message(self._image_msg(), "u1", "测试")
        m_reply.assert_called_once()  # 先发"正在识别"提示
        m_tool.assert_called_with("describe_image", mock.ANY)
        self.assertEqual(m_md.call_count, 1)
        text = m_md.call_args[1]["text"]
        self.assertIn("一张电路板照片", text)
        self.assertEqual(m_md.call_args[1]["title"], "恩特小助手 - 图片识别")

    @mock.patch("tools.execute_tool")
    @mock.patch("file_handler.download_and_save_file")
    @mock.patch.object(ErrorQueryHandler, "reply_markdown")
    @mock.patch.object(ErrorQueryHandler, "reply_text")
    @mock.patch("config.DASHSCOPE_API_KEY", "sk-test")
    def test_recognize_failure_fallback(self, m_reply, m_md, m_save, m_tool):
        """识图失败 → 降级该张，回复保存确认"""
        import json
        m_save.return_value = self._saved_file()
        m_tool.return_value = json.dumps({"error": "图片识别失败，请稍后重试"}, ensure_ascii=False)
        self.handler._handle_image_message(self._image_msg(), "u1", "测试")
        text = m_md.call_args[1]["text"]
        self.assertIn("识别失败", text)
        self.assertIn("图片已保存", text)
        self.assertEqual(m_md.call_args[1]["title"], "恩特小助手 - 图片接收")

    @mock.patch("tools.execute_tool")
    @mock.patch("file_handler.download_and_save_file")
    @mock.patch.object(ErrorQueryHandler, "reply_markdown")
    @mock.patch.object(ErrorQueryHandler, "reply_text")
    @mock.patch("config.DASHSCOPE_API_KEY", "")
    def test_no_key_skip(self, m_reply, m_md, m_save, m_tool):
        """未配置 key → 跳过识图，回复旧文案，不调识图工具"""
        m_save.return_value = self._saved_file()
        self.handler._handle_image_message(self._image_msg(), "u1", "测试")
        m_tool.assert_not_called()
        text = m_md.call_args[1]["text"]
        self.assertIn("图片已保存", text)

    @mock.patch("tools.execute_tool")
    @mock.patch("file_handler.download_and_save_file")
    @mock.patch.object(ErrorQueryHandler, "reply_markdown")
    @mock.patch.object(ErrorQueryHandler, "reply_text")
    @mock.patch("config.DASHSCOPE_API_KEY", "sk-test")
    def test_save_failed(self, m_reply, m_md, m_save, m_tool):
        """保存失败 → 回复接收失败，不调识图"""
        m_save.return_value = {"success": False, "message": "下载失败"}
        self.handler._handle_image_message(self._image_msg(), "u1", "测试")
        m_tool.assert_not_called()
        self.assertEqual(m_md.call_args[1]["text"], "图片接收失败，请重试")


class FileMessageImageRecognitionTests(unittest.TestCase):
    """v1.11.11 图片当作「文件消息」发也能识图（复用 describe_image 能力）"""

    def setUp(self):
        self.handler = ErrorQueryHandler()

    def _file_msg(self, file_name="image_1.png"):
        msg = mock.MagicMock()
        msg.extensions = {"content": {
            "fileName": file_name, "downloadCode": "x", "fileSize": 100}}
        return msg

    @staticmethod
    def _saved_file(file_name="image_1.png"):
        return {
            "success": True,
            "file_path": f"D:/ENTAR_AGENT/data/uploads/u1/2026-08-07/{file_name}",
            "file_name": file_name,
            "file_size": 100,
            "message": "ok",
        }

    @mock.patch("tools.execute_tool")
    @mock.patch("file_handler.download_and_save_file")
    @mock.patch.object(ErrorQueryHandler, "reply_markdown")
    @mock.patch.object(ErrorQueryHandler, "reply_text")
    @mock.patch("config.DASHSCOPE_API_KEY", "sk-test")
    def test_file_image_recognized(self, m_reply, m_md, m_save, m_tool):
        """图片当文件发 → 调识图，回复含识别内容"""
        import json
        m_save.return_value = self._saved_file()
        m_tool.return_value = json.dumps({"description": "一张电路板照片"}, ensure_ascii=False)
        self.handler._handle_file_message(self._file_msg(), "u1", "测试")
        m_tool.assert_called_with("describe_image", mock.ANY)
        text = m_md.call_args[1]["text"]
        self.assertIn("图片识别", text)
        self.assertIn("一张电路板照片", text)

    @mock.patch("tools.execute_tool")
    @mock.patch("file_handler.download_and_save_file")
    @mock.patch.object(ErrorQueryHandler, "reply_markdown")
    @mock.patch.object(ErrorQueryHandler, "reply_text")
    @mock.patch("config.DASHSCOPE_API_KEY", "")
    def test_file_image_no_key_skip(self, m_reply, m_md, m_save, m_tool):
        """未配置 key → 图片当文件发不调识图，仍提示图片已保存"""
        m_save.return_value = self._saved_file()
        self.handler._handle_file_message(self._file_msg(), "u1", "测试")
        m_tool.assert_not_called()
        text = m_md.call_args[1]["text"]
        self.assertIn("图片已保存", text)

    @mock.patch("tools.execute_tool")
    @mock.patch("knowledge_review.set_pending_learn")
    @mock.patch("file_handler.download_and_save_file")
    @mock.patch.object(ErrorQueryHandler, "reply_markdown")
    @mock.patch.object(ErrorQueryHandler, "reply_text")
    @mock.patch("config.DASHSCOPE_API_KEY", "sk-test")
    def test_non_image_file_not_recognized(self, m_reply, m_md, m_save, m_learn, m_tool):
        """非图片文件（.txt）→ 不调识图，走入库推荐（不误伤普通文件）"""
        m_save.return_value = self._saved_file("说明.txt")
        self.handler._handle_file_message(self._file_msg("说明.txt"), "u1", "测试")
        m_tool.assert_not_called()
        text = m_md.call_args[1]["text"]
        self.assertIn("要不要我把这份文件入库知识库", text)
