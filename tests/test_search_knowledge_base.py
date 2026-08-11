"""通用知识库查询工具测试（v1.11.5）—— 指定库分发 / 全库合并 / 可见性 / 错误分支"""

import json
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

from kb_registry import KBRegistry  # noqa: E402


class _ToolBase(unittest.TestCase):
    """临时注册表 + mock 注册表单例（不碰真实 DB，不碰真实 Chroma）"""

    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._db_path = path
        self.reg = KBRegistry(db_path=path)
        self.patch_reg = mock.patch("kb_registry.get_registry", return_value=self.reg)
        self.patch_reg.start()
        self.addCleanup(self.patch_reg.stop)
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        try:
            self.reg.close()
        except Exception:
            pass
        for suffix in ("", "-wal", "-shm"):
            p = self._db_path + suffix
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass


class SearchToolTests(_ToolBase):
    """通用工具 execute：分发 + 合并 + 可见性 + 错误"""

    def test_empty_query_error(self):
        from tools.search_knowledge_base import execute
        r = json.loads(execute({"query": "   "}))
        self.assertIn("error", r)

    def test_tool_registered(self):
        from tools import get_tool_names
        self.assertIn("search_knowledge_base", get_tool_names())

    def test_specific_kb_dispatch_to_experience(self):
        """指定「经验知识库」→ 分发到 experience_query.search_kb"""
        from tools.search_knowledge_base import execute
        fake = [{
            "std_title": "经验条目：过温", "_content": "清理风扇",
            "chapter_title": "解决方案", "_score": 0.3, "_match_type": "semantic",
        }]
        with mock.patch("skills.experience_query.search_kb", return_value=fake) as m:
            r = json.loads(execute({"query": "过温", "knowledge_base": "经验知识库"}))
        m.assert_called_once_with("过温")
        self.assertTrue(r["found"])
        self.assertEqual(len(r["results"]), 1)
        self.assertEqual(r["results"][0]["kb_name"], "经验知识库")
        # 内部字段清理：_content → content_summary，保留 source_label
        self.assertIn("content_summary", r["results"][0])
        self.assertNotIn("_match_type", r["results"][0])
        self.assertIn("source_label", r["results"][0])
        self.assertIn("经验", r["results"][0]["source_label"])

    def test_specific_kb_dispatch_to_error_codes(self):
        """指定「故障知识库」→ error_query.search_kb（故障码精确通道）"""
        from tools.search_knowledge_base import execute
        fake = [{
            "name": "急停告警", "fault_code": "d4-1", "fault_reason": "外部急停",
            "_score": 0.0, "_match_type": "exact",
        }]
        with mock.patch("skills.error_query.search_kb", return_value=fake) as m:
            r = json.loads(execute({"query": "d4-1", "knowledge_base": "故障知识库"}))
        m.assert_called_once_with("d4-1")
        self.assertTrue(r["found"])
        self.assertEqual(r["results"][0]["kb_name"], "故障知识库")
        self.assertIn("d4-1", r["results"][0]["source_label"])

    def test_unknown_kb_message(self):
        from tools.search_knowledge_base import execute
        r = json.loads(execute({"query": "x", "knowledge_base": "不存在的库"}))
        self.assertFalse(r["found"])
        self.assertIn("未找到知识库", r["message"])

    def test_invisible_department(self):
        """部门权限预留：rd 库对 bz 用户不可见，给出明确提示"""
        from tools.search_knowledge_base import execute
        self.reg.create_knowledge_base("研发库", department="rd")
        with mock.patch("tools.get_user_centers", return_value=["bz"]):
            r = json.loads(execute({"query": "x", "knowledge_base": "研发库"}))
        self.assertFalse(r["found"])
        self.assertIn("不可见", r["message"])

    def test_visible_department(self):
        """rd 用户能看到 rd 库并搜索"""
        from tools.search_knowledge_base import execute
        self.reg.create_knowledge_base("研发库", department="rd")
        fake = [{"title": "电路设计", "_content": "xx", "_score": 0.5,
                 "_match_type": "semantic"}]
        with mock.patch("tools.get_user_centers", return_value=["rd"]), \
             mock.patch("skills.enhanced_search.enhanced_query", return_value={
                 "documents": [["内容"]], "metadatas": [[{"title": "电路设计"}]],
                 "distances": [[0.5]]}):
            r = json.loads(execute({"query": "电路", "knowledge_base": "研发库"}))
        self.assertTrue(r["found"])

    def test_all_visible_merge_sorted(self):
        """留空 knowledge_base → 全可见库搜，合并去重按 _score 升序"""
        from tools.search_knowledge_base import execute
        err_fake = [{"name": "急停告警", "fault_code": "d4-1",
                     "fault_reason": "外部急停", "_score": 0.1,
                     "_match_type": "exact"}]
        std_fake = [{"std_id": "GB/T 34133", "chapter": "6", "page": "12",
                     "_score": 0.2, "_match_type": "semantic",
                     "_content": "并网电压范围..."}]
        exp_fake = [{"std_title": "经验条目：过温", "_score": 0.5,
                     "_match_type": "semantic", "_content": "清理风扇"}]
        with mock.patch("skills.error_query.search_kb", return_value=err_fake), \
             mock.patch("skills.standards_query.search_kb", return_value=std_fake), \
             mock.patch("skills.experience_query.search_kb", return_value=exp_fake):
            r = json.loads(execute({"query": "急停 并网"}))
        self.assertTrue(r["found"])
        results = r["results"]
        self.assertEqual(len(results), 3)
        # 按 _score 升序（内部排序后清理 _ 字段，输出保留顺序）：
        # 故障(0.1) → 标准(0.2) → 经验(0.5)
        self.assertEqual([x["kb_name"] for x in results],
                         ["故障知识库", "标准知识库", "经验知识库"])
        self.assertEqual(results[0]["source_label"].count("d4-1"), 1)

    def test_no_results_message(self):
        from tools.search_knowledge_base import execute
        with mock.patch("skills.error_query.search_kb", return_value=[]), \
             mock.patch("skills.standards_query.search_kb", return_value=[]), \
             mock.patch("skills.experience_query.search_kb", return_value=[]):
            r = json.loads(execute({"query": "xyz"}))
        self.assertFalse(r["found"])
        self.assertIn("未找到", r["message"])

    def test_disabled_kb_not_visible(self):
        """已停用库：指定查询时提示已停用"""
        from tools.search_knowledge_base import execute
        self.reg.create_knowledge_base("停用库")
        kb = self.reg.get_knowledge_base("停用库")
        self.reg.update_knowledge_base(kb["key"], enabled=0)
        r = json.loads(execute({"query": "x", "knowledge_base": "停用库"}))
        self.assertFalse(r["found"])
        self.assertIn("停用", r["message"])


if __name__ == "__main__":
    unittest.main()
