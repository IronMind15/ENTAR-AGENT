"""看板技能测试（v1.11.0）—— 订阅管理对话（mock store/service，不打真实 API）"""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from skills.dashboard import DashboardSkill  # noqa: E402


class MatchTests(unittest.TestCase):
    def test_kanban_topic_matches(self):
        """订阅管理话题 → 技能拦截"""
        for text in ("帮我推个看板", "改看板时间到10点", "每周一和周五推看板",
                     "停掉看板", "也推给张工", "我的看板几点推送"):
            self.assertTrue(DashboardSkill.match(text), text)

    def test_real_time_query_not_match(self):
        """实时查询看板（今天怎么样/什么情况）→ 放给 Agent 调 query_dashboard 工具"""
        with mock.patch("dashboard.subscription_commands.has_recent_kanban_activity",
                        return_value=False):
            for text in ("看板今天怎么样", "看板现在什么情况", "今天有哪些滞后项目"):
                self.assertFalse(DashboardSkill.match(text), text)

    def test_negative_topic_not_match(self):
        for text in ("看板功能方案", "看板文档学习", "看板方法论", "看板怎么用"):
            self.assertFalse(DashboardSkill.match(text), text)

    def test_confirm_word_needs_recent_activity(self):
        with mock.patch("dashboard.subscription_commands.has_recent_kanban_activity",
                        return_value=False):
            # 无近期活动 → 不拦截
            self.assertFalse(DashboardSkill.match("确认"))

    def test_confirm_word_with_recent_activity(self):
        with mock.patch("dashboard.subscription_commands.has_recent_kanban_activity",
                        return_value=True):
            self.assertTrue(DashboardSkill.match("确认"))

    def test_unrelated_does_not_match(self):
        with mock.patch("dashboard.subscription_commands.has_recent_kanban_activity",
                        return_value=False):
            self.assertFalse(DashboardSkill.match("你好"))
            self.assertFalse(DashboardSkill.match("d4-1 是什么故障"))


class HandleCreateTests(unittest.TestCase):
    """创建订阅：反问 → 确认 → 落地 + 推样例"""

    def setUp(self):
        self.patch_pending = mock.patch("dashboard.subscription_commands._pending", {})
        self.patch_pending.start()
        self.addCleanup(self.patch_pending.stop)
        # 用临时订阅存储
        import tempfile, os
        from dashboard.subscription_store import SubscriptionStore
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._db_path = path
        self._store = SubscriptionStore(db_path=path)
        self.patch_store = mock.patch(
            "skills.dashboard.get_subscription_store",
            return_value=self._store)
        self.patch_store.start()
        self.addCleanup(self.patch_store.stop)
        self.addCleanup(self._cleanup_db)

    def _cleanup_db(self):
        self._store.close()
        for suffix in ("", "-wal", "-shm"):
            p = self._db_path + suffix
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

    @mock.patch("skills.dashboard.DashboardSkill._staff_id_of", return_value="staff001")
    def test_create_flow(self, mock_staff):
        # 1. 说「帮我推个看板」→ 反问
        r = DashboardSkill.handle("帮我推个看板", user_id="union001")
        self.assertEqual(r["source"], "dashboard")
        self.assertIn("确认", r["answer"])
        self.assertIn("研发项目现况表", r["answer"])
        # 2. 回复「确认」→ 落地 + 推样例（mock push）
        with mock.patch("skills.dashboard.DashboardSkill._push_sample",
                        return_value="已推送示例看板") as m_push:
            r2 = DashboardSkill.handle("确认", user_id="union001")
        self.assertIn("开通", r2["answer"])
        m_push.assert_called_once()
        subs = self._store.list_for_owner("union001")
        self.assertEqual(len(subs), 1)
        self.assertTrue(subs[0].enabled)
        self.assertEqual(subs[0].owner_union_id, "union001")
        self.assertEqual(subs[0].owner_staff_id, "staff001")
        self.assertIn("staff001", subs[0].recipients)

    @mock.patch("skills.dashboard.DashboardSkill._staff_id_of", return_value="staff001")
    @mock.patch("skills.dashboard.DashboardSkill._push_sample", return_value="")
    def test_create_sets_defaults(self, m_push, mock_staff):
        DashboardSkill.handle("帮我推个看板", user_id="u1")
        DashboardSkill.handle("确认", user_id="u1")
        sub = self._store.list_for_owner("u1")[0]
        self.assertEqual(sub.push_hour, 9)
        self.assertEqual(sub.push_minute, 0)
        self.assertEqual(sub.weekdays, "")
        self.assertEqual(sub.alert_mode, "always")
        self.assertEqual(sub.data_sources, ["project_status", "test_issues"])

    def test_confirm_without_pending(self):
        r = DashboardSkill.handle("确认", user_id="nobody")
        self.assertIn("还没有", r["answer"])


class HandleChangeTests(unittest.TestCase):
    """改时间/频率/接收人：需先有订阅"""

    def setUp(self):
        self.patch_pending = mock.patch("dashboard.subscription_commands._pending", {})
        self.patch_pending.start()
        self.addCleanup(self.patch_pending.stop)
        import tempfile, os
        from dashboard.subscription_store import Subscription, SubscriptionStore
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._db_path = path
        self._store = SubscriptionStore(db_path=path)
        self.patch_store = mock.patch(
            "skills.dashboard.get_subscription_store",
            return_value=self._store)
        self.patch_store.start()
        self.addCleanup(self.patch_store.stop)
        self.addCleanup(self._cleanup_db)
        # 预置一个订阅
        sub = Subscription(owner_user_id="u1", owner_staff_id="staff001",
                           owner_union_id="u1", data_sources=["project_status"],
                           recipients=["staff001"])
        self._sub_id = self._store.create(sub)

    def _cleanup_db(self):
        self._store.close()
        for suffix in ("", "-wal", "-shm"):
            p = self._db_path + suffix
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

    def test_change_time_flow(self):
        r = DashboardSkill.handle("改看板时间到10点", user_id="u1")
        self.assertIn("确认", r["answer"])
        self.assertIn("10:00", r["answer"])
        DashboardSkill.handle("确认", user_id="u1")
        self.assertEqual(self._store.get(self._sub_id).push_hour, 10)

    def test_change_freq_flow(self):
        DashboardSkill.handle("每周一和周五推看板", user_id="u1")
        DashboardSkill.handle("确认", user_id="u1")
        self.assertEqual(self._store.get(self._sub_id).weekdays, "1,5")

    @mock.patch("dashboard.subscription_commands.resolve_recipient",
                side_effect=lambda name: {"张工": "staff_zhang"}[name])
    def test_change_recipient_flow(self, mock_resolve):
        DashboardSkill.handle("也推给张工", user_id="u1")
        DashboardSkill.handle("确认", user_id="u1")
        self.assertIn("staff_zhang", self._store.get(self._sub_id).recipients)

    def test_change_without_subscription(self):
        r = DashboardSkill.handle("改看板时间到10点", user_id="nobody")
        self.assertIn("还没有订阅", r["answer"])


class HandleStopAndQueryTests(unittest.TestCase):
    def setUp(self):
        import tempfile, os
        from dashboard.subscription_store import Subscription, SubscriptionStore
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._db_path = path
        self._store = SubscriptionStore(db_path=path)
        self.patch_store = mock.patch(
            "skills.dashboard.get_subscription_store",
            return_value=self._store)
        self.patch_store.start()
        self.addCleanup(self.patch_store.stop)
        self.addCleanup(self._cleanup_db)
        sub = Subscription(owner_user_id="u1", owner_staff_id="staff001",
                           owner_union_id="u1", data_sources=["project_status"],
                           recipients=["staff001"], push_hour=10)
        self._store.create(sub)

    def _cleanup_db(self):
        self._store.close()
        for suffix in ("", "-wal", "-shm"):
            p = self._db_path + suffix
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

    def test_stop_disables_subscription(self):
        r = DashboardSkill.handle("停掉看板", user_id="u1")
        self.assertIn("已停止", r["answer"])
        subs = self._store.list_for_owner("u1")
        self.assertTrue(all(not s.enabled for s in subs))

    def test_stop_without_subscription(self):
        r = DashboardSkill.handle("停掉看板", user_id="nobody")
        self.assertIn("还没有订阅", r["answer"])

    def test_query_status(self):
        r = DashboardSkill.handle("我的看板几点推送", user_id="u1")
        self.assertIn("10:00", r["answer"])
        self.assertIn("运行中", r["answer"])

    def test_query_without_subscription(self):
        r = DashboardSkill.handle("我的看板几点推送", user_id="nobody")
        self.assertIn("还没有订阅", r["answer"])


class HandleDocCreateTests(unittest.TestCase):
    """「按这几个文档做每日看板」：动态数据源创建 + 确认落地"""

    def setUp(self):
        self.patch_pending = mock.patch("dashboard.subscription_commands._pending", {})
        self.patch_pending.start()
        self.addCleanup(self.patch_pending.stop)
        import tempfile
        from dashboard.subscription_store import SubscriptionStore
        from dashboard.doc_candidates import DocCandidate, DocCandidateStore
        # 订阅存储
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._db_path = path
        self._store = SubscriptionStore(db_path=path)
        self.patch_store = mock.patch("skills.dashboard.get_subscription_store",
                                      return_value=self._store)
        self.patch_store.start()
        self.addCleanup(self.patch_store.stop)
        # 候选存储（预置 2 个可看板文档）
        fd2, path2 = tempfile.mkstemp(suffix=".db")
        os.close(fd2)
        self._cand_path = path2
        self._cands = DocCandidateStore(db_path=path2)
        self.patch_cands = mock.patch("dashboard.doc_candidates.get_candidate_store",
                                      return_value=self._cands)
        self.patch_cands.start()
        self.addCleanup(self.patch_cands.stop)
        self.addCleanup(self._cleanup)
        # v1.11.6：权限预检统一放行（专项测试 PrecheckTests 单独覆盖），
        # 避免 doc_create 触发真实文档读取 API
        self.patch_precheck = mock.patch.object(
            DashboardSkill, "_precheck_doc_candidates",
            side_effect=lambda cands: (cands, []))
        self.patch_precheck.start()
        self.addCleanup(self.patch_precheck.stop)

    def _cleanup(self):
        self._store.close()
        self._cands.close()
        for p in (self._db_path, self._cand_path):
            for suffix in ("", "-wal", "-shm"):
                fp = p + suffix
                if os.path.exists(fp):
                    try:
                        os.remove(fp)
                    except OSError:
                        pass

    def _seed_candidates(self):
        from dashboard.doc_candidates import DocCandidate
        self._cands.add(DocCandidate(
            user_id="u1", url="u1", node_id="n1", kind="notable",
            operator_union="u1", enabled=True, name="研发项目现况表"))
        self._cands.add(DocCandidate(
            user_id="u1", url="u2", node_id="n2", kind="notable",
            operator_union="u1", enabled=True, name="整机测试问题"))

    @mock.patch("skills.dashboard.DashboardSkill._staff_id_of", return_value="staff001")
    @mock.patch("skills.dashboard.DashboardSkill._push_sample", return_value="已推送")
    def test_doc_create_flow(self, m_push, mock_staff):
        self._seed_candidates()
        # 1. 说「按这几个文档做每日看板」→ 反问
        r = DashboardSkill.handle("按这几个文档做每日看板", user_id="u1")
        self.assertEqual(r["source"], "dashboard")
        self.assertIn("确认", r["answer"])
        self.assertIn("研发项目现况表", r["answer"])
        self.assertIn("整机测试问题", r["answer"])
        # 2. 确认 → 落地订阅（data_sources = doc_* keys）
        r2 = DashboardSkill.handle("确认", user_id="u1")
        self.assertIn("开通", r2["answer"])
        subs = self._store.list_for_owner("u1")
        self.assertEqual(len(subs), 1)
        self.assertEqual(sorted(subs[0].data_sources), ["doc_1", "doc_2"])
        self.assertTrue(subs[0].enabled)

    @mock.patch("skills.dashboard.DashboardSkill._staff_id_of", return_value="")
    def test_no_documents_prompts_send_link(self, mock_staff):
        r = DashboardSkill.handle("按这几个文档做每日看板", user_id="u1")
        self.assertIn("发送", r["answer"])
        self.assertIn("文档链接", r["answer"])

    @mock.patch("skills.dashboard.DashboardSkill._staff_id_of", return_value="staff001")
    def test_doc_create_source_candidate_ids_only_takes_specified(self, mock_staff):
        """v1.11.4：传入本次候选 id 时，历史候选不得混入"""
        from dashboard.doc_candidates import DocCandidate
        self._seed_candidates()
        # 历史候选（昨天登记的同一文档，非本次发送）——不应进入本次看板
        self._cands.add(DocCandidate(
            user_id="u1", url="u_old", node_id="n1", kind="notable",
            operator_union="u1", enabled=True, name="旧登记同文档"))
        # 只传本次识别的候选 id 1、2
        r = DashboardSkill._handle_doc_create("u1", source_candidate_ids=[1, 2])
        self.assertIn("研发项目现况表", r["answer"])
        self.assertIn("整机测试问题", r["answer"])
        self.assertNotIn("旧登记同文档", r["answer"])
        from dashboard.subscription_commands import get_pending
        p = get_pending("u1")
        self.assertEqual(sorted(p["data_sources"]), ["doc_1", "doc_2"])
        self.assertEqual(p["alert_mode"], "always")

    @mock.patch("skills.dashboard.DashboardSkill._staff_id_of", return_value="staff001")
    def test_doc_create_without_candidate_ids_falls_back_all(self, mock_staff):
        """缺省 source_candidate_ids → 兜底取全部可看板候选"""
        from dashboard.doc_candidates import DocCandidate
        self._seed_candidates()
        self._cands.add(DocCandidate(
            user_id="u1", url="u3", node_id="n3", kind="notable",
            operator_union="u1", enabled=True, name="历史登记"))
        r = DashboardSkill._handle_doc_create("u1")
        self.assertIn("历史登记", r["answer"])
        from dashboard.subscription_commands import get_pending
        p = get_pending("u1")
        self.assertEqual(sorted(p["data_sources"]), ["doc_1", "doc_2", "doc_3"])


class DocCreatePrecheckTests(unittest.TestCase):
    """创建前权限预检在 _handle_doc_create 的整合（v1.11.6）"""

    def setUp(self):
        self.patch_pending = mock.patch("dashboard.subscription_commands._pending", {})
        self.patch_pending.start()
        self.addCleanup(self.patch_pending.stop)
        import tempfile
        from dashboard.doc_candidates import DocCandidate, DocCandidateStore
        fd2, path2 = tempfile.mkstemp(suffix=".db")
        os.close(fd2)
        self._cand_path = path2
        self._cands = DocCandidateStore(db_path=path2)
        self.patch_cands = mock.patch("dashboard.doc_candidates.get_candidate_store",
                                      return_value=self._cands)
        self.patch_cands.start()
        self.addCleanup(self.patch_cands.stop)
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        self._cands.close()
        for suffix in ("", "-wal", "-shm"):
            p = self._cand_path + suffix
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

    def _seed(self):
        from dashboard.doc_candidates import DocCandidate
        self._cands.add(DocCandidate(
            user_id="u1", url="u1", node_id="n1", kind="notable",
            operator_union="u1", enabled=True, name="研发项目现况表"))

    @mock.patch("skills.dashboard.DashboardSkill._staff_id_of", return_value="staff001")
    def test_all_blocked_prompts_permission(self, mock_staff):
        """全部文档不可读 → 明确提示权限问题，不建订阅"""
        self._seed()
        with mock.patch.object(
                DashboardSkill, "_precheck_doc_candidates",
                return_value=([], [{"name": "研发项目现况表", "reason": "无权限"}])):
            r = DashboardSkill._handle_doc_create("u1")
        self.assertIn("无法读取", r["answer"])
        from dashboard.subscription_commands import get_pending
        self.assertIsNone(get_pending("u1"))   # 未设 pending，不进入确认

    @mock.patch("skills.dashboard.DashboardSkill._staff_id_of", return_value="staff001")
    def test_partial_blocked_keeps_readable(self, mock_staff):
        """部分文档不可读 → 可读的继续，不可读的剔除"""
        from dashboard.doc_candidates import DocCandidate
        self._seed()
        self._cands.add(DocCandidate(
            user_id="u1", url="u2", node_id="n2", kind="notable",
            operator_union="u1", enabled=True, name="整机测试问题"))
        with mock.patch.object(
                DashboardSkill, "_precheck_doc_candidates",
                side_effect=lambda cands: (
                    [c for c in cands if c.node_id == "n1"],
                    [{"name": "整机测试问题", "reason": "无权限"}])):
            r = DashboardSkill._handle_doc_create("u1")
        self.assertIn("研发项目现况表", r["answer"])
        self.assertNotIn("整机测试问题", r["answer"])
        from dashboard.subscription_commands import get_pending
        p = get_pending("u1")
        self.assertEqual(p["data_sources"], ["doc_1"])


class HandleResumeDeleteTests(unittest.TestCase):
    """恢复/删除订阅（v1.11.6）"""

    def setUp(self):
        import tempfile
        from dashboard.subscription_store import Subscription, SubscriptionStore
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._db_path = path
        self._store = SubscriptionStore(db_path=path)
        self.patch_store = mock.patch(
            "skills.dashboard.get_subscription_store",
            return_value=self._store)
        self.patch_store.start()
        self.addCleanup(self.patch_store.stop)
        self.addCleanup(self._cleanup_db)

    def _cleanup_db(self):
        self._store.close()
        for suffix in ("", "-wal", "-shm"):
            p = self._db_path + suffix
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

    def _sub(self, **kw):
        from dashboard.subscription_store import Subscription
        base = Subscription(owner_user_id="u1", owner_staff_id="staff001",
                            owner_union_id="u1", data_sources=["project_status"],
                            recipients=["staff001"])
        for k, v in kw.items():
            setattr(base, k, v)
        return base

    def test_resume_reenables_paused(self):
        """恢复：重新启用已停用订阅，不新建重复"""
        self._store.create(self._sub(enabled=False))
        r = DashboardSkill.handle("恢复看板", user_id="u1")
        self.assertIn("已恢复", r["answer"])
        subs = self._store.list_for_owner("u1")
        self.assertEqual(len(subs), 1)       # 不新建
        self.assertTrue(subs[0].enabled)     # 已启用

    def test_reopen_phrase_resumes_not_duplicates(self):
        """回归：v1.11.5 前「重新开通看板」新建重复订阅，现在应恢复"""
        self._store.create(self._sub(enabled=False))
        r = DashboardSkill.handle("重新开通看板", user_id="u1")
        self.assertIn("已恢复", r["answer"])
        subs = self._store.list_for_owner("u1")
        self.assertEqual(len(subs), 1)
        self.assertTrue(subs[0].enabled)

    def test_resume_nothing_paused(self):
        self._store.create(self._sub())
        r = DashboardSkill.handle("恢复看板", user_id="u1")
        self.assertIn("无需恢复", r["answer"])

    def test_resume_without_subscription(self):
        r = DashboardSkill.handle("恢复看板", user_id="nobody")
        self.assertIn("还没有订阅", r["answer"])

    def test_delete_confirmation_then_removes(self):
        """删除：先反问确认，确认后彻底移除"""
        self._store.create(self._sub())
        r = DashboardSkill.handle("删除看板", user_id="u1")
        self.assertIn("确认", r["answer"])
        self.assertEqual(len(self._store.list_for_owner("u1")), 1)  # 未确认不删
        r2 = DashboardSkill.handle("确认", user_id="u1")
        self.assertIn("已删除", r2["answer"])
        self.assertEqual(self._store.list_for_owner("u1"), [])

    def test_delete_without_subscription(self):
        r = DashboardSkill.handle("删除看板", user_id="nobody")
        self.assertIn("还没有订阅", r["answer"])


class HandleSetRecipientSelfTests(unittest.TestCase):
    """「只推给我自己」→ 明确提示默认即自己（v1.11.5 解析 + v1.11.6 消费）"""

    def test_set_recipient_self(self):
        r = DashboardSkill.handle("看板只推给我自己", user_id="u1")
        self.assertIn("默认就是只推送给您自己", r["answer"])


class PrecheckTests(unittest.TestCase):
    """_precheck_doc_candidates 逻辑：ok=False 剔除、权限错剔除、其他异常放行"""

    @staticmethod
    def _cand(node_id="n1", name="文档A"):
        from dashboard.doc_candidates import DocCandidate
        return DocCandidate(user_id="u1", url=f"u/{node_id}", node_id=node_id,
                            kind="notable", operator_union="u1", enabled=True,
                            name=name)

    def test_all_readable(self):
        cands = [self._cand(), self._cand("n2", "文档B")]
        client = mock.Mock()
        client.read_document.return_value = {"ok": True}
        with mock.patch("dingtalk_doc_client.get_doc_client", return_value=client):
            usable, blocked = DashboardSkill._precheck_doc_candidates(cands)
        self.assertEqual(len(usable), 2)
        self.assertEqual(blocked, [])

    def test_ok_false_blocked(self):
        cands = [self._cand(), self._cand("n2", "文档B")]
        client = mock.Mock()
        client.read_document.side_effect = [
            {"ok": True},
            {"ok": False, "message": "无权限"},
        ]
        with mock.patch("dingtalk_doc_client.get_doc_client", return_value=client):
            usable, blocked = DashboardSkill._precheck_doc_candidates(cands)
        self.assertEqual(len(usable), 1)
        self.assertEqual(usable[0].node_id, "n1")
        self.assertEqual(len(blocked), 1)
        self.assertIn("无权限", blocked[0]["reason"])

    def test_permission_error_blocked(self):
        from dingtalk_doc_client import DingTalkDocPermissionError
        cands = [self._cand()]
        client = mock.Mock()
        client.read_document.side_effect = DingTalkDocPermissionError("no perm")
        with mock.patch("dingtalk_doc_client.get_doc_client", return_value=client):
            usable, blocked = DashboardSkill._precheck_doc_candidates(cands)
        self.assertEqual(usable, [])
        self.assertEqual(len(blocked), 1)
        self.assertIn("权限", blocked[0]["reason"])

    def test_other_exception_passes_through(self):
        """网络瞬断等异常不确定不可读 → 保守放行，不误杀"""
        cands = [self._cand()]
        client = mock.Mock()
        client.read_document.side_effect = TimeoutError("timeout")
        with mock.patch("dingtalk_doc_client.get_doc_client", return_value=client):
            usable, blocked = DashboardSkill._precheck_doc_candidates(cands)
        self.assertEqual(len(usable), 1)
        self.assertEqual(blocked, [])


if __name__ == "__main__":
    unittest.main()
