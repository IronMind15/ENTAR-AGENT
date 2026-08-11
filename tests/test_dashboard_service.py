"""看板 service 动态源桥接测试（v1.11.0）—— build/resolve 动态数据源"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from dashboard.doc_candidates import DocCandidate, DocCandidateStore  # noqa: E402
from dashboard.service import (  # noqa: E402
    build_dynamic_source, load_all_available_sources, resolve_subscription_sources,
)
from dashboard.subscription_store import Subscription  # noqa: E402


class BuildDynamicSourceTests(unittest.TestCase):
    def test_maps_candidate_to_source(self):
        cand = DocCandidate(
            id=7, user_id="u1", url="u", node_id="n1", sheet_id="s1",
            kind="notable", operator_union="union1",
            field_map='{"a1": {"label": "名称", "type": "string", "max_len": 200}}')
        src = build_dynamic_source(cand)
        self.assertEqual(src.key, "doc_7")
        self.assertEqual(src.base_id, "n1")
        self.assertEqual(src.table_id, "s1")
        self.assertEqual(src.operator_id, "union1")
        self.assertEqual(src.field_map["a1"].label, "名称")
        self.assertEqual(src.kind, "notable")

    def test_name_fallback(self):
        cand = DocCandidate(id=1, user_id="u", url="u", node_id="n",
                            kind="notable", name="")
        self.assertEqual(build_dynamic_source(cand).name, "文档n")

    def test_custom_name(self):
        cand = DocCandidate(id=1, user_id="u", url="u", node_id="n",
                            kind="notable", name="研发项目现况表")
        self.assertEqual(build_dynamic_source(cand).name, "研发项目现况表")

    def test_bad_field_map_json(self):
        cand = DocCandidate(id=1, user_id="u", url="u", node_id="n",
                            kind="notable", field_map="not json")
        src = build_dynamic_source(cand)
        self.assertEqual(src.field_map, {})


class ResolveSubscriptionSourcesTests(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._db_path = path
        self._store = DocCandidateStore(db_path=path)
        self.patch_cand = mock.patch(
            "dashboard.doc_candidates.get_candidate_store", return_value=self._store)
        self.patch_cand.start()
        self.addCleanup(self.patch_cand.stop)
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

    def _sub(self, data_sources):
        return Subscription(owner_user_id="u1", data_sources=data_sources)

    def test_mixed_config_and_doc_keys(self):
        from dashboard.config_model import SourceConfig
        self._store.add(DocCandidate(user_id="u1", url="u", node_id="n1",
                                     kind="notable", enabled=True))
        sub = self._sub(["project_status", "doc_1"])
        with mock.patch("dashboard.config_model.get_source",
                        return_value=SourceConfig(key="project_status",
                                                  name="研发项目现况表")):
            sources = resolve_subscription_sources(sub)
        self.assertEqual([s.key for s in sources], ["project_status", "doc_1"])

    def test_missing_doc_skipped(self):
        sub = self._sub(["doc_999"])
        with mock.patch("dashboard.config_model.get_source"):
            sources = resolve_subscription_sources(sub)
        self.assertEqual(sources, [])

    def test_disabled_doc_skipped(self):
        self._store.add(DocCandidate(user_id="u1", url="u", node_id="n1",
                                     kind="notable", enabled=False))
        sub = self._sub(["doc_1"])
        with mock.patch("dashboard.config_model.get_source"):
            sources = resolve_subscription_sources(sub)
        self.assertEqual(sources, [])

    def test_missing_config_skipped(self):
        sub = self._sub(["missing_key"])
        with mock.patch("dashboard.config_model.get_source", return_value=None):
            sources = resolve_subscription_sources(sub)
        self.assertEqual(sources, [])


class LoadAllAvailableSourcesTests(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._db_path = path
        self._store = DocCandidateStore(db_path=path)
        self.patch_cand = mock.patch(
            "dashboard.doc_candidates.get_candidate_store", return_value=self._store)
        self.patch_cand.start()
        self.addCleanup(self.patch_cand.stop)
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

    def test_config_plus_dynamic(self):
        from dashboard.config_model import SourceConfig
        self._store.add(DocCandidate(user_id="u1", url="u", node_id="n1",
                                     kind="notable", enabled=True))
        with mock.patch("dashboard.config_model.load_sources",
                        return_value=[SourceConfig(key="project_status",
                                                   name="研发项目现况表")]):
            sources = load_all_available_sources()
        self.assertEqual({s.key for s in sources}, {"project_status", "doc_1"})

    def test_doc_kind_included(self):
        # v1.11.1：doc 也纳入看板数据源
        self._store.add(DocCandidate(user_id="u1", url="u", node_id="n3",
                                     kind="doc", enabled=True))
        with mock.patch("dashboard.config_model.load_sources", return_value=[]):
            sources = load_all_available_sources()
        self.assertEqual({s.key for s in sources}, {"doc_1"})
        self.assertEqual(sources[0].kind, "doc")


if __name__ == "__main__":
    unittest.main()
