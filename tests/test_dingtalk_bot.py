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
        handler._sync_user_info_async = mock.MagicMock()
        handler.reply_text = mock.MagicMock()
        handler.reply_markdown = mock.MagicMock()
        return handler

    async def test_text_message_runs_in_to_thread(self):
        handler = self._handler()
        msg = _make_text_msg("你好")
        with mock.patch(
            "asyncio.to_thread",
            new=mock.AsyncMock(return_value={"answer": "回复", "source": "x"}),
        ) as m, mock.patch("skills.memory.add"):
            code, status = await handler.process(msg)
            m.assert_called_once()
            # 放行的是 _process_text 方法（携带 user_id / staff_id）
            self.assertEqual(m.call_args.args[0], handler._process_text)
        handler.reply_markdown.assert_called_once()
        self.assertEqual(str(code), "200")

    async def test_text_message_to_thread_passes_ids(self):
        handler = self._handler()
        msg = _make_text_msg("问题", user_id="web_x", staff_id="staff9")
        with mock.patch(
            "asyncio.to_thread",
            new=mock.AsyncMock(return_value={"answer": "ok", "source": "x"}),
        ) as m, mock.patch("skills.memory.add"):
            await handler.process(msg)
        args = m.call_args.args
        self.assertEqual(args[0], handler._process_text)
        self.assertEqual(args[1], "问题")
        self.assertEqual(args[2], "web_x")
        self.assertEqual(args[3], "staff9")

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
            m.assert_called_once()
            self.assertEqual(m.call_args.args[0], handler._handle_file_message)
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
        handler._sync_user_info_async = mock.MagicMock()
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


if __name__ == "__main__":
    unittest.main()
