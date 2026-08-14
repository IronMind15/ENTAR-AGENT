"""看板订阅管理指令解析测试（v1.11.0）—— 纯文本解析，不打真实 API"""

import sys
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from dashboard import subscription_commands as sc  # noqa: E402
from dashboard.config_model import SourceConfig  # noqa: E402

# A1（v1.12.6）后静态配置已清空，渲染名需 mock config_model.load_sources
_FIXTURE_SOURCES = [
    SourceConfig(key="project_status", name="研发项目现况表",
                 base_id="b1", enabled=True),
    SourceConfig(key="test_issues", name="整机下线测试问题",
                 base_id="b2", enabled=True),
]


class ParseIntentTests(unittest.TestCase):
    """各意图识别"""

    def test_create(self):
        for text in ("帮我推个看板", "帮我每日推送项目看板", "给我推个看板",
                     "替我每天推看板"):
            r = sc.parse_subscription_command(text)
            self.assertEqual(r["intent"], "create", text)

    def test_query_view_not_create(self):
        """v1.11.5：想看实时看板（我想看看板/现在推看板）→ 放行 Agent 调
        dash_query/dash_push 工具，不建订阅、不误判"""
        for text in ("我想看看板", "帮我看看板", "看板今天怎么样", "现在推看板", "马上推看板"):
            self.assertIsNone(sc.parse_subscription_command(text), text)

    def test_change_time(self):
        r = sc.parse_subscription_command("改看板时间到10点")
        self.assertEqual(r["intent"], "change_time")
        self.assertEqual(r["push_hour"], 10)
        self.assertEqual(r["push_minute"], 0)

    def test_change_freq(self):
        r = sc.parse_subscription_command("每周一和周五推看板")
        self.assertEqual(r["intent"], "change_freq")
        self.assertEqual(r["weekdays"], "1,5")

    def test_change_recipients(self):
        r = sc.parse_subscription_command("也推给张工")
        self.assertEqual(r["intent"], "change_recipients")
        self.assertEqual(r["recipient_names"], ["张工"])
        self.assertTrue(r["add"])

    def test_remove_recipient(self):
        r = sc.parse_subscription_command("不要推给李四了")
        self.assertEqual(r["intent"], "change_recipients")
        self.assertFalse(r["add"])
        self.assertEqual(r["recipient_names"], ["李四"])

    def test_remove_recipient_trailing_particle(self):
        """v1.11.5：句末语气词剥掉——「李四了」取人名「李四」"""
        r = sc.parse_subscription_command("别推给王工啦")
        self.assertEqual(r["intent"], "change_recipients")
        self.assertEqual(r["recipient_names"], ["王工"])
        self.assertFalse(r["add"])

    def test_recipient_self_not_change(self):
        """④v1.11.5：「只推送给我自己」接收人本就是自己，不当作加人"""
        r = sc.parse_subscription_command("只推给我自己")
        self.assertEqual(r["intent"], "set_recipient_self")

    def test_pasted_again_not_recipient(self):
        """⑤v1.11.5：「我已经粘贴过了重新发给了你」的「了你」不是人名"""
        self.assertIsNone(sc.parse_subscription_command("我已经粘贴过了重新发给了你"))

    def test_kanban_relationship_question_not_create(self):
        """⑥v1.11.5：质疑反问「这跟看板功能有什么关系」不拦截、不建订阅"""
        self.assertIsNone(sc.parse_subscription_command("这跟看板功能有什么关系"))

    def test_stop(self):
        r = sc.parse_subscription_command("停掉看板")
        self.assertEqual(r["intent"], "stop")

    def test_stop_with_qualifier(self):
        """口语插字（停掉前面的看板）必须识别为 stop，不得兜底成 create"""
        for q in ("停掉前面的看板", "把看板停掉", "停用这个看板",
                  "取消之前的看板订阅", "关掉看板吧"):
            r = sc.parse_subscription_command(q)
            self.assertEqual(r["intent"], "stop", f"{q} → {r}")

    def test_resume(self):
        """v1.11.6：恢复订阅——「重新开通看板」是恢复不是新建（防重复订阅）"""
        for text in ("恢复看板", "重新开通看板", "重新打开看板", "启用看板",
                     "重新开通我的每日看板"):
            r = sc.parse_subscription_command(text)
            self.assertEqual(r["intent"], "resume", text)

    def test_delete(self):
        """v1.11.6：删除订阅——「删除看板订阅」不得误判成 query 查看设置"""
        for text in ("删除看板", "删掉看板", "删除看板订阅", "解绑看板",
                     "把看板删除"):
            r = sc.parse_subscription_command(text)
            self.assertEqual(r["intent"], "delete", f"{text} → {r}")

    def test_delete_not_swallowed_by_query(self):
        """回归：v1.11.6 前「删除看板订阅」命中 _QUERY_RE 的「看板.*订阅」误判成 query"""
        r = sc.parse_subscription_command("删除看板订阅")
        self.assertIsNotNone(r)
        self.assertEqual(r["intent"], "delete")

    def test_query(self):
        r = sc.parse_subscription_command("我的看板几点推送")
        self.assertEqual(r["intent"], "query")

    def test_ctx_passed_through(self):
        ctx = {"user_id": "u1", "staff_id": "s1", "union_id": "x"}
        r = sc.parse_subscription_command("停掉看板", ctx=ctx)
        self.assertEqual(r["user_id"], "u1")
        self.assertEqual(r["staff_id"], "s1")

    def test_doc_create_without_doc_ref(self):
        """v1.11.10：『我想做一个每日看板』无『文档/表格』引用词也应识别创建
        （对应同事杨妍：发完文档链接后补一句创建意图，但没带引用词）。
        bot 带链接路径 require_doc_ref=False 放宽。"""
        r = sc.parse_doc_dashboard_intent(
            "我想做一个每日看板，需要每天上午9点发给我", require_doc_ref=False)
        self.assertIsNotNone(r)
        self.assertEqual(r["intent"], "doc_create")

    def test_doc_create_strict_by_default(self):
        """v1.11.10：技能路径默认严格——『帮我推个看板』是普通订阅，
        不能被动态文档看板（doc_create）误抢；带文档引用词的仍识别。"""
        self.assertIsNone(sc.parse_doc_dashboard_intent("帮我推个看板"))
        r = sc.parse_doc_dashboard_intent("按这几个文档做每日看板")
        self.assertEqual(r["intent"], "doc_create")

    def test_query_task_list(self):
        """v1.11.10：『查看看板任务』是查询订阅状态，不是实时看板内容"""
        for text in ("帮我查看一下看板任务都有哪些", "帮我查一下我的看板任务"):
            r = sc.parse_subscription_command(text)
            self.assertIsNotNone(r)
            self.assertEqual(r["intent"], "query", text)

    def test_not_kanban_returns_none(self):
        self.assertIsNone(sc.parse_subscription_command("你好"))
        self.assertIsNone(sc.parse_subscription_command(""))
        self.assertIsNone(sc.parse_subscription_command(None))

    def test_negative_topic_blocked(self):
        """文档/方案等话题不算操作订阅，不拦截"""
        for text in ("看板功能方案", "看板方法论文档", "看板设计说明", "把看板文档学习入库"):
            self.assertIsNone(sc.parse_subscription_command(text), text)


class ParseTimeTests(unittest.TestCase):
    def test_whole_hour(self):
        self.assertEqual(sc._parse_time("改到10点"), (10, 0))

    def test_half_hour(self):
        self.assertEqual(sc._parse_time("9点半"), (9, 30))

    def test_colon(self):
        self.assertEqual(sc._parse_time("每天10:30推"), (10, 30))

    def test_bounds(self):
        self.assertEqual(sc._parse_time("25点"), (23, 0))


class ParseWeekdaysTests(unittest.TestCase):
    def test_everyday(self):
        self.assertEqual(sc._parse_weekdays("每天推看板"), "")

    def test_weekend(self):
        self.assertEqual(sc._parse_weekdays("周末推"), "6,7")

    def test_two_days(self):
        self.assertEqual(sc._parse_weekdays("每周一和周五"), "1,5")

    def test_digits(self):
        self.assertEqual(sc._parse_weekdays("每周 1,3,5"), "1,3,5")

    def test_unknown_returns_empty(self):
        self.assertEqual(sc._parse_weekdays("看板"), "")


class FormatWeekdaysTests(unittest.TestCase):
    def test_format(self):
        self.assertEqual(sc.format_weekdays("1,5"), "每周周一、周五")
        self.assertEqual(sc.format_weekdays(""), "每天")
        self.assertEqual(sc.format_weekdays("6,7"), "每周周六、周日")


class ResolveRecipientTests(unittest.TestCase):
    @mock.patch("contact_api.get_contact_client")
    def test_resolve_by_name(self, mock_get):
        client = mock_get.return_value
        client.search.return_value = {
            "found": True, "results": [{"userId": "staff_zhanggong", "name": "张工"}],
        }
        self.assertEqual(sc.resolve_recipient("张工"), "staff_zhanggong")

    @mock.patch("contact_api.get_contact_client")
    def test_not_found_returns_empty(self, mock_get):
        client = mock_get.return_value
        client.search.return_value = {"found": False, "results": []}
        self.assertEqual(sc.resolve_recipient("不存在的人"), "")

    @mock.patch("contact_api.get_contact_client")
    def test_api_error_returns_empty(self, mock_get):
        mock_get.side_effect = RuntimeError("网络错误")
        self.assertEqual(sc.resolve_recipient("张工"), "")


class PendingTests(unittest.TestCase):
    def setUp(self):
        # v1.12.1（M3）：pending 统一收口到 pending_context，patch 目标迁移
        import pending_context
        self._patched = mock.patch.object(pending_context, "_pending", {})
        self._patched.start()
        self.addCleanup(self._patched.stop)

    def test_pending_flow(self):
        pending = {"intent": "create", "user_id": "u1"}
        sc.set_pending("u1", pending)
        self.assertEqual(sc.get_pending("u1"), pending)
        sc.clear_pending("u1")
        self.assertIsNone(sc.get_pending("u1"))

    def test_get_unknown_returns_none(self):
        self.assertIsNone(sc.get_pending("nobody"))


class RenderConfirmationTests(unittest.TestCase):
    @mock.patch("dashboard.config_model.load_sources",
                return_value=_FIXTURE_SOURCES)
    def test_create_confirmation(self, mock_load):
        pending = {
            "intent": "create",
            "data_sources": ["project_status", "test_issues"],
            "push_hour": 9, "push_minute": 0, "weekdays": "",
            "alert_mode": "changes_only",
        }
        text = sc.render_confirmation(pending)
        self.assertIn("研发项目现况表", text)
        self.assertIn("整机下线测试问题", text)
        self.assertIn("09:00", text)
        self.assertIn("确认", text)

    def test_default_alert_mode_daily_push_text(self):
        """默认 always → 确认文案「每天固定推送（附今日变化）」（v1.11.4）"""
        pending = {"intent": "create", "data_sources": ["s1"],
                   "push_hour": 9, "push_minute": 0, "weekdays": "",
                   "alert_mode": "always"}
        text = sc.render_confirmation(pending)
        self.assertIn("每天固定推送（附今日变化）", text)
        self.assertNotIn("仅数据有变化时推送", text)

    def test_changes_only_confirmation_text(self):
        pending = {"intent": "doc_create", "data_sources": ["doc_1"],
                   "push_hour": 9, "push_minute": 0, "weekdays": "",
                   "alert_mode": "changes_only"}
        text = sc.render_confirmation(pending)
        self.assertIn("仅数据有变化时推送", text)

    def test_stop_confirmation(self):
        text = sc.render_confirmation({"intent": "stop"})
        self.assertIn("停止", text)

    def test_change_time_confirmation(self):
        pending = {"intent": "change_time", "push_hour": 10, "push_minute": 30,
                   "weekdays": ""}
        text = sc.render_confirmation(pending)
        self.assertIn("10:30", text)


class LLMVerifyUnitTests(unittest.TestCase):
    """_llm_verify_subscription 解析逻辑（注入 fake llm_fn，不打真实 API）"""

    def test_not_subscription_returns_none(self):
        verdict = sc._llm_verify_subscription(
            "看板推送是不是要收费", fallback_intent="create",
            llm_fn=lambda prompt, max_tokens=0: '{"is_subscription": false, "intent": "null"}')
        self.assertIsNone(verdict)

    def test_llm_corrects_intent(self):
        """LLM 把「不要推给别人」修正为 set_recipient_self"""
        verdict = sc._llm_verify_subscription(
            "不要推给别人", fallback_intent="change_recipients",
            llm_fn=lambda prompt, max_tokens=0: '{"is_subscription": true, "intent": "set_recipient_self"}')
        self.assertEqual(verdict, "set_recipient_self")

    def test_bad_json_falls_back(self):
        verdict = sc._llm_verify_subscription(
            "看板推送是不是要收费", fallback_intent="create",
            llm_fn=lambda prompt, max_tokens=0: "not json")
        self.assertEqual(verdict, "create")

    def test_llm_exception_falls_back(self):
        def boom(prompt, max_tokens=0):
            raise RuntimeError("api down")
        verdict = sc._llm_verify_subscription(
            "看板推送是不是要收费", fallback_intent="create", llm_fn=boom)
        self.assertEqual(verdict, "create")

    def test_needs_llm_verify_signals(self):
        """可疑才复核：疑问句/指代词命中，普通指令不命中"""
        self.assertTrue(sc._needs_llm_verify("看板推送是不是要收费", "create"))
        self.assertTrue(sc._needs_llm_verify("不要推给别人", "change_recipients"))
        self.assertFalse(sc._needs_llm_verify("帮我推个看板", "create"))
        self.assertFalse(sc._needs_llm_verify("也推给张工", "change_recipients"))
        # v1.12.3：概念疑问句命中 query 意图也复核（防被 _QUERY_RE 吞成查订阅状态）
        self.assertTrue(sc._needs_llm_verify(
            "现在的看板数据源和看板任务是分开的吗", "query"))
        self.assertFalse(sc._needs_llm_verify("我的看板", "query"))

    def test_needs_kanban_ambiguity_check(self):
        """v1.12.3：看板话题 + 疑问词 → True；明确管理/查询无疑问词 → False"""
        self.assertTrue(sc.needs_kanban_ambiguity_check(
            "现在的看板数据源和看板任务是分开的吗"))
        self.assertTrue(sc.needs_kanban_ambiguity_check("看板推送是不是要收费"))
        self.assertFalse(sc.needs_kanban_ambiguity_check("我的看板几点推送"))
        self.assertFalse(sc.needs_kanban_ambiguity_check("停掉看板"))
        self.assertFalse(sc.needs_kanban_ambiguity_check("看板文档学习"))  # 否定词话题


class LLMVerifyIntegrationTests(unittest.TestCase):
    """parse_subscription_command 接入 LLM 复核的集成行为"""

    def test_plain_commands_do_not_call_llm(self):
        """正常指令不触发 LLM（正则快闸零成本）"""
        with mock.patch.object(sc, "_llm_verify_subscription") as m:
            self.assertEqual(sc.parse_subscription_command("帮我推个看板")["intent"], "create")
            self.assertEqual(sc.parse_subscription_command("也推给张工")["intent"], "change_recipients")
            self.assertEqual(sc.parse_subscription_command("看板不要推给李四")["intent"], "change_recipients")
            m.assert_not_called()

    def test_fee_question_released(self):
        """修复：『看板推送是不是要收费』→ LLM 判定非订阅 → 放行"""
        with mock.patch.object(sc, "_llm_verify_subscription", return_value=None):
            self.assertIsNone(sc.parse_subscription_command("看板推送是不是要收费"))

    def test_dont_push_others_becomes_set_self(self):
        """修复：『不要推给别人』→ LLM 修正为只推给自己"""
        with mock.patch.object(sc, "_llm_verify_subscription",
                               return_value="set_recipient_self"):
            r = sc.parse_subscription_command("不要推给别人")
            self.assertEqual(r["intent"], "set_recipient_self")

    def test_llm_failure_keeps_regex_behavior(self):
        """LLM 失败回退正则原逻辑（create 兜底），不崩"""
        with mock.patch.object(sc, "_llm_verify_subscription", return_value="create"):
            r = sc.parse_subscription_command("看板推送是不是要收费")
            self.assertEqual(r["intent"], "create")


class FeatureRegressionTests(unittest.TestCase):
    """v1.11.6 特征测试（awesome-test-writing tripwire）：把实测用例集固化为安全网。
    任一改动能被这些用例捉住：正常意图必须识别、非订阅话题必须放行。"""

    def test_create_variants(self):
        for text in ("帮我开通看板", "帮我推个看板", "帮我每日推送项目看板",
                     "给我推个看板", "替我每天推看板", "订阅发给我自己"):
            r = sc.parse_subscription_command(text)
            self.assertIsNotNone(r, text)
            self.assertIn(r["intent"], ("create", "set_recipient_self"), text)

    def test_stop_variants(self):
        for text in ("把看板停掉", "把看板推送关掉", "停掉前面的看板", "关掉看板吧"):
            self.assertEqual(sc.parse_subscription_command(text)["intent"], "stop", text)

    def test_query_and_config(self):
        self.assertEqual(sc.parse_subscription_command("看板几点推送")["intent"], "query")

    def test_released_dialogs(self):
        """非订阅话题一律放行（含历史所有误判话术）"""
        for text in ("这跟看板功能有什么关系", "这个文档怎么学习入库",
                     "帮我总结这份方案", "看看今天的看板",
                     "我已经粘贴过了重新发给了你", "这个看板功能有什么用",
                     "看板数据从哪来", "这个看板跟我有啥关系",
                     "我想看下看板配置", "推一下看板"):
            self.assertIsNone(sc.parse_subscription_command(text), text)

    def test_doc_create_separate_function(self):
        r = sc.parse_doc_dashboard_intent("按这几个文档做每日看板")
        self.assertEqual(r["intent"], "doc_create")
        self.assertIsNone(sc.parse_doc_dashboard_intent("把文档学习入库"))


class SetPerSourceTests(unittest.TestCase):
    """v1.12.5：每源独立总结意图（不带「看板」也识别；关开两态）"""

    def test_parse_on_variants(self):
        # 袁会荧实测需求「四份文件各自独立总结」——输出模式调整，无「看板」也能进
        for text in ("四份文件各自独立总结",
                     "每个数据源单独总结",
                     "分开总结一下",
                     "每份文件单独推送",
                     "看板每个数据源单独分析"):
            r = sc.parse_subscription_command(text)
            self.assertIsNotNone(r, text)
            self.assertEqual(r["intent"], "set_per_source", text)
            self.assertTrue(r["per_source"], text)

    def test_parse_off_variants(self):
        for text in ("不要分开总结了，合并成一份",
                     "还是合并成一份报告吧",
                     "取消每份文件单独总结"):
            r = sc.parse_subscription_command(text)
            self.assertIsNotNone(r, text)
            self.assertEqual(r["intent"], "set_per_source", text)
            self.assertFalse(r["per_source"], text)

    def test_not_per_source_released(self):
        # 普通「总结/帮我总结」不带各自/单独/分开等 → 放行给 Agent，不误拦
        for text in ("帮我总结一下这个文档",
                     "总结一下这周的工作",
                     "这个表格帮我删掉",
                     "今天天气怎么样"):
            self.assertIsNone(sc.parse_subscription_command(text), text)

    def test_render_confirmation_on_off(self):
        on = sc.render_confirmation({"intent": "set_per_source", "per_source": True})
        self.assertIn("每个数据源单独总结一条", on)
        off = sc.render_confirmation({"intent": "set_per_source", "per_source": False})
        self.assertIn("合并成一份看板报告", off)


if __name__ == "__main__":
    unittest.main()
