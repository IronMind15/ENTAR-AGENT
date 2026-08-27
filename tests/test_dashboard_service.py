"""看板 service 动态源桥接测试（v1.11.0）—— build/resolve 动态数据源"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"

from scripts.dashboard.doc_candidates import DocCandidate, DocCandidateStore  # noqa: E402
from scripts.dashboard.service import (  # noqa: E402
    assemble_per_source_messages, build_dynamic_source, load_all_available_sources,
    resolve_subscription_sources, source_resolution_warnings,
)
from scripts.dashboard.subscription_store import Subscription  # noqa: E402
from scripts.tools.dash_query import _task_prompt_for_query  # noqa: E402


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
            "scripts.dashboard.doc_candidates.get_candidate_store", return_value=self._store)
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

    def test_new_document_does_not_auto_join_task(self):
        """v1.12.7（D1）：任务只解析自己绑定的源。

        订阅绑定 doc_1；之后用户又发布了 doc_2——任务边界固定，doc_2 默认
        不进已有任务（需主动「把这个文档加进看板」）。
        """
        self._store.add(DocCandidate(user_id="u1", url="u", node_id="n1",
                                     kind="notable", enabled=True))
        self._store.add(DocCandidate(user_id="u1", url="u2", node_id="n2",
                                     kind="notable", enabled=True))
        sub = self._sub(["doc_1"])
        with mock.patch("scripts.dashboard.config_model.load_sources", return_value=[]):
            sources = resolve_subscription_sources(sub)
        self.assertEqual([s.key for s in sources], ["doc_1"])

    def test_other_owner_candidates_isolated(self):
        """v1.12.7（D1）：不同 owner 的文件互不影响（隔离）——只解析任务绑定候选"""
        self._store.add(DocCandidate(user_id="u1", url="u", node_id="n1",
                                     kind="notable", enabled=True))
        self._store.add(DocCandidate(user_id="u2", url="u", node_id="n2",
                                     kind="notable", enabled=True))
        sub = self._sub(["doc_1"])
        with mock.patch("scripts.dashboard.config_model.load_sources", return_value=[]):
            sources = resolve_subscription_sources(sub)
        self.assertEqual([s.key for s in sources], ["doc_1"])

    def test_disabled_candidate_excluded(self):
        """v1.12.7（D1）：绑定源停用/删除 → 自动退出推送（任务内隔离的停用手段）"""
        self._store.add(DocCandidate(user_id="u1", url="u", node_id="n1",
                                     kind="notable", enabled=False))
        sub = self._sub(["doc_1"])
        with mock.patch("scripts.dashboard.config_model.load_sources", return_value=[]):
            sources = resolve_subscription_sources(sub)
        self.assertEqual(sources, [])

    def test_static_config_sources_only_bound(self):
        """v1.12.7（D1）：任务只解析自己绑定的源——绑 project_status 就只出它。

        即使 owner 有动态候选 doc_1，任务边界固定不并入（用户没把它加进任务）。
        """
        from scripts.dashboard.config_model import SourceConfig
        self._store.add(DocCandidate(user_id="u1", url="u", node_id="n1",
                                     kind="notable", enabled=True))
        sub = self._sub(["project_status"])
        with mock.patch("scripts.dashboard.config_model.get_source",
                        return_value=SourceConfig(key="project_status",
                                                  name="研发项目现况表",
                                                  base_id="b1")):
            sources = resolve_subscription_sources(sub)
        self.assertEqual([s.key for s in sources], ["project_status"])

    def test_migration_fallback_stored_config_only(self):
        """迁移兜底：任务绑定的源逐个解析——配置键从 config 取，doc_ 键查候选。

        绑定的 project_status 存在 → 出；doc_999 无对应候选 → 跳过，不崩。
        """
        from scripts.dashboard.config_model import SourceConfig
        sub = self._sub(["project_status", "doc_999"])
        with mock.patch("scripts.dashboard.config_model.get_source",
                        return_value=SourceConfig(key="project_status",
                                                  name="研发项目现况表",
                                                  base_id="b1")), \
                mock.patch("scripts.dashboard.config_model.load_sources", return_value=[]):
            sources = resolve_subscription_sources(sub)
        self.assertEqual([s.key for s in sources], ["project_status"])

    def test_no_sources_at_all(self):
        sub = self._sub(["doc_999"])
        with mock.patch("scripts.dashboard.config_model.get_source", return_value=None), \
                mock.patch("scripts.dashboard.config_model.load_sources", return_value=[]):
            sources = resolve_subscription_sources(sub)
        self.assertEqual(sources, [])

    def test_warning_only_when_all_sources_gone(self):
        """v1.12.7（D1）：仅当任务绑定源全部不可用且旧绑定非空才提醒（避免静默空推）"""
        sub = self._sub(["doc_1", "doc_2"])
        warnings = source_resolution_warnings(sub, [])
        self.assertEqual(len(warnings), 1)
        self.assertIn("doc_1", warnings[0])

    def test_no_warning_when_sources_present(self):
        sub = self._sub(["doc_1", "doc_2"])
        warnings = source_resolution_warnings(sub, [object()])
        self.assertEqual(warnings, [])


class LoadAllAvailableSourcesTests(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._db_path = path
        self._store = DocCandidateStore(db_path=path)
        self.patch_cand = mock.patch(
            "scripts.dashboard.doc_candidates.get_candidate_store", return_value=self._store)
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
        from scripts.dashboard.config_model import SourceConfig
        self._store.add(DocCandidate(user_id="u1", url="u", node_id="n1",
                                     kind="notable", enabled=True))
        with mock.patch("scripts.dashboard.config_model.load_sources",
                        return_value=[SourceConfig(key="project_status",
                                                   name="研发项目现况表",
                                                   base_id="b1")]):
            sources = load_all_available_sources()
        self.assertEqual({s.key for s in sources}, {"project_status", "doc_1"})

    def test_doc_kind_included(self):
        # v1.11.1：doc 也纳入看板数据源
        self._store.add(DocCandidate(user_id="u1", url="u", node_id="n3",
                                     kind="doc", enabled=True))
        with mock.patch("scripts.dashboard.config_model.load_sources", return_value=[]):
            sources = load_all_available_sources()
        self.assertEqual({s.key for s in sources}, {"doc_1"})
        self.assertEqual(sources[0].kind, "doc")

    def test_folder_kind_included(self):
        # v1.12.3：folder 纳入看板数据源；v1.12.6（C8）每次推送解读全部子文档
        self._store.add(DocCandidate(user_id="u1", url="u", node_id="f1",
                                     kind="folder", enabled=True,
                                     name="部门周报"))
        with mock.patch("scripts.dashboard.config_model.load_sources", return_value=[]):
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
        with mock.patch("scripts.dashboard.config_model.load_sources", return_value=[]):
            scoped = load_all_available_sources(user_id="u1")
        self.assertEqual({s.key for s in scoped}, {"doc_1"})
        with mock.patch("scripts.dashboard.config_model.load_sources", return_value=[]):
            all_ = load_all_available_sources()
        self.assertEqual({s.key for s in all_}, {"doc_1", "doc_2"})


class AssemblePerSourceMessagesTests(unittest.TestCase):
    """v1.12.5：逐源组装——每个数据源单独一轮 LLM，各产出消息"""

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
            "scripts.dashboard.llm_pipeline.build_dashboard_report",
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
        with mock.patch("scripts.dashboard.llm_pipeline.build_dashboard_report",
                        return_value=report):
            msgs = assemble_per_source_messages(parsed)
        self.assertEqual(msgs, ["第1页", "第2页"])

    def test_empty_parsed_returns_empty(self):
        with mock.patch("scripts.dashboard.llm_pipeline.build_dashboard_report") as build:
            msgs = assemble_per_source_messages([])
        self.assertEqual(msgs, [])
        build.assert_not_called()


class CollectAndParsePartialFailureTests(unittest.TestCase):
    """v1.12.6（C8）：collect_and_parse 对部分失败（error 与 records 并存）保留数据"""

    def _run(self, sources, collected):
        from scripts.dashboard.service import collect_and_parse
        with mock.patch("scripts.dashboard.collector.Collector.collect_all",
                        return_value=collected):
            return collect_and_parse(sources, operator_id="op")

    @staticmethod
    def _src():
        return build_dynamic_source(DocCandidate(
            id=1, user_id="u1", url="u", node_id="n1", sheet_id="s1",
            kind="notable", operator_union="union1"))

    def test_full_failure_without_records_is_skipped(self):
        """error 非空且无 records → 整源跳过进 errors（旧行为保持）"""
        src = self._src()
        parsed, errors = self._run([src], [{
            "source_key": src.key, "name": "A", "table_name": "",
            "records": [], "error": "无权限",
        }])
        self.assertEqual(parsed, [])
        self.assertEqual(len(errors), 1)
        self.assertIn("无权限", errors[0])

    def test_partial_failure_keeps_records_and_reports_error(self):
        """v1.12.6（C8）：文件夹部分子文档失败 → 记录保留 + 错误同时上报"""
        src = self._src()
        collected = [{
            "source_key": src.key, "name": "部门周报", "table_name": "",
            "records": [{"fields": {"名称": "交付验收"}},
                        {"fields": {"名称": "设计评审"}}],
            "error": "1/2 份子文档读取失败：33周部门周报: 无权限",
        }]
        parsed, errors = self._run([src], collected)
        # 成功记录照常进 parsed（不再因 error 非空整源丢弃）
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["source_key"], src.key)
        # 失败信息同时进 errors，报告可提示不完整
        self.assertEqual(len(errors), 1)
        self.assertIn("1/2 份子文档读取失败", errors[0])


class DashQueryTaskPromptTests(unittest.TestCase):
    """v1.13.3（P4 口径统一）：dash_query 实时查询与定时推送同口径

    ——用户仅有唯一启用任务时注入其固定提示词快照；多任务/无任务/快照未
    固化时退 llm_pipeline 默认整理指令。
    """

    def _sub(self, enabled=True, task_prompt="", spec=True, sub_id=1):
        return Subscription(
            id=sub_id, owner_user_id="u1", enabled=enabled,
            task_prompt=task_prompt,
            task_prompt_spec={"key": "daily"} if spec else {},
        )

    @mock.patch("scripts.dashboard.subscription_store.get_subscription_store")
    def test_single_enabled_subscription_injects_task_prompt(self, mock_store):
        mock_store.return_value.list_for_owner.return_value = [
            self._sub(task_prompt="固定提示词A")]
        self.assertEqual("固定提示词A", _task_prompt_for_query("u1"))

    @mock.patch("scripts.dashboard.subscription_store.get_subscription_store")
    def test_multiple_enabled_subscriptions_fall_back_to_generic(self, mock_store):
        mock_store.return_value.list_for_owner.return_value = [
            self._sub(task_prompt="A", sub_id=1),
            self._sub(task_prompt="B", sub_id=2)]
        self.assertEqual("", _task_prompt_for_query("u1"))

    @mock.patch("scripts.dashboard.subscription_store.get_subscription_store")
    def test_disabled_only_subscription_falls_back(self, mock_store):
        mock_store.return_value.list_for_owner.return_value = [
            self._sub(enabled=False, task_prompt="A")]
        self.assertEqual("", _task_prompt_for_query("u1"))

    @mock.patch("scripts.dashboard.subscription_store.get_subscription_store")
    def test_missing_prompt_spec_falls_back(self, mock_store):
        mock_store.return_value.list_for_owner.return_value = [
            self._sub(task_prompt="A", spec=False)]
        self.assertEqual("", _task_prompt_for_query("u1"))

    def test_empty_user_id_falls_back(self):
        self.assertEqual("", _task_prompt_for_query(""))

    def test_execute_injects_task_prompt_when_single_task(self):
        import json
        from scripts.tools import set_current_user_id
        from scripts.tools.dash_query import execute as dash_exec
        set_current_user_id("u1")
        sub = self._sub(task_prompt="固定提示词A")
        with mock.patch("scripts.dashboard.service.load_all_available_sources",
                        return_value=[mock.Mock(key="doc_1")]), \
             mock.patch("scripts.dashboard.service.collect_and_parse",
                        return_value=([{"name": "看板", "source_key": "doc_1"}], [])), \
             mock.patch("scripts.dashboard.subscription_store.get_subscription_store") \
                 as mock_store, \
             mock.patch("scripts.dashboard.service.assemble",
                        return_value="看板正文") as mock_assemble:
            mock_store.return_value.list_for_owner.return_value = [sub]
            result = json.loads(dash_exec({}))
        self.assertEqual("看板正文", result["dashboard"])
        self.assertEqual("固定提示词A",
                         mock_assemble.call_args.kwargs.get("task_prompt"))


if __name__ == "__main__":
    unittest.main()
