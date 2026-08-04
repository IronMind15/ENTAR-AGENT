"""崩溃恢复测试（recovery.plan_recovery 纯逻辑 + recover_crashed_data 端到端）"""

import sys
import threading
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from doc_mgr.storage import ChromaStore
from doc_mgr import recovery


class FakeCollection:
    """内存版 Chroma collection（恢复只需 get/update/delete）"""

    def __init__(self):
        self.rows = {}

    def get(self, ids=None, where=None):
        selected = []
        requested = set(ids) if ids else None
        for item_id, row in self.rows.items():
            if requested is not None and item_id not in requested:
                continue
            selected.append((item_id, row))
        return {
            "ids": [x[0] for x in selected],
            "documents": [x[1]["document"] for x in selected],
            "metadatas": [x[1]["metadata"] for x in selected],
        }

    def update(self, ids, metadatas):
        for item_id, metadata in zip(ids, metadatas):
            self.rows[item_id]["metadata"] = dict(metadata)

    def delete(self, ids=None, where=None):
        if ids:
            for item_id in ids:
                self.rows.pop(item_id, None)


def make_store(collection):
    store = ChromaStore.__new__(ChromaStore)
    store._visibility_lock = threading.RLock()
    store._replace_lock = threading.RLock()
    store._get_collection = lambda _: collection
    return store


def _meta(doc_id, version_id, state):
    return {"doc_id": doc_id, "version_id": version_id, "version_state": state}


class PlanRecoveryTests(unittest.TestCase):
    """plan_recovery 纯逻辑"""

    def test_staging_cleanup(self):
        blocks = [
            ("old-1", _meta("doc-1", "v-old", "active")),
            ("new-1", _meta("doc-1", "v-new", "staging")),
            ("new-2", _meta("doc-1", "v-new", "staging")),
        ]
        plan = recovery.plan_recovery(blocks)
        self.assertEqual(["new-1", "new-2"], plan["delete_ids"])
        self.assertEqual([], plan["activate"])

    def test_partial_activate_cleanup(self):
        """新版本部分 active 部分 staging + 旧版本 active → 新版本整组作废"""
        blocks = [
            ("new-a", _meta("doc-1", "v-new", "active")),
            ("new-s", _meta("doc-1", "v-new", "staging")),
            ("old", _meta("doc-1", "v-old", "active")),
        ]
        plan = recovery.plan_recovery(blocks)
        self.assertEqual({"new-a", "new-s"}, set(plan["delete_ids"]))
        self.assertNotIn("old", plan["delete_ids"])
        self.assertEqual([], plan["activate"])

    def test_retired_cleanup_when_active_exists(self):
        blocks = [
            ("new", _meta("doc-1", "v-new", "active")),
            ("old", _meta("doc-1", "v-old", "retired")),
        ]
        plan = recovery.plan_recovery(blocks)
        self.assertEqual(["old"], plan["delete_ids"])

    def test_restore_retired_when_no_active(self):
        """只有 retired 无 active → 恢复为 active（防数据丢失）"""
        blocks = [
            ("r1", _meta("doc-1", "v-old", "retired")),
            ("r2", _meta("doc-1", "v-old", "retired")),
        ]
        plan = recovery.plan_recovery(blocks)
        self.assertEqual([], plan["delete_ids"])
        self.assertEqual(["r1", "r2"], [b[0] for b in plan["activate"]])
        for _, meta in plan["activate"]:
            self.assertEqual("active", meta["version_state"])
            self.assertEqual("v-old", meta["version_id"])

    def test_mixed_group_restore_when_no_active(self):
        """同版本组内 active+retired 混合、无可见版本 → 全部恢复 active"""
        blocks = [
            ("a", _meta("doc-1", "v-old", "active")),
            ("r", _meta("doc-1", "v-old", "retired")),
        ]
        plan = recovery.plan_recovery(blocks)
        self.assertEqual([], plan["delete_ids"])
        self.assertEqual({"a", "r"}, {b[0] for b in plan["activate"]})

    def test_multiple_active_keep_largest(self):
        """多 active 版本 → 保留块数最多的，其余删除"""
        blocks = [
            ("a1", _meta("doc-1", "v-a", "active")),
            ("a2", _meta("doc-1", "v-a", "active")),
            ("a3", _meta("doc-1", "v-a", "active")),
            ("b1", _meta("doc-1", "v-b", "active")),
            ("b2", _meta("doc-1", "v-b", "active")),
        ]
        plan = recovery.plan_recovery(blocks)
        self.assertEqual(["b1", "b2"], plan["delete_ids"])

    def test_legacy_untouched(self):
        """无 doc_id / 无版本机制的 legacy 数据不动"""
        blocks = [
            ("legacy", {"kind": "old"}),
            ("normal", _meta("doc-1", "v", "active")),
        ]
        plan = recovery.plan_recovery(blocks)
        self.assertEqual([], plan["delete_ids"])
        self.assertEqual([], plan["activate"])

    def test_multi_doc_isolation(self):
        """不同 doc 的残留互不影响"""
        blocks = [
            ("a-new", _meta("doc-a", "v-new", "staging")),
            ("a-old", _meta("doc-a", "v-old", "active")),
            ("b-ok", _meta("doc-b", "v-1", "active")),
        ]
        plan = recovery.plan_recovery(blocks)
        self.assertEqual(["a-new"], plan["delete_ids"])
        self.assertEqual([], plan["activate"])


class RecoverEndToEndTests(unittest.TestCase):
    """recover_crashed_data 通过 store 端到端"""

    def test_cleanup_retired_flow(self):
        collection = FakeCollection()
        collection.rows = {
            "new": {"document": "新", "metadata": _meta("doc-1", "v-new", "active")},
            "old": {"document": "旧", "metadata": _meta("doc-1", "v-old", "retired")},
            "legacy": {"document": "legacy", "metadata": {"kind": "old"}},
        }
        store = make_store(collection)
        stats = recovery.recover_crashed_data(store, collections=["standards"])
        self.assertEqual({"standards": {"deleted": 1, "restored": 0}}, stats)
        self.assertNotIn("old", collection.rows)
        self.assertIn("new", collection.rows)
        self.assertIn("legacy", collection.rows)

    def test_restore_flow(self):
        collection = FakeCollection()
        collection.rows = {
            "r1": {"document": "旧内容", "metadata": _meta("doc-1", "v-old", "retired")},
        }
        store = make_store(collection)
        stats = recovery.recover_crashed_data(store, collections=["standards"])
        self.assertEqual({"standards": {"deleted": 0, "restored": 1}}, stats)
        self.assertEqual("active", collection.rows["r1"]["metadata"]["version_state"])


if __name__ == "__main__":
    unittest.main()
