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
from dashboard_scheduler import (_deduplicate_due_subscriptions,
                                 _execute_subscription, is_due)  # noqa: E402


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
        # v1.12.x（审查 High 4）：±5 分钟宽容窗口——9:05 距 9:00 恰在窗口边缘
        self.assertFalse(is_due(self._now(9, 6), sub))   # 超出 ±5 窗口
        self.assertFalse(is_due(self._now(8, 0), sub))   # 差一小时

    def test_due_window_gap_up_to_5_minutes(self):
        """±5 分钟宽容窗口：单次 tick 卡顿/错过精确分钟仍可在窗口内补推"""
        sub = _sub(push_hour=9, push_minute=0)
        self.assertTrue(is_due(self._now(9, 4), sub))
        self.assertTrue(is_due(self._now(9, 5), sub))    # 窗口边缘（≤5）
        self.assertFalse(is_due(self._now(9, 6), sub))   # 超出窗口
        self.assertTrue(is_due(self._now(8, 56), sub))   # 提前方向窗口内（差4）
        self.assertTrue(is_due(self._now(8, 55), sub))   # 提前方向窗口边缘（差5，与9:05对称）
        self.assertFalse(is_due(self._now(8, 54), sub))  # 提前方向超出（差6）

    def test_due_window_dedup_within_window(self):
        """窗口内已推过（last_pushed_at 距 now ≤5 分钟）→ 不重复推送"""
        sub = _sub(push_hour=9, push_minute=0,
                   last_pushed_at="2026-08-10 08:59:00")
        # 09:04 距 scheduled 4 分钟到期，但距 last_pushed 5 分钟 → 防重入拦截
        self.assertFalse(is_due(self._now(9, 4), sub))
        # 昨天的推送不拦截今天窗口内到期
        yesterday = _sub(push_hour=9, push_minute=0,
                         last_pushed_at="2026-08-09 09:01:00")
        self.assertTrue(is_due(self._now(9, 3), yesterday))

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

    def test_subset_subscriptions_are_suppressed_for_same_delivery(self):
        full = _sub(id=5, data_sources=["doc_1", "doc_2", "doc_3"])
        subset_a = _sub(id=4, data_sources=["doc_1", "doc_2"])
        subset_b = _sub(id=6, data_sources=["doc_3"])
        # v1.12.7（D1）：去重按任务绑定解析的有效来源集合——mock effective_source_keys
        # 以绑定快照为解析结果，覆盖集合语义与旧实现一致。
        with mock.patch("dashboard.service.effective_source_keys",
                        side_effect=lambda sub: set(sub.data_sources or [])):
            selected, suppressed = _deduplicate_due_subscriptions(
                [subset_a, full, subset_b])
        self.assertEqual([sub.id for sub in selected], [5])
        self.assertEqual(set(suppressed), {(4, 5), (6, 5)})


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

    def _fake_sources(self):
        """v1.11.5：静态配置 project_status base_id 为空会被 source_usable 过滤，
        执行链测试直接 mock 数据源解析，聚焦变化检测→组装→推送→快照。"""
        from dashboard.config_model import SourceConfig
        return [SourceConfig(key="project_status", name="研发项目现况表",
                             kind="notable", base_id="b1", table_id="s1")]

    def test_pushes_and_updates_snapshot(self):
        sub_id = self._store.create(_sub(alert_mode="always"))
        sub = self._store.get(sub_id)
        with mock.patch("dashboard.service.resolve_subscription_sources",
                        return_value=self._fake_sources()), \
             mock.patch("dashboard.service.collect_and_parse",
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

    def test_push_writes_history_archive(self):
        """v1.12.7：推送成功后写留档（每任务保留 30 次）"""
        import tempfile
        from dashboard.push_history import PushHistoryStore
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        hist = PushHistoryStore(db_path=path)
        self.patch_hist = mock.patch(
            "dashboard.push_history.get_push_history_store", return_value=hist)
        self.patch_hist.start()
        self.addCleanup(self.patch_hist.stop)
        self.addCleanup(lambda: (hist.close(),
                                 *[os.path.exists(path + s) and os.remove(path + s)
                                   for s in ("", "-wal", "-shm")]))
        sub_id = self._store.create(_sub(alert_mode="always"))
        sub = self._store.get(sub_id)
        with mock.patch("dashboard.service.resolve_subscription_sources",
                        return_value=self._fake_sources()), \
             mock.patch("dashboard.service.collect_and_parse",
                        return_value=(_parsed(), [])), \
             mock.patch("dashboard.service.assemble",
                        return_value="# 看板"), \
             mock.patch("dashboard.service.push",
                        return_value=(True, "")), \
             mock.patch("dashboard.service.now_str",
                        return_value="2026-08-10 09:00:00"):
            result = _execute_subscription(sub)
        self.assertTrue(result["ok"])
        self.assertEqual(hist.count(sub_id), 1)
        latest = hist.latest(sub_id)
        self.assertIsNotNone(latest)
        self.assertEqual(latest.sub_id, sub_id)

    def test_changes_only_no_change_is_silent(self):
        sub_id = self._store.create(_sub(alert_mode="changes_only"))
        sub = self._store.get(sub_id)
        sub.last_snapshot = [{
            "source_key": "project_status", "table_name": "33周", "total": 1,
            "status_counts": {"滞后": 1},
            "attention": [{"status": "滞后", "title": "项目A"}],
            "normal": [],
        }]
        with mock.patch("dashboard.service.resolve_subscription_sources",
                        return_value=self._fake_sources()), \
             mock.patch("dashboard.service.collect_and_parse",
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
        with mock.patch("dashboard.service.resolve_subscription_sources",
                        return_value=self._fake_sources()), \
             mock.patch("dashboard.service.collect_and_parse",
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
        with mock.patch("dashboard.service.resolve_subscription_sources",
                        return_value=self._fake_sources()), \
             mock.patch("dashboard.service.collect_and_parse",
                        return_value=(_parsed(), [])), \
             mock.patch("dashboard.service.assemble", return_value="# 看板"), \
             mock.patch("dashboard.service.push",
                        return_value=(True, "")) as m_push:
            result = _execute_subscription(sub)
        self.assertTrue(result["ok"])
        sent_text = m_push.call_args[0][2]  # push(recipients, title, text)
        self.assertTrue(sent_text.startswith("📌 今日无变化"))

    def test_key_sources_expanded_into_messages(self):
        """v1.12.5：汇总报告之外，变化最多 Top N 关键源单独展开一条；空变化不展开"""
        from types import SimpleNamespace
        sub_id = self._store.create(_sub(alert_mode="always"))
        sub = self._store.get(sub_id)
        sub.last_snapshot = [{
            "source_key": "project_status", "table_name": "33周", "total": 1,
            "status_counts": {"滞后": 1}, "attention": [], "normal": [],
        }]
        fake_report = SimpleNamespace(
            messages=["汇总报告"], changes=[
                {"source_key": "project_status", "source_name": "研发项目现况表",
                 "change": "baseline", "record_changes": [
                     {"change": "added", "source_key": "project_status",
                      "record_id": "r1", "fields": {}, "evidence": {}}]},
                # 空变化源（首次/baseline 无展开价值）→ 不单独展开
                {"source_key": "other", "source_name": "源二",
                 "change": "baseline", "record_changes": []},
            ], verification={})
        key_expand = SimpleNamespace(
            messages=["🔍 研发项目现况表 详情"], changes=[], verification={})
        with mock.patch("dashboard.service.resolve_subscription_sources",
                        return_value=self._fake_sources()), \
             mock.patch("dashboard.service.collect_and_parse",
                        return_value=(_parsed(), [])), \
             mock.patch("dashboard.service.assemble_report",
                        side_effect=[fake_report, key_expand]), \
             mock.patch("dashboard.service.push",
                        return_value=(True, "")) as m_push, \
             mock.patch("dashboard.service.now_str",
                        return_value="2026-08-10 09:00:00"):
            result = _execute_subscription(sub)
        self.assertTrue(result["ok"])
        texts = [c.args[2] for c in m_push.call_args_list]
        self.assertGreaterEqual(len(texts), 2)   # 汇总报告 + 关键源展开条
        self.assertIn("汇总报告", texts[0])
        self.assertTrue(any("🔍 研发项目现况表 详情" in t for t in texts))
        self.assertEqual(len(texts), 2)          # 空变化源不展开，共 2 条

    def test_per_source_uses_per_source_messages(self):
        """v1.12.5：per_source 订阅走逐源组装（不合并报告、不做关键源展开），
        变化 banner 注入第一条"""
        sub_id = self._store.create(_sub(alert_mode="always", per_source=True))
        sub = self._store.get(sub_id)
        sub.last_snapshot = [{
            "source_key": "project_status", "table_name": "33周", "total": 9,
            "status_counts": {}, "attention": [], "normal": [],
        }]
        per_source_msgs = ["【研发项目现况表】本周总结", "【整机下线】问题清单"]
        with mock.patch("dashboard.service.resolve_subscription_sources",
                        return_value=self._fake_sources()), \
             mock.patch("dashboard.service.collect_and_parse",
                        return_value=(_parsed(), [])), \
             mock.patch("dashboard.service.assemble_per_source_messages",
                        return_value=per_source_msgs) as m_ps, \
             mock.patch("dashboard.service.assemble_report") as m_report, \
             mock.patch("dashboard.service.push",
                        return_value=(True, "")) as m_push, \
             mock.patch("dashboard.service.now_str",
                        return_value="2026-08-10 09:00:00"):
            result = _execute_subscription(sub)
        self.assertTrue(result["ok"])
        m_ps.assert_called_once()            # 逐源组装被调用
        m_report.assert_not_called()         # 不合并成一份报告
        texts = [c.args[2] for c in m_push.call_args_list]
        self.assertEqual(len(texts), 2)      # 两个源 → 两条独立消息
        self.assertTrue(texts[0].startswith("📌 今日变化："))
        self.assertIn(per_source_msgs[0], texts[0])   # banner 注入第一条
        self.assertEqual(texts[1], per_source_msgs[1])

    def test_non_per_source_does_not_call_per_source(self):
        """v1.12.6：默认合并订阅不误走逐源组装"""
        from types import SimpleNamespace
        sub_id = self._store.create(_sub(alert_mode="always"))
        sub = self._store.get(sub_id)
        sub.last_snapshot = [{
            "source_key": "project_status", "table_name": "33周", "total": 1,
            "status_counts": {"滞后": 1}, "attention": [], "normal": [],
        }]
        with mock.patch("dashboard.service.resolve_subscription_sources",
                        return_value=self._fake_sources()), \
             mock.patch("dashboard.service.collect_and_parse",
                        return_value=(_parsed(), [])), \
             mock.patch("dashboard.service.assemble_per_source_messages") as m_ps, \
             mock.patch("dashboard.service.assemble_report",
                        return_value=SimpleNamespace(
                            messages=["汇总报告"], changes=[], verification={})), \
             mock.patch("dashboard.service.push",
                        return_value=(True, "")) as m_push, \
             mock.patch("dashboard.service.now_str",
                        return_value="2026-08-10 09:00:00"):
            result = _execute_subscription(sub)
        self.assertTrue(result["ok"])
        m_ps.assert_not_called()
        # always 模式第一页带「📌 今日变化」banner，汇总报告在其后
        self.assertTrue(m_push.call_args_list[0][0][2].endswith("汇总报告"))

    def test_always_change_injects_change_banner(self):
        """always：有变化顶部注入「📌 今日变化」+ 板块名（v1.11.4）"""
        sub_id = self._store.create(_sub(alert_mode="always"))
        sub = self._store.get(sub_id)
        sub.last_snapshot = [{
            "source_key": "project_status", "table_name": "33周", "total": 9,
            "status_counts": {}, "attention": [], "normal": [],
        }]
        with mock.patch("dashboard.service.resolve_subscription_sources",
                        return_value=self._fake_sources()), \
             mock.patch("dashboard.service.collect_and_parse",
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
        with mock.patch("dashboard.service.resolve_subscription_sources",
                        return_value=self._fake_sources()), \
             mock.patch("dashboard.service.collect_and_parse",
                        return_value=(_parsed(), [])), \
             mock.patch("dashboard.service.push") as m_push:
            result = _execute_subscription(sub)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "off")
        m_push.assert_not_called()

    def test_no_sources_skips(self):
        sub_id = self._store.create(_sub(data_sources=["missing"]))
        sub = self._store.get(sub_id)
        with mock.patch("dashboard.service.collect_and_parse") as m_coll, \
             mock.patch("dingtalk_notifier.DingTalkNotifier"):  # v1.11.6 失败告警
            result = _execute_subscription(sub)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "no_sources")
        m_coll.assert_not_called()

    def test_push_failure_keeps_snapshot_unchanged(self):
        sub_id = self._store.create(_sub(alert_mode="always"))
        sub = self._store.get(sub_id)
        with mock.patch("dashboard.service.resolve_subscription_sources",
                        return_value=self._fake_sources()), \
             mock.patch("dashboard.service.collect_and_parse",
                        return_value=(_parsed(), [])), \
             mock.patch("dashboard.service.assemble", return_value="# 看板"), \
             mock.patch("dashboard.service.push", return_value=(False, "权限不足")), \
             mock.patch("dingtalk_notifier.DingTalkNotifier"):  # v1.11.6 失败告警
            result = _execute_subscription(sub)
        self.assertFalse(result["ok"])
        self.assertIn("权限不足", result["reason"])
        got = self._store.get(sub_id)
        self.assertIsNone(got.last_snapshot)   # 失败不更新快照，下次可重试
        self.assertEqual(got.last_pushed_at, "")


class FailureAlertTests(unittest.TestCase):
    """失败告警（v1.11.6）：执行失败推送给创建人 + 管理员；正常静默不误告警"""

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

    def _fake_sources(self):
        from dashboard.config_model import SourceConfig
        return [SourceConfig(key="project_status", name="研发项目现况表",
                             kind="notable", base_id="b1", table_id="s1")]

    def test_push_failure_notifies_owner(self):
        sub_id = self._store.create(_sub(alert_mode="always"))
        sub = self._store.get(sub_id)
        with mock.patch("dashboard.service.resolve_subscription_sources",
                        return_value=self._fake_sources()), \
             mock.patch("dashboard.service.collect_and_parse",
                        return_value=(_parsed(), [])), \
             mock.patch("dashboard.service.assemble", return_value="# 看板"), \
             mock.patch("dashboard.service.push", return_value=(False, "权限不足")), \
             mock.patch("dingtalk_notifier.DingTalkNotifier") as m_ntf:
            result = _execute_subscription(sub)
        self.assertFalse(result["ok"])
        m_ntf.return_value.send_markdown_to_users.assert_called_once()
        recipients = m_ntf.return_value.send_markdown_to_users.call_args[0][0]
        self.assertIn("staff001", recipients)   # 告警含订阅创建人

    def test_no_sources_notifies_owner(self):
        sub_id = self._store.create(_sub(data_sources=["missing"]))
        sub = self._store.get(sub_id)
        with mock.patch("dashboard.service.collect_and_parse") as m_coll, \
             mock.patch("dingtalk_notifier.DingTalkNotifier") as m_ntf:
            result = _execute_subscription(sub)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "no_sources")
        m_ntf.return_value.send_markdown_to_users.assert_called_once()

    def test_no_changes_does_not_alert(self):
        """changes_only 无变化静默是正常行为，不告警"""
        sub_id = self._store.create(_sub(alert_mode="changes_only"))
        sub = self._store.get(sub_id)
        sub.last_snapshot = [{
            "source_key": "project_status", "table_name": "33周", "total": 1,
            "status_counts": {"滞后": 1},
            "attention": [{"status": "滞后", "title": "项目A"}],
            "normal": [],
        }]
        with mock.patch("dashboard.service.resolve_subscription_sources",
                        return_value=self._fake_sources()), \
             mock.patch("dashboard.service.collect_and_parse",
                        return_value=(_parsed(), [])), \
             mock.patch("dashboard.service.push"), \
             mock.patch("dingtalk_notifier.DingTalkNotifier") as m_ntf:
            result = _execute_subscription(sub)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "no_changes")
        m_ntf.return_value.send_markdown_to_users.assert_not_called()

    def test_off_does_not_alert(self):
        sub_id = self._store.create(_sub(alert_mode="off"))
        sub = self._store.get(sub_id)
        with mock.patch("dashboard.service.resolve_subscription_sources",
                        return_value=self._fake_sources()), \
             mock.patch("dashboard.service.collect_and_parse",
                        return_value=(_parsed(), [])), \
             mock.patch("dashboard.service.push"), \
             mock.patch("dingtalk_notifier.DingTalkNotifier") as m_ntf:
            result = _execute_subscription(sub)
        self.assertFalse(result["ok"])
        m_ntf.return_value.send_markdown_to_users.assert_not_called()


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
