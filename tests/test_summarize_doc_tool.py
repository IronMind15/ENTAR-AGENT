"""doc_summarize 工具测试（v1.11.3）—— 定位候选 → 全读 → 总结 → 可选推送本人

纯逻辑：mock 候选存储/文档客户端/LLM/notifier，不打真实 API。
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"

from scripts.dashboard.doc_candidates import DocCandidate, DocCandidateStore  # noqa: E402
from scripts.tools.doc_summarize import DEFINITION, execute  # noqa: E402


class SummarizeDocToolTests(unittest.TestCase):
    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._db_path = path
        self._cand = DocCandidateStore(db_path=path)
        self.patch_cand = patch("scripts.dashboard.doc_candidates.get_candidate_store",
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

    def _add(self, node_id="n1", kind="notable", name="33周汇总"):
        self._cand.add(DocCandidate(
            user_id="u1", url=f"https://alidocs.dingtalk.com/i/nodes/{node_id}",
            node_id=node_id, kind=kind, operator_union="u1", name=name))

    def _patch_context(self, user_id="u1", staff_id="staff1"):
        patch_uid = patch("scripts.tools.get_current_user_id", return_value=user_id)
        patch_sid = patch("scripts.tools.get_current_staff_id", return_value=staff_id)
        patch_uid.start()
        patch_sid.start()
        self.addCleanup(patch_uid.stop)
        self.addCleanup(patch_sid.stop)

    def test_definition_registered(self):
        self.assertEqual(DEFINITION["name"], "doc_summarize")
        from scripts.tools import get_tool_names
        self.assertIn("doc_summarize", get_tool_names())

    def test_summarize_success_by_node_id(self):
        self._add()
        self._patch_context()
        fake_client = unittest.mock.Mock()
        fake_client.read_document.return_value = {
            "ok": True, "kind": "notable", "node_id": "n1",
            "records": [{"fields": {"名称": "项目A", "状态": "滞后"}}],
        }
        with patch("scripts.dingtalk_doc_client.get_doc_client",
                   return_value=fake_client), \
             patch("scripts.skills.agent.call_deepseek",
                   return_value="1. 项目A：状态滞后") as m_llm:
            out = json.loads(execute({"doc_ref": "n1"}))
        self.assertTrue(out["ok"])
        self.assertEqual(out["summary"], "1. 项目A：状态滞后")
        self.assertEqual(out["doc_name"], "33周汇总")
        self.assertEqual(out["records"], 1)
        self.assertFalse(out["pushed"])
        # 全读模式：summary=False
        self.assertFalse(fake_client.read_document.call_args[1]["summary"])
        # LLM prompt 含文档名与正文
        self.assertIn("33周汇总", m_llm.call_args[0][0])

    def test_fallback_to_latest_pending(self):
        """doc_ref 为空 → 取用户最近未学候选"""
        self._add(node_id="n9")
        self._patch_context()
        fake_client = unittest.mock.Mock()
        fake_client.read_document.return_value = {
            "ok": True, "kind": "notable", "node_id": "n9",
            "records": [{"fields": {"名称": "B"}}],
        }
        with patch("scripts.dingtalk_doc_client.get_doc_client",
                   return_value=fake_client), \
             patch("scripts.skills.agent.call_deepseek", return_value="要点"):
            out = json.loads(execute({}))
        self.assertTrue(out["ok"])
        self.assertEqual(out["doc_name"], "33周汇总")

    def test_no_candidate_error(self):
        self._patch_context()
        out = json.loads(execute({"doc_ref": "nope"}))
        self.assertIn("error", out)
        self.assertIn("没有找到", out["error"])

    def test_no_user_error(self):
        self._patch_context(user_id="")
        out = json.loads(execute({}))
        self.assertIn("error", out)

    def test_read_failure_error(self):
        self._add()
        self._patch_context()
        fake_client = unittest.mock.Mock()
        fake_client.read_document.return_value = {"ok": False, "message": "无权限"}
        with patch("scripts.dingtalk_doc_client.get_doc_client",
                   return_value=fake_client):
            out = json.loads(execute({"doc_ref": "n1"}))
        self.assertIn("无权限", out["error"])

    def test_llm_failure_falls_back_to_raw_text(self):
        self._add()
        self._patch_context()
        fake_client = unittest.mock.Mock()
        fake_client.read_document.return_value = {
            "ok": True, "kind": "notable", "node_id": "n1",
            "records": [{"fields": {"名称": "项目A"}}],
        }
        with patch("scripts.dingtalk_doc_client.get_doc_client",
                   return_value=fake_client), \
             patch("scripts.skills.agent.call_deepseek", return_value=""):
            out = json.loads(execute({"doc_ref": "n1"}))
        self.assertTrue(out["ok"])
        self.assertIn("项目A", out["summary"])  # 兜底给原文前 500 字

    def test_doc_kind_uses_markdown(self):
        self._add(node_id="n5", kind="doc", name="周会记录")
        self._patch_context()
        fake_client = unittest.mock.Mock()
        fake_client.read_document.return_value = {
            "ok": True, "kind": "doc", "node_id": "n5",
            "records": [], "markdown": "周会记录\n\n| 项 | 状 |\n| A | 滞后 |",
        }
        with patch("scripts.dingtalk_doc_client.get_doc_client",
                   return_value=fake_client), \
             patch("scripts.skills.agent.call_deepseek", return_value="要点") as m_llm:
            out = json.loads(execute({"doc_ref": "n5"}))
        self.assertTrue(out["ok"])
        self.assertIn("| A | 滞后 |", m_llm.call_args[0][0])  # doc 走保真 markdown

    def test_push_to_self(self):
        self._add()
        self._patch_context(staff_id="staff9")
        fake_client = unittest.mock.Mock()
        fake_client.read_document.return_value = {
            "ok": True, "kind": "notable", "node_id": "n1",
            "records": [{"fields": {"名称": "A"}}],
        }
        with patch("scripts.dingtalk_doc_client.get_doc_client",
                   return_value=fake_client), \
             patch("scripts.skills.agent.call_deepseek", return_value="要点"), \
             patch("scripts.dingtalk_notifier.DingTalkNotifier") as m_notif:
            out = json.loads(execute({"doc_ref": "n1", "push_to_self": True}))
        self.assertTrue(out["ok"])
        self.assertTrue(out["pushed"])
        # 推后只回简短确认，不回全文（防 Agent 双发）
        self.assertNotIn("summary", out)
        sent = m_notif.return_value.send_markdown_to_users
        self.assertEqual(sent.call_args[0][0], ["staff9"])
        self.assertIn("《33周汇总》总结", sent.call_args[0][1])

    def test_push_failure_returns_error(self):
        self._add()
        self._patch_context(staff_id="staff9")
        fake_client = unittest.mock.Mock()
        fake_client.read_document.return_value = {
            "ok": True, "kind": "notable", "node_id": "n1",
            "records": [{"fields": {"名称": "A"}}],
        }
        with patch("scripts.dingtalk_doc_client.get_doc_client",
                   return_value=fake_client), \
             patch("scripts.skills.agent.call_deepseek", return_value="要点"), \
             patch("scripts.dingtalk_notifier.DingTalkNotifier") as m_notif:
            m_notif.return_value.send_markdown_to_users.side_effect = \
                RuntimeError("接口超时")
            out = json.loads(execute({"doc_ref": "n1", "push_to_self": True}))
        self.assertIn("error", out)
        self.assertIn("推送失败", out["error"])


if __name__ == "__main__":
    unittest.main()
