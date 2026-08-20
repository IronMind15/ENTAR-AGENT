"""看板推送留档测试（v1.12.7）—— 留档写入、每任务保留 30 次、历史回放查询"""

import os
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from dashboard.push_history import (  # noqa: E402
    KEEP_RECENT, PushHistoryStore, join_messages,
)


class _TempDB(unittest.TestCase):
    def setUp(self):
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._db_path = path
        self._store = PushHistoryStore(db_path=path)
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


class RecordAndTrimTests(_TempDB):
    def test_record_creates_history(self):
        h = self._store.record(1, "u1", "恩特能源每日项目看板",
                               ["第一条", "第二条"],
                               prompt_version="task-prompt-user-v1",
                               prompt_hash="abc123")
        self.assertEqual(h.sub_id, 1)
        self.assertEqual(h.message_count, 2)
        self.assertIn("第一条", h.content)
        self.assertIn("第二条", h.content)
        self.assertEqual("task-prompt-user-v1", h.prompt_version)
        self.assertEqual("abc123", h.prompt_hash)
        self.assertEqual(self._store.count(1), 1)

    def test_join_messages_separates(self):
        self.assertEqual(join_messages(["a", "b"]), "a\n\n──────────\n\nb")
        # 空消息忽略，不产生空行碎片
        self.assertEqual(join_messages(["a", "", None, "b"]),
                         "a\n\n──────────\n\nb")

    def test_latest_returns_most_recent(self):
        self._store.record(1, "u1", "t1", ["第1次"])
        self._store.record(1, "u1", "t2", ["第2次"])
        latest = self._store.latest(1)
        self.assertEqual(latest.content, "第2次")

    def test_keep_recent_30_per_task(self):
        """v1.12.7 拍板：每个任务保留最近 30 次，超出自动裁剪最旧"""
        for i in range(35):
            self._store.record(1, "u1", f"t{i}", [f"内容{i}"])
        self.assertEqual(self._store.count(1), KEEP_RECENT)
        self.assertEqual(self._store.count(1), 30)
        # 保留的是最近 30 次
        latest = self._store.latest(1)
        self.assertEqual(latest.content, "内容34")

    def test_keep_recent_per_task_isolated(self):
        """保留按任务隔离：任务 2 的留档不受任务 1 裁剪影响"""
        for i in range(35):
            self._store.record(1, "u1", "t1", ["1"])
        self._store.record(2, "u1", "t2", ["任务2"])
        self.assertEqual(self._store.count(1), 30)
        self.assertEqual(self._store.count(2), 1)

    def test_list_for_sub_desc(self):
        self._store.record(1, "u1", "t1", ["1"])
        self._store.record(1, "u1", "t2", ["2"])
        items = self._store.list_for_sub(1, limit=10)
        self.assertEqual([h.content for h in items], ["2", "1"])


class HistoryQueryTests(_TempDB):
    def test_latest_none_when_empty(self):
        self.assertIsNone(self._store.latest(99))

    def test_list_for_owner_across_tasks(self):
        self._store.record(1, "u1", "t1", ["1"])
        self._store.record(2, "u1", "t2", ["2"])
        items = self._store.list_for_owner("u1", limit=10)
        self.assertEqual(len(items), 2)


if __name__ == "__main__":
    unittest.main()
