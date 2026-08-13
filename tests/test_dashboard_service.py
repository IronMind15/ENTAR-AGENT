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
    assemble_per_source_messages, build_dynamic_source, load_all_available_sources,
    resolve_subscription_sources, source_resolution_warnings,
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
        self.assertEqual(src.source_url, "u")
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
                                                  name="研发项目现况表",
                                                  base_id="b1")):
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

    def test_missing_source_has_generic_warning(self):
        from dashboard.config_model import SourceConfig
        sub = self._sub(["doc_1", "doc_2"])
        warnings = source_resolution_warnings(
            sub, [SourceConfig(key="doc_1", base_id="n1")])
        self.assertIn("doc_2", warnings[0])


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
                                                   name="研发项目现况表",
                                                   base_id="b1")]):
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

    def test_folder_kind_included(self):
        # v1.13.0：folder 纳入看板数据源（动态取最新子文档）
        self._store.add(DocCandidate(user_id="u1", url="u", node_id="f1",
                                     kind="folder", enabled=True,
                                     name="部门周报"))
        with mock.patch("dashboard.config_model.load_sources", return_value=[]):
            sources = load_all_available_sources()
        self.assertEqual({s.key for s in sources}, {"doc_1"})
        self.assertEqual(sources[0].kind, "folder")
        self.assertEqual(sources[0].name, "部门周报")

    def test_load_all_sources_user_scoped(self):
        """v1.11.10：query 按当前用户过滤动态源，不把别人的文档候选混入"""
        self._store.add(DocCandidate(user_id="u1", url="u", node_id="n1",
                                     kind="notable", enabled=True))
        self._store.add(DocCandidate(user_id="u2", url="u2", node_id="n2",
                                     kind="notable", enabled=True))
        with mock.patch("dashboard.config_model.load_sources", return_value=[]):
            scoped = load_all_available_sources(user_id="u1")
        self.assertEqual({s.key for s in scoped}, {"doc_1"})
        with mock.patch("dashboard.config_model.load_sources", return_value=[]):
            all_ = load_all_available_sources()
        self.assertEqual({s.key for s in all_}, {"doc_1", "doc_2"})


class AssemblePerSourceMessagesTests(unittest.TestCase):
    """v1.13.0：逐源组装——每个数据源单独一轮 LLM，各产出消息"""

    def _parsed_item(self, source_key):
        return {"source_key": source_key, "name": f"源{source_key}",
                "table_name": "", "total": 1, "items": [], "detailed_items": [],
                "status_counts": {}, "attention_items": [], "normal_items": [],
                "other_items": []}

    def test_each_source_reduced_separately(self):
        parsed = [self._parsed_item("a"), self._parsed_item("b")]
        fake_report = mock.MagicMock()
        fake_report.messages = ["总结"]
        build = mock.patch(
            "dashboard.llm_pipeline.build_dashboard_report",
            return_value=fake_report).start()
        self.addCleanup(mock.patch.stopall)
        msgs = assemble_per_source_messages(
            parsed, title="T", date_str="2026-08-13", llm_func=lambda *a, **k: {})
        self.assertEqual(msgs, ["总结", "总结"])
        # 每个源独立调用一次 build_dashboard_report，且只传该源 item
        self.assertEqual(build.call_count, 2)
        for i, item in enumerate(parsed):
            self.assertEqual(build.call_args_list[i][0][0], [item])
        # 旧快照全量传入（field_diff 内部按 source_key 过滤），透传 title/template
        self.assertEqual(build.call_args_list[0][1]["title"], "T")
        self.assertEqual(build.call_args_list[0][0][1], None)  # 位置参数 = old_snapshot

    def test_multi_page_source_all_appended(self):
        # 单源超长时 report.messages 自带语义分页，全量追加（多条也算一条源）
        parsed = [self._parsed_item("a")]
        report = mock.MagicMock()
        report.messages = ["第1页", "第2页"]
        with mock.patch("dashboard.llm_pipeline.build_dashboard_report",
                        return_value=report):
            msgs = assemble_per_source_messages(parsed)
        self.assertEqual(msgs, ["第1页", "第2页"])

    def test_empty_parsed_returns_empty(self):
        with mock.patch("dashboard.llm_pipeline.build_dashboard_report") as build:
            msgs = assemble_per_source_messages([])
        self.assertEqual(msgs, [])
        build.assert_not_called()


if __name__ == "__main__":
    unittest.main()
