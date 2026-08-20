"""看板操作意图 LLM 分类治本（v1.12.9）——口语操作句承接 / 看板上下文放行 / 缓存 / 源增删让位

背景（2026-08-14 袁会荧实测）：口语操作句无「看板」词时正则接不住 → 落 Agent →
LLM 无工具调用编造「将停用」。折中方案：正则快闸保留（确定性指令秒回），LLM 只在
「正则未命中 + 用户在看板上下文」时承接分类，写操作仍走二次确认。

覆盖计划 D 部分：
- 口语操作端到端（「帮我停掉这个」「不是停掉，是删除」→ LLM 兜底分类 → 确认 → 落地）
- 「删除全部数据源」bot 层 gate defer → 技能层 change_sources remove_all（不落 kb_file_manage）
- 看板上下文放行非订阅；非看板上下文不误接；确认词不被 LLM 吞；LLM 短缓存单次调用
"""

import contextlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from skills.dashboard import DashboardSkill  # noqa: E402
from dashboard import subscription_commands as sub_cmd  # noqa: E402
from dashboard.subscription_store import Subscription  # noqa: E402


class _KanbanStoreFixture:
    """临时订阅库 + mock 注入；owner 预置一个启用订阅（doc_1/doc_2 双源）。"""

    def __init__(self, owner="u1"):
        self.owner = owner
        self._fd, self._path = tempfile.mkstemp(suffix=".db")
        os.close(self._fd)
        self._db_path = self._path
        from dashboard.subscription_store import SubscriptionStore
        self.store = SubscriptionStore(db_path=self._path)
        sub = Subscription(
            owner_user_id=owner, owner_staff_id="staff001",
            owner_union_id=owner,
            data_sources=["doc_1", "doc_2"],
            recipients=["staff001"],
        )
        self.sub_id = self.store.create(sub)

    def __enter__(self):
        self._stack = contextlib.ExitStack()
        self._stack.enter_context(
            mock.patch("skills.dashboard.get_subscription_store",
                       return_value=self.store))
        self._stack.enter_context(
            mock.patch("pending_context._pending", {}))
        return self

    def __exit__(self, *exc):
        self._stack.close()
        self.store.close()
        for suffix in ("", "-wal", "-shm"):
            p = self._db_path + suffix
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass


def _clear_classify_cache():
    """LLM 兜底分类短缓存是模块级全局，测试间必须清空，防 mock 返回值串台。"""
    with sub_cmd._classify_cache_lock:
        sub_cmd._classify_cache.clear()


class ParseLlmFallbackTests(unittest.TestCase):
    """parse_subscription_command 的 LLM 兜底分类（A2/A3/A4/A5）"""

    def setUp(self):
        _clear_classify_cache()

    def test_spoken_stop_classified_in_kanban_context(self):
        """看板语境中「帮我停掉这个」（无「看板」词）→ LLM 判 stop 承接"""
        uid = "u-spoken-stop"
        sub_cmd.touch_activity(uid)
        with mock.patch.object(sub_cmd, "_llm_verify_subscription",
                               return_value="stop"):
            r = sub_cmd.parse_subscription_command("帮我停掉这个", {"user_id": uid})
        self.assertEqual(r and r["intent"], "stop")

    def test_spoken_delete_classified_in_kanban_context(self):
        """「不是停掉，是删除」→ LLM 判 delete 承接（纠正句也可）"""
        uid = "u-spoken-del"
        sub_cmd.touch_activity(uid)
        with mock.patch.object(sub_cmd, "_llm_verify_subscription",
                               return_value="delete"):
            r = sub_cmd.parse_subscription_command("不是停掉，是删除", {"user_id": uid})
        self.assertEqual(r and r["intent"], "delete")

    def test_no_kanban_context_not_intercepted(self):
        """从未聊过看板的用户说「删除」→ 不调 LLM、不拦截"""
        uid = "u-fresh"
        with mock.patch.object(sub_cmd, "_llm_verify_subscription",
                               side_effect=AssertionError("不应调用 LLM")):
            r = sub_cmd.parse_subscription_command("删除", {"user_id": uid})
        self.assertIsNone(r)

    def test_kanban_topic_no_user_id_still_works(self):
        """看板词面命中（无 user_id）→ 正则快路径不受 LLM 兜底影响"""
        with mock.patch.object(sub_cmd, "_llm_verify_subscription",
                               side_effect=AssertionError("不应调用 LLM")):
            r = sub_cmd.parse_subscription_command("停掉看板", {})
        self.assertEqual(r and r["intent"], "stop")

    def test_confirm_not_swallowed_by_llm(self):
        """看板上下文中确认词先于 LLM 分类，不触发 LLM（防把确认再送分类）"""
        uid = "u-confirm"
        sub_cmd.set_pending(uid, {"intent": "query"})
        with mock.patch.object(sub_cmd, "_llm_verify_subscription",
                               side_effect=AssertionError("确认词不应走 LLM")):
            r = sub_cmd.parse_subscription_command("确认", {"user_id": uid})
        self.assertIsNone(r)
        sub_cmd.clear_pending(uid)

    def test_contextual_delete_not_swallowed_by_llm(self):
        """「全部删除」属上下文删除承接，不走 LLM（避免与 handle 双处理）"""
        uid = "u-ctxdel"
        sub_cmd.touch_activity(uid)
        with mock.patch.object(sub_cmd, "_llm_verify_subscription",
                               side_effect=AssertionError("上下文删除不应走 LLM")):
            r = sub_cmd.parse_subscription_command("全部删除", {"user_id": uid})
        self.assertIsNone(r)

    def test_cache_single_llm_call_for_implicit_reference(self):
        """无明确任务指代的口语承接，match+handle 双调仍应只问 LLM 一次。"""
        uid = "u-cache"
        sub_cmd.touch_activity(uid)
        with mock.patch.object(sub_cmd, "_llm_verify_subscription",
                               return_value="stop") as llm:
            r1 = sub_cmd.parse_subscription_command("把它停掉吧", {"user_id": uid})
            r2 = sub_cmd.parse_subscription_command("把它停掉吧", {"user_id": uid})
        self.assertEqual(r1 and r1["intent"], "stop")
        self.assertEqual(r2 and r2["intent"], "stop")
        self.assertEqual(llm.call_count, 1)

    def test_llm_classify_release_on_failure(self):
        """LLM 分类失败/判非订阅（返回 None）→ 放行不拦截，不编造意图"""
        uid = "u-release"
        sub_cmd.touch_activity(uid)
        with mock.patch.object(sub_cmd, "_llm_verify_subscription",
                               return_value=None):
            r = sub_cmd.parse_subscription_command("把它停掉吧", {"user_id": uid})
        self.assertIsNone(r)

    def test_kanban_word_message_not_sent_to_llm_fallback(self):
        """含「看板」词但正则未命中的消息（「我想看下看板配置」）→ 不走 LLM 兜底
        （回归护栏：v1.12.6 修复的「看板词句放行」不被 v1.12.9 LLM 兜底误抢）"""
        with mock.patch.object(sub_cmd, "_llm_verify_subscription",
                               side_effect=AssertionError("看板词句不应走 LLM 兜底")):
            r = sub_cmd.parse_subscription_command("我想看下看板配置")
        self.assertIsNone(r)


class HandleLlmFallbackTests(unittest.TestCase):
    """口语操作句端到端：LLM 分类 → 反问确认 → 确认后按真实 store 落地"""

    def setUp(self):
        _clear_classify_cache()

    def test_spoken_stop_end_to_end(self):
        uid = "u-e2e-stop"
        sub_cmd.touch_activity(uid)
        with _KanbanStoreFixture(owner=uid) as fx:
            with mock.patch.object(sub_cmd, "_llm_verify_subscription",
                                   return_value="stop"):
                r = DashboardSkill.handle("帮我停掉这个", user_id=uid)
            self.assertIn("确认", r["answer"])
            self.assertTrue(fx.store.get(fx.sub_id).enabled)  # 未确认前不动
            DashboardSkill.handle("确认", user_id=uid)
            self.assertFalse(fx.store.get(fx.sub_id).enabled)  # 真实停用

    def test_spoken_delete_end_to_end(self):
        uid = "u-e2e-del"
        sub_cmd.touch_activity(uid)
        with _KanbanStoreFixture(owner=uid) as fx:
            with mock.patch.object(sub_cmd, "_llm_verify_subscription",
                                   return_value="delete"):
                r = DashboardSkill.handle("不是停掉，是删除", user_id=uid)
            self.assertIn("确认", r["answer"])
            self.assertIsNotNone(fx.store.get(fx.sub_id))
            DashboardSkill.handle("确认删除", user_id=uid)
            self.assertIsNone(fx.store.get(fx.sub_id))  # 真实删除

    def test_change_sources_remove_all_end_to_end(self):
        """「删除全部数据源」→ LLM 判 change_sources → remove_all 确认 → 源清空"""
        uid = "u-rmall"
        sub_cmd.touch_activity(uid)
        with _KanbanStoreFixture(owner=uid) as fx:
            with mock.patch.object(sub_cmd, "_llm_verify_subscription",
                                   return_value="change_sources"):
                r = DashboardSkill.handle("删除全部数据源", user_id=uid)
            self.assertIn("清空", r["answer"])
            self.assertIn("确认", r["answer"])
            self.assertEqual(fx.store.get(fx.sub_id).data_sources, ["doc_1", "doc_2"])
            DashboardSkill.handle("确认", user_id=uid)
            self.assertEqual(fx.store.get(fx.sub_id).data_sources, [])

    def test_remove_all_no_sources_hint(self):
        """remove_all 但任务本来就没源 → 提示无需清空，不建 pending"""
        uid = "u-rmall-none"
        sub_cmd.touch_activity(uid)
        with _KanbanStoreFixture(owner=uid) as fx:
            sub = fx.store.get(fx.sub_id)
            sub.data_sources = []
            fx.store.update(sub)
            with mock.patch.object(sub_cmd, "_llm_verify_subscription",
                                   return_value="change_sources"):
                r = DashboardSkill.handle("删除全部数据源", user_id=uid)
            self.assertIn("无需清空", r["answer"])
            self.assertIsNone(sub_cmd.get_pending(uid))


class DeleteAllSourcesGateTests(unittest.TestCase):
    """bot 层「删除全部数据源」让位技能层（C）：gate defer → skill 接住，不落 kb_file_manage"""

    def setUp(self):
        _clear_classify_cache()

    def test_gate_defers_in_kanban_context(self):
        from skills.dingtalk_bot import _file_command_gate
        uid = "u-gate-defer"
        sub_cmd.touch_activity(uid)
        g, _ = _file_command_gate("删除全部数据源", uid, "delete", "全部数据源")
        self.assertEqual(g, "defer")

    def test_gate_proceeds_for_file_with_extension(self):
        """带扩展名的明确文件删除照旧 proceed（「删除学习 参数表.xlsx」不误让位）"""
        from skills.dingtalk_bot import _file_command_gate
        uid = "u-gate-ext"
        sub_cmd.touch_activity(uid)
        g, _ = _file_command_gate("删除学习 参数表.xlsx", uid, "delete", "参数表.xlsx")
        self.assertEqual(g, "proceed")

    def test_gate_proceeds_for_no_kanban_context(self):
        """非看板语境照旧 proceed（文件删除原路径不受影响）"""
        from skills.dingtalk_bot import _file_command_gate
        g, _ = _file_command_gate("删除全部数据源", "u-gate-fresh", "delete", "全部数据源")
        self.assertEqual(g, "proceed")

    def test_process_text_delete_all_sources_handled_by_dashboard(self):
        """端到端：bot 收到「删除全部数据源」→ gate defer → 技能层 remove_all 确认，
        不落 knowledge_delete（kb_file_manage 不被调用）"""
        from skills.dingtalk_bot import ErrorQueryHandler
        uid = "u-bot-rmall"
        sub_cmd.touch_activity(uid)
        handler = ErrorQueryHandler()
        with _KanbanStoreFixture(owner=uid) as fx:
            with mock.patch.object(sub_cmd, "_llm_verify_subscription",
                                   return_value="change_sources"):
                with mock.patch("knowledge_review.delete_file_for_user") as mdel:
                    r = handler._process_text("删除全部数据源", uid, "s1")
        self.assertEqual(r["source"], "dashboard")
        self.assertIn("确认", r["answer"])
        mdel.assert_not_called()


class KanbanContextReleaseTests(unittest.TestCase):
    """看板上下文放行非订阅 / 非看板上下文不误接（match 层）"""

    def setUp(self):
        _clear_classify_cache()

    def test_kanban_context_releases_non_subscription(self):
        """看板语境中说「今天天气怎么样」→ LLM 判非订阅 → 放行不接"""
        uid = "u-rel-weather"
        sub_cmd.touch_activity(uid)
        with mock.patch.object(sub_cmd, "_llm_verify_subscription",
                               return_value=None):
            self.assertFalse(DashboardSkill.match("今天天气怎么样", user_id=uid))

    def test_kanban_context_releases_generic_chat(self):
        """看板语境中普通聊天 → 放行不接（不误弹看板帮助）"""
        uid = "u-rel-chat"
        sub_cmd.touch_activity(uid)
        with mock.patch.object(sub_cmd, "_llm_verify_subscription",
                               return_value=None):
            self.assertFalse(DashboardSkill.match("帮我查一下 d4-1 故障", user_id=uid))

    def test_non_kanban_context_not_intercepted(self):
        """从未聊过看板的用户说「删除」→ match False（不误接）"""
        with mock.patch("dashboard.subscription_commands.has_recent_kanban_activity",
                        return_value=False):
            self.assertFalse(DashboardSkill.match("删除", user_id="u-nobody"))


if __name__ == "__main__":
    unittest.main()
