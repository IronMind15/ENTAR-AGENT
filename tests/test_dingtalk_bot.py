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
        fake.handle.assert_called_once_with("随便问问", user_id="u1")

    @mock.patch("skills.get_matched_skill")
    def test_fallback(self, mock_get_skill):
        """无匹配技能 → fallback 兜底"""
        mock_get_skill.return_value = None
        result = self.handler._process_text("xxx", "u1", "s1")
        self.assertEqual(result["source"], "fallback")
        self.assertIn("无法处理", result["answer"])


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
        """回复（reply_markdown）与记忆写入（memory.add）也放线程池，避免阻塞事件循环（v1.6.1）"""
        handler = self._handler()
        msg = _make_text_msg("你好")
        with mock.patch(
            "asyncio.to_thread",
            new=mock.AsyncMock(return_value={"answer": "回复", "source": "x"}),
        ) as m, mock.patch("skills.memory.add") as mem_add:
            await handler.process(msg)
        # reply_markdown 作为可调用对象传给了 to_thread（而非在事件循环直接调用）
        # to_thread 调用结构：(func, title, text, incoming_message)，func 是 args[0]
        reply_calls = self._to_thread_calls(m, handler.reply_markdown)
        self.assertEqual(len(reply_calls), 1)
        self.assertEqual(reply_calls[0].args[1], "恩特小助手")  # title
        self.assertEqual(reply_calls[0].args[2], "回复")  # answer/text 透传
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


if __name__ == "__main__":
    unittest.main()
