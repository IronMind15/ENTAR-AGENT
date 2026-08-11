"""钉钉文档「帮我学习」入库测试（v1.11.0）—— mock 客户端/存储/process_text"""

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
from dashboard.doc_learn import _records_to_markdown, learn_dingtalk_doc  # noqa: E402


class LearnDingtalkDocTests(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._db_path = path
        self._cand = DocCandidateStore(db_path=path)
        self.patch_cand = mock.patch("dashboard.doc_candidates.get_candidate_store",
                                     return_value=self._cand)
        self.patch_cand.start()
        self.addCleanup(self.patch_cand.stop)
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        self._cand.close()
        for suffix in ("", "-wal", "-shm"):
            p = self._db_path + suffix
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

    def _add_candidate(self, node_id="n1", kind="notable"):
        self._cand.add(DocCandidate(
            user_id="u1", url=f"https://alidocs.dingtalk.com/i/nodes/{node_id}",
            node_id=node_id, kind=kind, operator_union="u1", records_count=1))

    def test_no_candidate(self):
        result = learn_dingtalk_doc("u1", client=object())
        self.assertFalse(result["has_candidate"])
        self.assertFalse(result["ok"])

    def test_successful_learn(self):
        self._add_candidate()
        fake_client = mock.Mock()
        fake_client.read_document.return_value = {
            "ok": True, "kind": "notable", "node_id": "n1",
            "records": [{"fields": {"名称": "项目A", "状态": "滞后"}}],
        }
        with mock.patch("doc_mgr.engine.process_text",
                        return_value=mock.Mock(status="done", chunk_count=3)) as m_pt:
            result = learn_dingtalk_doc("u1", client=fake_client)
        self.assertTrue(result["has_candidate"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["chunk_count"], 3)
        # process_text 收到拼好的 Markdown + standards 目标库
        content, kwargs = m_pt.call_args[0][0], m_pt.call_args[1]
        self.assertIn("项目A", content)
        self.assertEqual(kwargs["target_collection"], "standards")
        # 候选标记为已学习（get_pending 返回 None）
        self.assertIsNone(self._cand.get_pending("u1"))

    def test_successful_learn_to_specific_kb(self):
        """v1.11.5：「把这个文档学到产品手册」→ 入库到指定库（collection/department 继承）"""
        self._add_candidate()
        fake_client = mock.Mock()
        fake_client.read_document.return_value = {
            "ok": True, "kind": "notable", "node_id": "n1",
            "records": [{"fields": {"名称": "项目A"}}],
        }
        kb = {"key": "产品手册", "name": "产品手册", "collection": "产品手册",
              "department": "rd"}
        with mock.patch("doc_mgr.engine.process_text",
                        return_value=mock.Mock(status="done", chunk_count=2)) as m_pt:
            result = learn_dingtalk_doc("u1", client=fake_client, kb=kb)
        self.assertTrue(result["ok"])
        kwargs = m_pt.call_args[1]
        self.assertEqual(kwargs["target_collection"], "产品手册")
        self.assertEqual(kwargs["department"], "rd")

    def test_read_failure_keeps_candidate(self):
        self._add_candidate()
        fake_client = mock.Mock()
        fake_client.read_document.return_value = {"ok": False, "message": "无权限"}
        result = learn_dingtalk_doc("u1", client=fake_client)
        self.assertTrue(result["has_candidate"])
        self.assertFalse(result["ok"])
        self.assertIn("无权限", result["message"])
        self.assertIsNotNone(self._cand.get_pending("u1"))  # 保留，可重试

    def test_doc_kind_learns_markdown_fulltext(self):
        """v1.11.1：doc 走 blocks→Markdown 保真全文，跳过逐条拼装"""
        self._add_candidate(kind="doc")
        fake_client = mock.Mock()
        fake_client.read_document.return_value = {
            "ok": True, "kind": "doc", "node_id": "n1",
            "records": [{"类型": "段落", "内容": "周会记录"}],
            "markdown": "周会记录\n\n| 项 | 状 |\n| --- | --- |\n| A | 滞后 |",
        }
        with mock.patch("doc_mgr.engine.process_text",
                        return_value=mock.Mock(status="done", chunk_count=2)) as m_pt:
            result = learn_dingtalk_doc("u1", client=fake_client)
        self.assertTrue(result["ok"])
        content = m_pt.call_args[0][0]
        # 入库的是保真 Markdown 全文，而非逐条拼装（无「## 记录 1」）
        self.assertIn("| 项 | 状 |", content)
        self.assertNotIn("## 记录", content)
        self.assertIsNone(self._cand.get_pending("u1"))

    def test_process_failure_keeps_candidate(self):
        self._add_candidate()
        fake_client = mock.Mock()
        fake_client.read_document.return_value = {
            "ok": True, "kind": "notable", "node_id": "n1",
            "records": [{"fields": {"名称": "A"}}],
        }
        with mock.patch("doc_mgr.engine.process_text",
                        return_value=mock.Mock(status="error", message="入库失败")):
            result = learn_dingtalk_doc("u1", client=fake_client)
        self.assertFalse(result["ok"])
        self.assertIsNotNone(self._cand.get_pending("u1"))


class RecordsToMarkdownTests(unittest.TestCase):
    """AI表格记录 → Markdown 拼接"""

    def test_returns_markdown_with_label_value(self):
        records = [{"fields": {"名称": [{"name": "项目A"}], "状态": {"name": "滞后"}}}]
        md = _records_to_markdown(records)
        self.assertIn("## 记录 1", md)
        self.assertIn("- 名称：项目A", md)
        self.assertIn("- 状态：滞后", md)

    def test_skips_empty_values(self):
        records = [{"fields": {"名称": "A", "备注": ""}}]
        md = _records_to_markdown(records)
        self.assertIn("- 名称：A", md)
        self.assertNotIn("备注", md)


if __name__ == "__main__":
    unittest.main()
