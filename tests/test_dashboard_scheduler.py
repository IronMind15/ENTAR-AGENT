"""看板定时调度测试（v1.11.0）—— fake clock 测 is_due；mock 采集/推送测执行链"""

import datetime
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from dashboard.subscription_store import Subscription  # noqa: E402
from dashboard_scheduler import _execute_subscription, is_due  # noqa: E402


def _sub(**kw):
    base = Subscription(
        owner_user_id="u1", owner_staff_id="staff001", owner_union_id="u1",
        data_sources=["project_status"], push_hour=9, push_minute=0,
        weekdays="", alert_mode="changes_only", recipients=["staff001"],
    )
    for k, v in kw.items():
        setattr(base, k, v)
    return base


def _parsed():
    return [{
        "source_key": "project_status", "name": "研发项目现况表", "table_name": "33周",
        "total": 1, "status_counts": {"滞后": 1},
        "attention_items": [{"项目名称": "项目A", "状态": "滞后", "本周进展": "x"}],
        "normal_items": [], "other_items": [],
    }]


class IsDueTests(unittest.TestCase):
    """到点判定：时间/星期/当天不重复"""

    def _now(self, hour=9, minute=0, weekday=0, day=10):
        # 2026-08-10 是周一（weekday=0）
        base = datetime.date(2026, 8, 10)
        date = base + datetime.timedelta(days=weekday)
        return datetime.datetime(date.year, date.month, date.day, hour, minute)

    def test_matches_time(self):
        sub = _sub(push_hour=9, push_minute=0, weekdays="")
        self.assertTrue(is_due(self._now(9, 0), sub))

    def test_mismatch_time(self):
        sub = _sub(push_hour=9, push_minute=0)
        self.assertFalse(is_due(self._now(9, 5), sub))
        self.assertFalse(is_due(self._now(8, 0), sub))

    def test_weekday_filter(self):
        sub = _sub(push_hour=9, push_minute=0, weekdays="1,5")
        self.assertTrue(is_due(self._now(9, 0, weekday=0), sub))   # 周一
        self.assertTrue(is_due(self._now(9, 0, weekday=4), sub))   # 周五
        self.assertFalse(is_due(self._now(9, 0, weekday=2), sub))  # 周三

    def test_already_pushed_same_time(self):
        sub = _sub(push_hour=9, push_minute=0,
                   last_pushed_at="2026-08-10 09:00:00")
        self.assertFalse(is_due(self._now(9, 0), sub))

    def test_previous_day_does_not_block(self):
        sub = _sub(push_hour=9, push_minute=0,
                   last_pushed_at="2026-08-09 09:00:00")
        self.assertTrue(is_due(self._now(9, 0), sub))

    def test_everyday_default(self):
        sub = _sub(push_hour=9, push_minute=0, weekdays="")
        for wd in range(7):
            self.assertTrue(is_due(self._now(9, 0, weekday=wd), sub))


class ExecuteSubscriptionTests(unittest.TestCase):
    """执行链：变化检测 → 组装 → 推送 → 更新快照"""

    def setUp(self):
        import tempfile, os
        from dashboard.subscription_store import SubscriptionStore
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._db_path = path
        self._store = SubscriptionStore(db_path=path)
        self.patch_store = mock.patch(
            "dashboard.subscription_store.get_subscription_store",
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

    def test_pushes_and_updates_snapshot(self):
        sub_id = self._store.create(_sub(alert_mode="always"))
        sub = self._store.get(sub_id)
        with mock.patch("dashboard.service.collect_and_parse",
                        return_value=(_parsed(), [])), \
             mock.patch("dashboard.service.assemble",
                        return_value="# 看板"), \
             mock.patch("dashboard.service.push",
                        return_value=(True, "")), \
             mock.patch("dashboard.service.now_str",
                        return_value="2026-08-10 09:00:00"):
            result = _execute_subscription(sub)
        self.assertTrue(result["ok"])
        got = self._store.get(sub_id)
        self.assertEqual(got.last_pushed_at, "2026-08-10 09:00:00")
        self.assertIsNotNone(got.last_snapshot)

    def test_changes_only_no_change_is_silent(self):
        sub_id = self._store.create(_sub(alert_mode="changes_only"))
        sub = self._store.get(sub_id)
        sub.last_snapshot = [{
            "source_key": "project_status", "table_name": "33周", "total": 1,
            "status_counts": {"滞后": 1},
            "attention": [{"status": "滞后", "title": "项目A"}],
            "normal": [],
        }]
        with mock.patch("dashboard.service.collect_and_parse",
                        return_value=(_parsed(), [])), \
             mock.patch("dashboard.service.push") as m_push:
            result = _execute_subscription(sub)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "no_changes")
        m_push.assert_not_called()

    def test_changes_only_with_change_pushes(self):
        sub_id = self._store.create(_sub(alert_mode="changes_only"))
        sub = self._store.get(sub_id)
        sub.last_snapshot = [{
            "source_key": "project_status", "table_name": "33周", "total": 9,
            "status_counts": {}, "attention": [], "normal": [],
        }]
        with mock.patch("dashboard.service.collect_and_parse",
                        return_value=(_parsed(), [])), \
             mock.patch("dashboard.service.assemble", return_value="# 看板"), \
             mock.patch("dashboard.service.push", return_value=(True, "")):
            result = _execute_subscription(sub)
        self.assertTrue(result["ok"])

    def test_always_no_change_still_pushes_with_banner(self):
        """always：无变化也必推，顶部注入「📌 今日无变化」（v1.11.4）"""
        sub_id = self._store.create(_sub(alert_mode="always"))
        sub = self._store.get(sub_id)
        sub.last_snapshot = [{
            "source_key": "project_status", "table_name": "33周", "total": 1,
            "status_counts": {"滞后": 1},
            "attention": [{"status": "滞后", "title": "项目A"}],
            "normal": [],
        }]
        with mock.patch("dashboard.service.collect_and_parse",
                        return_value=(_parsed(), [])), \
             mock.patch("dashboard.service.assemble", return_value="# 看板"), \
             mock.patch("dashboard.service.push",
                        return_value=(True, "")) as m_push:
            result = _execute_subscription(sub)
        self.assertTrue(result["ok"])
        sent_text = m_push.call_args[0][2]  # push(recipients, title, text)
        self.assertTrue(sent_text.startswith("📌 今日无变化"))

    def test_always_change_injects_change_banner(self):
        """always：有变化顶部注入「📌 今日变化」+ 板块名（v1.11.4）"""
        sub_id = self._store.create(_sub(alert_mode="always"))
        sub = self._store.get(sub_id)
        sub.last_snapshot = [{
            "source_key": "project_status", "table_name": "33周", "total": 9,
            "status_counts": {}, "attention": [], "normal": [],
        }]
        with mock.patch("dashboard.service.collect_and_parse",
                        return_value=(_parsed(), [])), \
             mock.patch("dashboard.service.assemble", return_value="# 看板"), \
             mock.patch("dashboard.service.push",
                        return_value=(True, "")) as m_push:
            result = _execute_subscription(sub)
        self.assertTrue(result["ok"])
        sent_text = m_push.call_args[0][2]
        self.assertTrue(sent_text.startswith("📌 今日变化："))
        self.assertIn("研发项目现况表", sent_text)
        self.assertIn("总数 9→1", sent_text)

    def test_off_mode_skips(self):
        sub_id = self._store.create(_sub(alert_mode="off"))
        sub = self._store.get(sub_id)
        with mock.patch("dashboard.service.collect_and_parse",
                        return_value=(_parsed(), [])), \
             mock.patch("dashboard.service.push") as m_push:
            result = _execute_subscription(sub)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "off")
        m_push.assert_not_called()

    def test_no_sources_skips(self):
        sub_id = self._store.create(_sub(data_sources=["missing"]))
        sub = self._store.get(sub_id)
        with mock.patch("dashboard.service.collect_and_parse") as m_coll:
            result = _execute_subscription(sub)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "no_sources")
        m_coll.assert_not_called()

    def test_push_failure_keeps_snapshot_unchanged(self):
        sub_id = self._store.create(_sub(alert_mode="always"))
        sub = self._store.get(sub_id)
        with mock.patch("dashboard.service.collect_and_parse",
                        return_value=(_parsed(), [])), \
             mock.patch("dashboard.service.assemble", return_value="# 看板"), \
             mock.patch("dashboard.service.push", return_value=(False, "权限不足")):
            result = _execute_subscription(sub)
        self.assertFalse(result["ok"])
        self.assertIn("权限不足", result["reason"])
        got = self._store.get(sub_id)
        self.assertIsNone(got.last_snapshot)   # 失败不更新快照，下次可重试
        self.assertEqual(got.last_pushed_at, "")


class DocSubscriptionExecuteTests(unittest.TestCase):
    """订阅带 doc_* 动态源 key 的执行链"""

    def setUp(self):
        import tempfile
        from dashboard.subscription_store import SubscriptionStore
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._db_path = path
        self._store = SubscriptionStore(db_path=path)
        self.patch_store = mock.patch(
            "dashboard.subscription_store.get_subscription_store",
            return_value=self._store)
        self.patch_store.start()
        self.addCleanup(self.patch_store.stop)
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        self._store.close()
        for suffix in ("", "-wal", "-shm"):
            p = self._db_path + suffix
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

    def test_doc_key_resolved_and_executed(self):
        from dashboard.config_model import SourceConfig
        sub_id = self._store.create(
            _sub(data_sources=["doc_1"], alert_mode="always"))
        sub = self._store.get(sub_id)
        dyn = SourceConfig(key="doc_1", name="研发项目现况表", kind="notable",
                           base_id="n1", table_id="s1", operator_id="union1")
        with mock.patch("dashboard.service.resolve_subscription_sources",
                        return_value=[dyn]), \
             mock.patch("dashboard.service.collect_and_parse",
                        return_value=(_parsed(), [])), \
             mock.patch("dashboard.service.assemble", return_value="# 看板"), \
             mock.patch("dashboard.service.push", return_value=(True, "")):
            result = _execute_subscription(sub)
        self.assertTrue(result["ok"])
        got = self._store.get(sub_id)
        self.assertIsNotNone(got.last_snapshot)


if __name__ == "__main__":
    unittest.main()
