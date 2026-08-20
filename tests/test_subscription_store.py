"""看板订阅存储测试（v1.11.0）—— 临时 SQLite，不打真实库"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"

from scripts.dashboard.subscription_store import Subscription, SubscriptionStore  # noqa: E402


class SubscriptionStoreTests(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._db_path = path
        self.store = SubscriptionStore(db_path=path)
        self.addCleanup(lambda: self._cleanup())

    def _cleanup(self):
        """先关连接释放句柄（Windows 锁文件）再删"""
        self.store.close()
        for suffix in ("", "-wal", "-shm"):
            p = self._db_path + suffix
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

    def _sub(self, **kw):
        base = Subscription(
            owner_user_id="user001",
            owner_staff_id="staff001",
            owner_union_id="union001",
            data_sources=["project_status", "test_issues"],
            push_hour=9,
            push_minute=0,
            weekdays="",
            alert_mode="changes_only",
            recipients=["staff001"],
            title="恩特能源每日项目看板",
        )
        for k, v in kw.items():
            setattr(base, k, v)
        return base

    def test_create_and_get_roundtrip(self):
        sub_id = self.store.create(self._sub())
        self.assertGreater(sub_id, 0)
        got = self.store.get(sub_id)
        self.assertEqual(got.owner_user_id, "user001")
        self.assertEqual(got.owner_staff_id, "staff001")
        self.assertEqual(got.owner_union_id, "union001")
        self.assertEqual(got.data_sources, ["project_status", "test_issues"])
        self.assertEqual(got.recipients, ["staff001"])
        self.assertEqual(got.push_hour, 9)
        self.assertEqual(got.push_minute, 0)
        self.assertEqual(got.alert_mode, "changes_only")
        self.assertTrue(got.enabled)

    def test_list_all_and_enabled(self):
        self.store.create(self._sub())
        self.store.create(self._sub(owner_user_id="user002", enabled=False))
        self.assertEqual(len(self.store.list_all()), 2)
        self.assertEqual(len(self.store.list_enabled()), 1)
        self.assertEqual(self.store.list_enabled()[0].owner_user_id, "user001")

    def test_list_for_owner(self):
        self.store.create(self._sub())
        self.store.create(self._sub(owner_user_id="user002"))
        owned = self.store.list_for_owner("user002")
        self.assertEqual(len(owned), 1)
        self.assertEqual(owned[0].owner_user_id, "user002")

    def test_update(self):
        sub_id = self.store.create(self._sub())
        sub = self.store.get(sub_id)
        sub.push_hour = 10
        sub.push_minute = 30
        sub.weekdays = "1,5"
        sub.recipients = ["staff001", "staff002"]
        self.store.update(sub)
        got = self.store.get(sub_id)
        self.assertEqual(got.push_hour, 10)
        self.assertEqual(got.push_minute, 30)
        self.assertEqual(got.weekdays, "1,5")
        self.assertEqual(got.recipients, ["staff001", "staff002"])

    def test_per_source_roundtrip_and_update(self):
        # v1.12.5：每源独立总结字段持久化 + 可切换
        sub_id = self.store.create(self._sub(per_source=True))
        got = self.store.get(sub_id)
        self.assertTrue(got.per_source)
        got.per_source = False
        self.store.update(got)
        self.assertFalse(self.store.get(sub_id).per_source)

    def test_per_source_in_fingerprint(self):
        # v1.12.6：输出模式不同视为不同业务配置（合并 vs 逐源可并存）
        merged = self._sub(per_source=False)
        per_source = self._sub(per_source=True)
        self.assertNotEqual(merged.fingerprint(), per_source.fingerprint())

    def test_set_snapshot(self):
        sub_id = self.store.create(self._sub())
        snap = [{"source_key": "s1", "total": 7, "status_counts": {"滞后": 4}}]
        self.store.set_snapshot(sub_id, snap, last_pushed_at="2026-08-10 09:00:00")
        got = self.store.get(sub_id)
        self.assertEqual(got.last_snapshot, snap)
        self.assertEqual(got.last_pushed_at, "2026-08-10 09:00:00")

    def test_set_enabled(self):
        sub_id = self.store.create(self._sub())
        self.store.set_enabled(sub_id, False)
        self.assertFalse(self.store.get(sub_id).enabled)
        self.assertEqual(self.store.list_enabled(), [])

    def test_delete(self):
        sub_id = self.store.create(self._sub())
        self.store.delete(sub_id)
        self.assertIsNone(self.store.get(sub_id))

    def test_missing_subscription_returns_none(self):
        self.assertIsNone(self.store.get(999))


if __name__ == "__main__":
    unittest.main()
