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


class ParseIntentTests(unittest.TestCase):
    """各意图识别"""

    def test_create(self):
        for text in ("帮我推个看板", "帮我每日推送项目看板", "给我推个看板",
                     "替我每天推看板"):
            r = sc.parse_subscription_command(text)
            self.assertEqual(r["intent"], "create", text)

    def test_query_view_not_create(self):
        """v1.11.5：想看实时看板（我想看看板/现在推看板）→ 放行 Agent 调
        query_dashboard/push_dashboard 工具，不建订阅、不误判"""
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

    def test_query(self):
        r = sc.parse_subscription_command("我的看板几点推送")
        self.assertEqual(r["intent"], "query")

    def test_ctx_passed_through(self):
        ctx = {"user_id": "u1", "staff_id": "s1", "union_id": "x"}
        r = sc.parse_subscription_command("停掉看板", ctx=ctx)
        self.assertEqual(r["user_id"], "u1")
        self.assertEqual(r["staff_id"], "s1")

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
        self._patched = mock.patch.object(sc, "_pending", {})
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
    def test_create_confirmation(self):
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


if __name__ == "__main__":
    unittest.main()
