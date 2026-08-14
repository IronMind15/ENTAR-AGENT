"""看板技能测试（v1.11.0）—— 订阅管理对话（mock store/service，不打真实 API）"""

import contextlib
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
from dashboard.config_model import SourceConfig  # noqa: E402
from dashboard.template_store import _DEFAULT_SECTION_SPEC  # noqa: E402

# A1（v1.12.6）后 dashboard_sources.json 静态配置已清空（数据源全走动态候选）。
# C6（v1.12.6）后创建流程数据源 = owner 当前 enabled 候选（实时解析，新发布自动纳入）。
# 统一用 fixture 预置 owner 可看板候选 + mock config_model.load_sources（名称兜底）：
#   - dashboard.doc_candidates.get_candidate_store → 临时库预置可看板候选
#   - dashboard.config_model.load_sources         → render_confirmation/_format_sources 兜底
_FIXTURE_SOURCES = [
    SourceConfig(key="project_status", name="研发项目现况表",
                 base_id="b1", enabled=True),
    SourceConfig(key="test_issues", name="整机下线测试问题",
                 base_id="b2", enabled=True),
]


def _patch_sources_fixture(owner: str = "u1"):
    """创建流程 fixture：mock 候选存储（2 个可看板文档）+ 静态源名称兜底。

    C6（v1.12.6）：创建订阅的数据源不再来自静态配置，而是 owner 当前 enabled
    候选——预置与 _FIXTURE_SOURCES 同名的两个候选（id 自动 1、2）。
    """
    import os
    import tempfile
    from dashboard.doc_candidates import DocCandidate, DocCandidateStore

    stack = contextlib.ExitStack()
    stack.enter_context(
        mock.patch("dashboard.config_model.load_sources",
                   return_value=_FIXTURE_SOURCES))
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    store = DocCandidateStore(db_path=path)
    store.add(DocCandidate(user_id=owner, url="u1", node_id="n1",
                           kind="notable", enabled=True, name="研发项目现况表"))
    store.add(DocCandidate(user_id=owner, url="u2", node_id="n2",
                           kind="notable", enabled=True, name="整机下线测试问题"))
    stack.enter_context(
        mock.patch("dashboard.doc_candidates.get_candidate_store",
                   return_value=store))

    def _cleanup():
        store.close()
        for suffix in ("", "-wal", "-shm"):
            p = path + suffix
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

    stack.callback(_cleanup)
    return stack


class MatchTests(unittest.TestCase):
    def test_kanban_topic_matches(self):
        """订阅管理话题 → 技能拦截"""
        for text in ("帮我推个看板", "改看板时间到10点", "每周一和周五推看板",
                     "停掉看板", "也推给张工", "我的看板几点推送"):
            self.assertTrue(DashboardSkill.match(text), text)

    def test_concept_question_released_to_agent(self):
        """v1.12.3：概念疑问句「看板数据源和看板任务是分开的吗」→ LLM 判非订阅管理
        放行给 Agent 正常问答（修复 _QUERY_RE 的「看板.*任务」吞问题）"""
        from dashboard import subscription_commands as sub_cmd
        q = "现在的看板数据源和看板任务是分开的吗"
        with mock.patch.object(sub_cmd, "_llm_verify_subscription",
                               return_value=None):
            self.assertFalse(DashboardSkill.match(q))

    def test_concept_question_verified_query(self):
        """同一句 LLM 判是真订阅查询 → 保底接管（match True）"""
        from dashboard import subscription_commands as sub_cmd
        q = "现在的看板数据源和看板任务是分开的吗"
        with mock.patch.object(sub_cmd, "_llm_verify_subscription",
                               return_value="query"):
            self.assertTrue(DashboardSkill.match(q))

    def test_clear_query_no_llm_fast_path(self):
        """v1.12.3：明确查询（无疑问词）→ 正则快速路径，不付 LLM 成本"""
        from dashboard import subscription_commands as sub_cmd
        with mock.patch.object(sub_cmd, "_llm_verify_subscription",
                               side_effect=AssertionError("不应调用 LLM")):
            for text in ("帮我查看现在的看板任务有哪些",
                         "我的看板几点推送", "停掉看板"):
                self.assertTrue(DashboardSkill.match(text), text)

    def test_real_time_query_not_match(self):
        """实时查询看板（今天怎么样/什么情况）→ 放给 Agent 调 dash_query 工具"""
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

    def test_natural_delete_confirmation_matches(self):
        """真实话术“是的，全部删除”应能确认已有删除提案。"""
        with mock.patch("dashboard.subscription_commands.has_recent_kanban_activity",
                        return_value=True):
            self.assertTrue(DashboardSkill.match("是的，全部删除"))

    def test_natural_confirm_word_duide_matches(self):
        """v1.12.4 回归：创建看板确认后用户回「对的」必须被 dashboard 承接，
        不能落 Agent 被 LLM 谎称「已开通」而实际未建订阅（袁会荧实测冲突）。
        有近期看板活动（有 pending）时「对的」是确认词。"""
        with mock.patch("dashboard.subscription_commands.has_recent_kanban_activity",
                        return_value=True):
            for w in ("对的", "对呀", "是的呀", "好呀"):
                self.assertTrue(DashboardSkill.match(w), w)
        with mock.patch("dashboard.subscription_commands.has_recent_kanban_activity",
                        return_value=False):
            self.assertFalse(DashboardSkill.match("对的"))

    def test_unrelated_does_not_match(self):
        with mock.patch("dashboard.subscription_commands.has_recent_kanban_activity",
                        return_value=False):
            self.assertFalse(DashboardSkill.match("你好"))
            self.assertFalse(DashboardSkill.match("d4-1 是什么故障"))

    def test_confirm_word_isolated_per_user(self):
        """审查 Critical 1 回归：A 聊完看板后 B 说「确认」不得被 dashboard 拦截。
        此前 match 用全局活动时间戳，A 的 pending 会让 B 的确认词被误接住，
        返回「您还没有待确认的看板操作」误导提示。"""
        from dashboard import subscription_commands as sub_cmd
        from skills import get_matched_skill

        sub_cmd.set_pending("userA", {"action": "create", "data_sources": []})
        try:
            # B 无看板活动 → 确认词不被 dashboard 拦截，落到 Agent
            self.assertEqual(
                get_matched_skill("确认", user_id="userB").name, "智能 RAG")
            # A 自己有 pending → 确认词仍被 dashboard 承接
            self.assertEqual(
                get_matched_skill("确认", user_id="userA").name, "dashboard")
        finally:
            sub_cmd.clear_pending("userA")
            sub_cmd._activity_by_user.clear()
            sub_cmd._last_activity_ts = 0.0


class HandleCreateTests(unittest.TestCase):
    """创建订阅：反问 → 确认 → 落地 + 推样例"""

    def setUp(self):
        self.patch_pending = mock.patch("pending_context._pending", {})
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
    @mock.patch("skills.dashboard.DashboardSkill._user_template_hint", return_value="")
    def test_create_flow(self, mock_hint, mock_staff):
        # 1. 说「帮我推个看板」→ 反问（C6 后数据源 = owner 当前 enabled 候选）
        #    2. 回复「确认」→ 落地 + 推样例（mock push）——fixture 需覆盖确认，
        #    因 _execute_pending → find_exact_duplicate 也走实时有效源解析。
        with _patch_sources_fixture(owner="union001"):
            r = DashboardSkill.handle("帮我推个看板", user_id="union001")
            self.assertEqual(r["source"], "dashboard")
            self.assertIn("确认", r["answer"])
            self.assertIn("研发项目现况表", r["answer"])
            with mock.patch("skills.dashboard.DashboardSkill._push_sample",
                            return_value="已推送示例看板") as m_push:
                r2 = DashboardSkill.handle("确认", user_id="union001")
            self.assertIn("开通", r2["answer"])
            # v1.12.x：创建后提醒可自定义格式
            self.assertIn("自定义格式", r2["answer"])
            self.assertIn("先写总体结论", r2["answer"])
            m_push.assert_called_once()
            subs = self._store.list_for_owner("union001")
            self.assertEqual(len(subs), 1)
            self.assertTrue(subs[0].enabled)
            self.assertEqual(subs[0].owner_union_id, "union001")
            self.assertEqual(subs[0].owner_staff_id, "staff001")
            self.assertIn("staff001", subs[0].recipients)

    @mock.patch("skills.dashboard.DashboardSkill._staff_id_of", return_value="staff001")
    @mock.patch("skills.dashboard.DashboardSkill._push_sample", return_value="")
    @mock.patch("skills.dashboard.DashboardSkill._user_template_hint", return_value="")
    def test_create_sets_defaults(self, m_hint, m_push, mock_staff):
        # C6（v1.12.6）：创建数据源 = owner 当前 enabled 候选（doc_1/doc_2）
        with _patch_sources_fixture(owner="u1"):
            DashboardSkill.handle("帮我推个看板", user_id="u1")
            DashboardSkill.handle("确认", user_id="u1")
        sub = self._store.list_for_owner("u1")[0]
        self.assertEqual(sub.push_hour, 9)
        self.assertEqual(sub.push_minute, 0)
        self.assertEqual(sub.weekdays, "")
        self.assertEqual(sub.alert_mode, "always")
        self.assertEqual(sorted(sub.data_sources), ["doc_1", "doc_2"])

    def test_confirm_without_pending(self):
        r = DashboardSkill.handle("确认", user_id="nobody")
        self.assertIn("还没有", r["answer"])


class HandleChangeTests(unittest.TestCase):
    """改时间/频率/接收人：需先有订阅"""

    def setUp(self):
        self.patch_pending = mock.patch("pending_context._pending", {})
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


class HandleSetPerSourceTests(unittest.TestCase):
    """v1.12.5：每源独立总结（开启/关闭 → 确认 → 落地 per_source）"""

    def setUp(self):
        import tempfile, os
        from dashboard.subscription_store import Subscription, SubscriptionStore
        self.patch_pending = mock.patch("pending_context._pending", {})
        self.patch_pending.start()
        self.addCleanup(self.patch_pending.stop)
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._db_path = path
        self._store = SubscriptionStore(db_path=path)
        self.patch_store = mock.patch(
            "skills.dashboard.get_subscription_store", return_value=self._store)
        self.patch_store.start()
        self.addCleanup(self.patch_store.stop)
        self.addCleanup(self._cleanup_db)
        sub = Subscription(owner_user_id="u1", owner_staff_id="staff001",
                           owner_union_id="u1",
                           data_sources=["project_status", "doc_2"],
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

    def test_on_flow(self):
        # 袁会荧实测场景：不带「看板」的「四份文件各自独立总结」→ 确认 → per_source=True
        r = DashboardSkill.handle("四份文件各自独立总结", user_id="u1")
        self.assertIn("每个数据源单独总结一条", r["answer"])
        self.assertIn("确认", r["answer"])
        self.assertFalse(self._store.get(self._sub_id).per_source)  # 未确认前不变
        r = DashboardSkill.handle("确认", user_id="u1")
        self.assertIn("已切换", r["answer"])
        self.assertTrue(self._store.get(self._sub_id).per_source)

    def test_off_flow(self):
        r = DashboardSkill.handle("还是合并成一份报告吧", user_id="u1")
        self.assertIn("合并成一份看板报告", r["answer"])
        DashboardSkill.handle("确认", user_id="u1")
        self.assertFalse(self._store.get(self._sub_id).per_source)

    def test_no_subscription_honest(self):
        # 无订阅时如实引导，不编造「已生效」
        self._store.delete(self._sub_id)
        r = DashboardSkill.handle("四份文件各自独立总结", user_id="u1")
        self.assertIn("先开通", r["answer"])
        r = DashboardSkill.handle("确认", user_id="u1")
        self.assertIn("还没有待确认", r["answer"])


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
        self.assertIn("确认", r["answer"])
        self.assertTrue(all(s.enabled for s in self._store.list_for_owner("u1")))
        r = DashboardSkill.handle("确认", user_id="u1")
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
        self.patch_pending = mock.patch("pending_context._pending", {})
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
    @mock.patch("skills.dashboard.DashboardSkill._user_template_hint", return_value="")
    def test_doc_create_flow(self, m_hint, m_push, mock_staff):
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

    @mock.patch("skills.dashboard.DashboardSkill._staff_id_of", return_value="staff001")
    @mock.patch("skills.dashboard.DashboardSkill._push_sample", return_value="已推送")
    @mock.patch("skills.dashboard.DashboardSkill._user_template_hint", return_value="")
    def test_natural_confirm_word_duide_executes_pending(self, m_hint, m_push, mock_staff):
        """v1.12.4 回归（袁会荧实测）：发链接+「做每日看板」意图 → 反问确认 →
        用户回「对的」（自然口语确认）→ 必须执行 pending 建订阅。
        修复前「对的」不在确认词表 → 落 Agent → LLM 无工具调用谎称已开通，
        实际订阅从未创建 → 后续「示例看板在哪里」查询报「没有订阅」矛盾。"""
        self._seed_candidates()
        # 1. 模拟 bot 发链接后已创建 doc_create pending（反问确认）
        r1 = DashboardSkill._handle_doc_create("u1", source_candidate_ids=[1, 2])
        self.assertIn("确认", r1["answer"])
        # 2. 用户回「对的」→ match 必须承接（有 pending）
        self.assertTrue(DashboardSkill.match("对的", user_id="u1"))
        # 3. handle 执行确认 → 真实创建订阅
        r2 = DashboardSkill.handle("对的", user_id="u1")
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
        self.patch_pending = mock.patch("pending_context._pending", {})
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
        self.assertIn("确认", r["answer"])
        self.assertFalse(self._store.list_for_owner("u1")[0].enabled)
        r = DashboardSkill.handle("确认", user_id="u1")
        self.assertIn("已恢复", r["answer"])
        subs = self._store.list_for_owner("u1")
        self.assertEqual(len(subs), 1)       # 不新建
        self.assertTrue(subs[0].enabled)     # 已启用

    def test_reopen_phrase_resumes_not_duplicates(self):
        """回归：v1.11.5 前「重新开通看板」新建重复订阅，现在应恢复"""
        self._store.create(self._sub(enabled=False))
        r = DashboardSkill.handle("重新开通看板", user_id="u1")
        self.assertIn("确认", r["answer"])
        r = DashboardSkill.handle("确认", user_id="u1")
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

    def test_contextual_delete_all_then_natural_confirmation(self):
        """查询看板后说“全部删除”应生成真实删除提案，确认后回读为空。"""
        self._store.create(self._sub())
        self._store.create(self._sub(data_sources=["test_issues"]))
        DashboardSkill.handle("我的看板任务有哪些", user_id="u1")
        r = DashboardSkill.handle("现在帮我全部删除", user_id="u1")
        self.assertIn("2 个", r["answer"])
        self.assertEqual(len(self._store.list_for_owner("u1")), 2)
        r2 = DashboardSkill.handle("是的，全部删除", user_id="u1")
        self.assertIn("已删除 2 个", r2["answer"])
        self.assertEqual(self._store.list_for_owner("u1"), [])

    @mock.patch("skills.dashboard.DashboardSkill._staff_id_of", return_value="staff001")
    @mock.patch("skills.dashboard.DashboardSkill._push_sample", return_value="")
    def test_exact_duplicate_create_is_reused(self, m_push, mock_staff):
        """相同用户、来源、时间、接收人和模式不得创建第二条订阅。"""
        self._store.create(self._sub(data_sources=["project_status", "test_issues"]))
        with _patch_sources_fixture():
            DashboardSkill.handle("帮我推个看板", user_id="u1")
            r = DashboardSkill.handle("确认", user_id="u1")
        self.assertIn("已有相同", r["answer"])
        self.assertEqual(len(self._store.list_for_owner("u1")), 1)

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


class QueryDashboardToolContractTests(unittest.TestCase):
    """v1.11.10：dash_query 工具描述契约——必须显式要求保留数据来源链接。

    同事杨妍通过 Agent 实时查询看板时，LLM 转述丢掉了『## 数据来源』里的
    [查看原文](url) 链接，而她本人（订阅推送路径）能看到链接。工具描述
    是约束 LLM 转述行为的抓手，删掉这段要求会复发，故固化为契约测试。
    """

    def test_description_requires_preserving_source_links(self):
        from tools.dash_query import DEFINITION
        desc = DEFINITION["description"]
        self.assertIn("数据来源", desc)
        self.assertIn("查看原文", desc)
        self.assertIn("原样保留", desc)

    def test_execute_scopes_sources_to_current_user(self):
        """问题 4 回归：dash_query 必须按当前用户取数据源，防跨用户串看板"""
        from tools.dash_query import execute
        import inspect
        src = inspect.getsource(execute)
        self.assertIn("get_current_user_id()", src)
        self.assertIn("load_all_available_sources(user_id=", src)

    def test_dash_push_scopes_to_current_user(self):
        """v1.11.10：dash_push 主动推送必须按当前用户隔离——
        只采本人数据源、只推本人订阅，防『现在推看板』把别人的私人表格
        推给所有订阅接收人"""
        from tools.dash_push import execute as push_exec
        import inspect
        src = inspect.getsource(push_exec)
        self.assertIn("load_all_available_sources(user_id=uid)", src)
        self.assertIn("owner_user_id == uid", src)
        self.assertNotIn("load_all_available_sources()", src)

    def test_dash_push_definition_mentions_own_subscription(self):
        from tools.dash_push import DEFINITION
        self.assertIn("当前用户", DEFINITION["description"])
        self.assertIn("该用户启用的订阅", DEFINITION["description"])


class TemplateHintAndAdjustTests(unittest.TestCase):
    """v1.12.x：创建提醒自定义格式/复用私有模板 + 同名模板调节"""

    def setUp(self):
        from dashboard.template_store import TemplateStore
        import tempfile, os
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._db_path = path
        self._tpl_store = TemplateStore(db_path=path)
        self.addCleanup(self._cleanup)
        self.patch_ts = mock.patch(
            "dashboard.template_store.get_template_store",
            return_value=self._tpl_store)
        self.patch_ts.start()
        self.addCleanup(self.patch_ts.stop)

    def _cleanup(self):
        self._tpl_store.close()
        for suffix in ("", "-wal", "-shm"):
            p = self._db_path + suffix
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

    @mock.patch("skills.dashboard.DashboardSkill._staff_id_of",
                return_value="staff001")
    def test_user_template_hint_lists_private_templates(self, mock_staff):
        self._tpl_store.create_user_template(
            key="w1", name="我的周报", user_id="u1")
        hint = DashboardSkill._user_template_hint("u1")
        self.assertIn("您保存过模板", hint)
        self.assertIn("我的周报", hint)
        # 无私有模板 → 无提示
        self.assertEqual(DashboardSkill._user_template_hint("u2"), "")

    @mock.patch("skills.dashboard.DashboardSkill._staff_id_of",
                return_value="staff001")
    @mock.patch("dashboard.template_store.TemplateStore.sync_user_template_file",
                return_value="")
    def test_save_user_template_same_name_updates(self, mock_sync, mock_staff):
        """同名再提交 = 调节模板内容（更新而非报错/加后缀）"""
        tdef1 = {"name": "老板摘要", "description": "v1",
                 "map_instructions": "只看变化", "reduce_instructions": "先总体",
                 "section_spec": _DEFAULT_SECTION_SPEC}
        ok1, tpl1, msg1 = DashboardSkill._save_user_template("u1", tdef1)
        self.assertTrue(ok1, msg1)
        tdef2 = {"name": "老板摘要", "description": "v2",
                 "map_instructions": "只看变化",
                 "reduce_instructions": "先风险后进展",
                 "section_spec": _DEFAULT_SECTION_SPEC}
        ok2, tpl2, msg2 = DashboardSkill._save_user_template("u1", tdef2)
        self.assertTrue(ok2, msg2)
        # key 不变（同一条记录被更新），内容为 v2
        self.assertEqual(tpl1.key, tpl2.key)
        updated = self._tpl_store.get(tpl1.key, "u1")
        self.assertEqual(updated.reduce_instructions, "先风险后进展")
        # 没有生成「老板摘要2」之类的副本
        mine = [t for t in self._tpl_store.list_visible("u1")
                if t.scope == "user"]
        self.assertEqual(len(mine), 1)

    @mock.patch("skills.dashboard.DashboardSkill._staff_id_of",
                return_value="staff001")
    @mock.patch("dashboard.template_store.TemplateStore.sync_user_template_file",
                return_value="")
    def test_save_user_template_syncs_file(self, mock_sync, mock_staff):
        """保存后同步本地文件（调用点验证，写盘逻辑在 template_store 测试）"""
        from dashboard.template_store import _DEFAULT_SECTION_SPEC as spec
        ok, tpl, _ = DashboardSkill._save_user_template(
            "u1", {"name": "文件模板", "section_spec": spec})
        self.assertTrue(ok)
        mock_sync.assert_called_once()
        self.assertEqual(mock_sync.call_args[0][0].name, "文件模板")
        # 第二个参数是经 _staff_id_of 转换后的 staff_id
        self.assertEqual(mock_sync.call_args[0][1], "staff001")


class PushSamplePerSourceTests(unittest.TestCase):
    """v1.12.5：样例推送改逐源多条（识别/预览场景，每个数据源一条）"""

    def _sub(self):
        from dashboard.subscription_store import Subscription
        return Subscription(owner_user_id="u1", owner_staff_id="staff001",
                            owner_union_id="u1", data_sources=["a", "b"],
                            recipients=["staff001"])

    def test_push_sample_sends_per_source_messages(self):
        two_sources = [
            {"source_key": "a", "name": "源A", "table_name": "", "total": 1,
             "items": [], "detailed_items": [], "status_counts": {},
             "attention_items": [], "normal_items": [], "other_items": []},
            {"source_key": "b", "name": "源B", "table_name": "", "total": 1,
             "items": [], "detailed_items": [], "status_counts": {},
             "attention_items": [], "normal_items": [], "other_items": []},
        ]
        with mock.patch("dashboard.service.resolve_subscription_sources",
                        return_value=[mock.MagicMock()]), \
             mock.patch("dashboard.service.collect_and_parse",
                        return_value=(two_sources, [])), \
             mock.patch("dashboard.service.source_resolution_warnings",
                        return_value=[]), \
             mock.patch("dashboard.service.resolve_template",
                        return_value=None), \
             mock.patch("dashboard.service.assemble_per_source_messages",
                        return_value=["源A总结", "源B总结"]) as m_per_source, \
             mock.patch("dashboard.service.push_messages",
                        return_value=(True, "")) as m_push:
            note = DashboardSkill._push_sample(self._sub())
        self.assertIn("每个数据源一条", note)
        # 逐源组装：两源各一条，交给 push_messages 顺序发送
        m_per_source.assert_called_once()
        self.assertEqual(m_push.call_args[0][2], ["源A总结", "源B总结"])


class SpokenChoiceTests(unittest.TestCase):
    """v1.12.6（B5）：口语序数选择（「我说选第一个」）在看板层接住

    回归背景（2026-08-14 实测）：袁会荧在 create pending 反问中回「我说选第一个」，
    未达看板层（parse_template_choice 只认 choose_template，其余判定接不住），
    落 Agent 后无工具可用 → 调 contact_find 查通讯录 → 编造「看板维护转达工单」。
    B5：新增 parse_spoken_choice，绑 kanban pending + 反问窗口，接住口语序数选择。
    """

    def setUp(self):
        self.patch_pending = mock.patch("pending_context._pending", {})
        self.patch_pending.start()
        self.addCleanup(self.patch_pending.stop)

    def tearDown(self):
        from dashboard import subscription_commands as sc
        sc._activity_by_user.clear()
        sc._last_activity_ts = 0.0

    # ===== parse_spoken_choice 纯函数 =====
    def test_parse_spoken_choice_no_pending_returns_none(self):
        from dashboard import subscription_commands as sc
        for t in ("我说选第一个", "选第二个", "第一个", "就第三个吧"):
            self.assertIsNone(sc.parse_spoken_choice(t, "u_spoken"), t)

    def test_parse_spoken_choice_variants(self):
        from dashboard import subscription_commands as sc
        sc.set_pending("u_spoken", {"intent": "create", "data_sources": []})
        cases = [
            ("我说选第一个", 1), ("我是说选第一个", 1), ("选第二个", 2),
            ("第一个", 1), ("就第三个吧", 3), ("我说选第5个", 5),
        ]
        for t, expect in cases:
            p = sc.parse_spoken_choice(t, "u_spoken")
            self.assertIsNotNone(p, t)
            self.assertEqual(p["index"], expect, t)

    def test_parse_spoken_choice_requires_window(self):
        from dashboard import subscription_commands as sc
        sc.set_pending("u_spoken", {"intent": "create", "data_sources": []})
        sc._activity_by_user["u_spoken"] = 0.0  # 反问窗口过期
        self.assertIsNone(sc.parse_spoken_choice("选第一个", "u_spoken"))

    def test_parse_spoken_choice_does_not_steal_normal_chat(self):
        from dashboard import subscription_commands as sc
        sc.set_pending("u_spoken", {"intent": "create", "data_sources": []})
        for t in ("第一个文件我要看", "选一个", "第一个人是张工",
                  "你说第一个方案", "第一个提议不错"):
            self.assertIsNone(sc.parse_spoken_choice(t, "u_spoken"), t)

    # ===== 技能匹配/处理 =====
    def test_match_spoken_choice_with_pending(self):
        from dashboard import subscription_commands as sc
        sc.set_pending("u_spoken", {"intent": "create", "data_sources": []})
        self.assertTrue(DashboardSkill.match("我说选第一个", user_id="u_spoken"))

    def test_match_spoken_choice_no_pending_false(self):
        self.assertFalse(DashboardSkill.match("我说选第一个", user_id="u_spoken"))

    def test_handle_create_pending_honest_no_workorder(self):
        """create pending 中回「我说选第一个」→ 如实说明无分项候选项，不编造工单"""
        from dashboard import subscription_commands as sc
        sc.set_pending("u_spoken", {
            "intent": "create", "data_sources": [],
            "push_hour": 9, "push_minute": 0, "weekdays": "",
            "alert_mode": "always",
        })
        r = DashboardSkill.handle("我说选第一个", user_id="u_spoken")
        self.assertIn("确认", r["answer"])
        self.assertIn("工单", r["answer"])          # 明确说明无工单功能
        self.assertNotIn("已生成", r["answer"])     # 不编造完成态

    def test_handle_choose_template_spoken_maps(self):
        """choose_template 反问中「选第二个」→ 等价数字 2 → weekly"""
        from dashboard import subscription_commands as sc
        sc.set_pending("u_spoken", {"intent": "choose_template", "sub_id": 1})
        with mock.patch.object(
                DashboardSkill, "_execute_template_choice",
                return_value={"answer": "ok", "source": "dashboard"}) as m_exec:
            DashboardSkill.handle("选第二个", user_id="u_spoken")
        m_exec.assert_called_once()
        self.assertEqual(m_exec.call_args[0][0]["choice"], "weekly")

    def test_handle_choose_template_spoken_out_of_range(self):
        """choose_template 中「选第五个」超出 3 项 → 重列模板，不编造"""
        from dashboard import subscription_commands as sc
        sc.set_pending("u_spoken", {"intent": "choose_template", "sub_id": 1})
        r = DashboardSkill.handle("选第五个", user_id="u_spoken")
        self.assertIn("3 个模板", r["answer"])
        self.assertIsNotNone(sc.get_pending("u_spoken"))  # pending 保留待再选


if __name__ == "__main__":
    unittest.main()
