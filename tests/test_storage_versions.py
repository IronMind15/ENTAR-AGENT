import sys
import threading
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"

from scripts.doc_mgr.storage import ChromaStore


class FakeCollection:
    def __init__(self):
        self.rows = {}
        self.add_calls = 0
        self.update_calls = 0
        self.fail_add_call = None
        self.fail_update_call = None
        self.last_query_where = None

    @staticmethod
    def _matches(meta, where):
        if not where:
            return True
        if "$and" in where:
            return all(FakeCollection._matches(meta, item) for item in where["$and"])
        for key, expected in where.items():
            actual = meta.get(key)
            if isinstance(expected, dict):
                if "$nin" in expected and actual in expected["$nin"]:
                    return False
                if "$ne" in expected and actual == expected["$ne"]:
                    return False
            elif actual != expected:
                return False
        return True

    def add(self, ids, documents, metadatas):
        self.add_calls += 1
        if self.add_calls == self.fail_add_call:
            raise RuntimeError("injected add failure")
        for item_id, document, metadata in zip(ids, documents, metadatas):
            if item_id in self.rows:
                raise RuntimeError(f"duplicate id: {item_id}")
            self.rows[item_id] = {
                "document": document,
                "metadata": dict(metadata),
            }

    def update(self, ids, metadatas):
        self.update_calls += 1
        if self.update_calls == self.fail_update_call:
            raise RuntimeError("injected update failure")
        for item_id, metadata in zip(ids, metadatas):
            self.rows[item_id]["metadata"] = dict(metadata)

    def delete(self, ids=None, where=None):
        if ids:
            for item_id in ids:
                self.rows.pop(item_id, None)
        elif where:
            for item_id in list(self.rows):
                if self._matches(self.rows[item_id]["metadata"], where):
                    self.rows.pop(item_id)

    def get(self, ids=None, where=None):
        selected = []
        requested = set(ids) if ids else None
        for item_id, row in self.rows.items():
            if requested is not None and item_id not in requested:
                continue
            if not self._matches(row["metadata"], where):
                continue
            selected.append((item_id, row))
        return {
            "ids": [item[0] for item in selected],
            "documents": [item[1]["document"] for item in selected],
            "metadatas": [item[1]["metadata"] for item in selected],
        }

    def query(self, **kwargs):
        self.last_query_where = kwargs.get("where")
        visible = self.get(where=self.last_query_where)
        limit = kwargs.get("n_results", 5)
        return {
            "ids": [visible["ids"][:limit]],
            "documents": [visible["documents"][:limit]],
            "metadatas": [visible["metadatas"][:limit]],
            "distances": [[0.1] * min(limit, len(visible["ids"]))],
        }


def make_store(collection):
    store = ChromaStore.__new__(ChromaStore)
    store._visibility_lock = threading.RLock()
    store._replace_lock = threading.RLock()
    store._get_collection = lambda _: collection
    return store


class VersionReplacementTests(unittest.TestCase):
    def test_successful_replacement_activates_new_and_removes_old(self):
        collection = FakeCollection()
        collection.rows["legacy-old"] = {
            "document": "旧内容",
            "metadata": {"file_name": "doc.pdf"},
        }
        store = make_store(collection)

        count = store.replace_document(
            "standards", "doc-1", "doc.pdf",
            ["chunk-a", "chunk-b"], ["新内容1", "新内容2"],
            [
                {"doc_id": "doc-1", "version_id": "hash-new", "chunk_index": 0},
                {"doc_id": "doc-1", "version_id": "hash-new", "chunk_index": 1},
            ],
        )

        self.assertEqual(2, count)
        self.assertNotIn("legacy-old", collection.rows)
        self.assertEqual(
            {"新内容1", "新内容2"},
            {row["document"] for row in collection.rows.values()},
        )
        self.assertTrue(all(
            row["metadata"]["version_state"] == "active"
            for row in collection.rows.values()
        ))

    def test_batch_write_failure_keeps_old_and_cleans_staging(self):
        collection = FakeCollection()
        collection.rows["old"] = {
            "document": "旧内容",
            "metadata": {"doc_id": "doc-1", "file_name": "doc.pdf"},
        }
        collection.fail_add_call = 2
        store = make_store(collection)
        documents = [f"new-{index}" for index in range(60)]
        metadatas = [
            {"doc_id": "doc-1", "version_id": "hash-new", "chunk_index": index}
            for index in range(60)
        ]

        with self.assertRaises(RuntimeError):
            store.replace_document(
                "standards", "doc-1", "doc.pdf",
                [f"chunk-{index}" for index in range(60)],
                documents, metadatas,
            )

        self.assertEqual(["old"], list(collection.rows))
        self.assertEqual("旧内容", collection.rows["old"]["document"])

    def test_switch_failure_rolls_back_new_and_keeps_old_visible(self):
        collection = FakeCollection()
        collection.rows["old"] = {
            "document": "旧内容",
            "metadata": {"doc_id": "doc-1", "file_name": "doc.pdf"},
        }
        collection.fail_update_call = 2
        store = make_store(collection)

        with self.assertRaises(RuntimeError):
            store.replace_document(
                "standards", "doc-1", "doc.pdf",
                ["chunk"], ["新内容"],
                [{"doc_id": "doc-1", "version_id": "hash-new"}],
            )

        visible = store.get("standards")
        self.assertEqual(["old"], visible["ids"])
        self.assertEqual(["旧内容"], visible["documents"])

    def test_queries_hide_staging_and_retired_but_keep_legacy(self):
        collection = FakeCollection()
        collection.rows = {
            "legacy": {"document": "legacy", "metadata": {"kind": "old"}},
            "staged": {"document": "staged", "metadata": {"version_state": "staging"}},
            "active": {"document": "active", "metadata": {"version_state": "active"}},
            "retired": {"document": "retired", "metadata": {"version_state": "retired"}},
        }
        store = make_store(collection)

        result = store.get("standards")
        self.assertEqual({"legacy", "active"}, set(result["ids"]))
        queried = store.query("standards", "test", n_results=10)
        self.assertEqual({"legacy", "active"}, set(queried["ids"][0]))


if __name__ == "__main__":
    unittest.main()
