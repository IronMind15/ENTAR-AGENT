"""动态看板数据源注册表测试（v1.11.0）—— 临时 SQLite，不打真实库"""

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"

from scripts.dashboard.doc_candidates import DocCandidate, DocCandidateStore  # noqa: E402


class DocCandidateStoreTests(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._db_path = path
        self.store = DocCandidateStore(db_path=path)
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        self.store.close()
        for suffix in ("", "-wal", "-shm"):
            p = self._db_path + suffix
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

    def _cand(self, **kw):
        base = DocCandidate(
            user_id="u1", url="https://alidocs.dingtalk.com/i/nodes/n1",
            node_id="n1", kind="notable", operator_union="u1")
        for k, v in kw.items():
            setattr(base, k, v)
        return base

    def test_add_roundtrip_new_columns(self):
        self.store.add(self._cand(
            field_map='{"a1": {"label": "名称", "type": "string", "max_len": 200}}'))
        cand = self.store.get_pending("u1")
        self.assertEqual(cand.kind, "notable")
        self.assertTrue(cand.enabled)
        self.assertIn("a1", cand.field_map)

    def test_legacy_table_migration(self):
        """老库（无新列）初始化时应 ALTER TABLE 补齐，add 不崩"""
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        conn = sqlite3.connect(path)
        conn.execute("""
            CREATE TABLE dashboard_doc_candidates (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id        TEXT NOT NULL,
                url            TEXT NOT NULL,
                node_id        TEXT DEFAULT '',
                sheet_id       TEXT DEFAULT '',
                kind           TEXT DEFAULT '',
                operator_union TEXT DEFAULT '',
                records_count  INTEGER DEFAULT 0,
                learned        INTEGER DEFAULT 0,
                created_at     TEXT DEFAULT '',
                UNIQUE(user_id, url)
            )""")
        conn.commit()
        conn.close()
        store = DocCandidateStore(db_path=path)
        store.add(self._cand())
        cand = store.get_pending("u1")
        self.assertTrue(cand.enabled)
        self.assertEqual(cand.field_map, "{}")
        store.close()
        for suffix in ("", "-wal", "-shm"):
            p = path + suffix
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

    def test_list_dashboard_ready_filters(self):
        self.store.add(self._cand(node_id="n1", kind="notable", url="u1"))
        self.store.add(self._cand(node_id="n2", kind="workbook", url="u2"))
        self.store.add(self._cand(node_id="n3", kind="doc", url="u3"))
        self.store.add(self._cand(node_id="n4", kind="notable",
                                  enabled=False, url="u4"))
        ready = self.store.list_dashboard_ready("u1")
        # v1.11.1：doc 也纳入看板源
        self.assertEqual({c.node_id for c in ready}, {"n1", "n2", "n3"})

    def test_set_enabled(self):
        cid = self.store.add(self._cand())
        self.store.set_enabled(cid, False)
        self.assertFalse(self.store.get(cid).enabled)
        self.assertEqual(self.store.list_dashboard_ready("u1"), [])

    def test_list_all_enabled(self):
        self.store.add(self._cand(node_id="n1", kind="notable", url="u1"))
        self.store.add(self._cand(node_id="n2", kind="notable",
                                  enabled=False, url="u2"))
        self.assertEqual(len(self.store.list_all_enabled()), 1)

    def test_list_all_enabled_user_scoped(self):
        """v1.11.10：query 工具按用户取数据源，不能把别人的动态源带进来"""
        self.store.add(self._cand(node_id="n1", kind="notable", url="u1"))
        self.store.add(self._cand(node_id="n2", kind="notable", url="u2",
                                  user_id="u2"))
        self.assertEqual({c.node_id for c in self.store.list_all_enabled()},
                         {"n1", "n2"})
        self.assertEqual({c.node_id for c in self.store.list_all_enabled("u1")},
                         {"n1"})
        self.assertEqual({c.node_id for c in self.store.list_all_enabled("u2")},
                         {"n2"})


class FindByTests(unittest.TestCase):
    """v1.11.3 find_by_*：总结工具按 node_id/url/name 定位候选"""

    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._db_path = path
        self.store = DocCandidateStore(db_path=path)
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        self.store.close()
        for suffix in ("", "-wal", "-shm"):
            p = self._db_path + suffix
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

    def _cand(self, user_id="u1", node_id="n1", url="https://alidocs.dingtalk.com/i/nodes/n1",
              name="33周汇总"):
        return DocCandidate(user_id=user_id, url=url, node_id=node_id,
                            kind="notable", operator_union="u1", name=name)

    def test_find_by_node_id(self):
        self.store.add(self._cand(node_id="n1"))
        cand = self.store.find_by_node_id("u1", "n1")
        self.assertIsNotNone(cand)
        self.assertEqual(cand.node_id, "n1")
        self.assertIsNone(self.store.find_by_node_id("u1", "nope"))
        self.assertIsNone(self.store.find_by_node_id("u2", "n1"))  # 跨用户隔离

    def test_find_by_url_exact(self):
        self.store.add(self._cand(url="https://alidocs.dingtalk.com/i/nodes/n1"))
        cand = self.store.find_by_url("u1", "https://alidocs.dingtalk.com/i/nodes/n1")
        self.assertIsNotNone(cand)

    def test_find_by_url_node_id_fallback(self):
        """URL 变体（带参数）也能按 node_id 兜底命中"""
        self.store.add(self._cand(url="https://alidocs.dingtalk.com/i/nodes/n1"))
        cand = self.store.find_by_url(
            "u1", "https://alidocs.dingtalk.com/i/nodes/n1?sheet=s1&view=grid")
        self.assertIsNotNone(cand)
        self.assertEqual(cand.node_id, "n1")

    def test_find_by_name(self):
        self.store.add(self._cand(name="33周汇总"))
        cand = self.store.find_by_name("u1", "33周汇总")
        self.assertIsNotNone(cand)
        self.assertIsNone(self.store.find_by_name("u1", "不存在的表"))


if __name__ == "__main__":
    unittest.main()
