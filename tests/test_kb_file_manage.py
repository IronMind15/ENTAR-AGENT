"""kb_file_manage 工具 handler 补测（v1.13.4 审查补：治理框架 test_tool_governance 已测，
handler 本身零覆盖——删除/重学分支、错误路径、非法 action）"""

import json
import unittest
from unittest import mock

from scripts.tools import set_current_user_id


class KbFileManageHandlerTests(unittest.TestCase):
    """删除/重新学习分支 + 缺身份/目标 + 非法 action"""

    def setUp(self):
        set_current_user_id("u1")

    def tearDown(self):
        set_current_user_id("")

    def _run(self, action, target="a.pdf"):
        from scripts.tools.kb_file_manage import execute
        return json.loads(execute({"action": action, "target": target}))

    @mock.patch("scripts.knowledge_review.delete_file_for_user")
    @mock.patch("scripts.skills.dingtalk_bot._is_admin", return_value=False)
    def test_delete_success(self, mock_admin, mock_delete):
        """删除成功 → ok + 消息含块数与源文件去向"""
        mock_delete.return_value = {"status": "ok", "file_name": "a.pdf",
                                    "deleted_chunks": 2, "source_deleted": True}
        out = self._run("delete")
        self.assertTrue(out.get("ok"))
        self.assertIn("已删除", out["message"])
        self.assertIn("源文件已删除", out["message"])
        mock_delete.assert_called_once_with("u1", "a.pdf", is_admin=False)

    @mock.patch("scripts.knowledge_review.delete_file_for_user")
    @mock.patch("scripts.skills.dingtalk_bot._is_admin", return_value=False)
    def test_delete_source_failed_note(self, mock_admin, mock_delete):
        """源文件删除失败 → 消息如实提示保留，不谎报"""
        mock_delete.return_value = {"status": "ok", "file_name": "a.pdf",
                                    "deleted_chunks": 1, "source_deleted": False}
        out = self._run("delete")
        self.assertIn("源文件删除失败并已保留", out["message"])

    @mock.patch("scripts.knowledge_review.delete_file_for_user")
    @mock.patch("scripts.skills.dingtalk_bot._is_admin", return_value=False)
    def test_delete_failure_returns_error(self, mock_admin, mock_delete):
        """删除失败 → error（不谎报成功）"""
        mock_delete.return_value = {"status": "missing", "message": "文件不存在"}
        out = self._run("delete")
        self.assertIn("error", out)
        self.assertIn("文件不存在", out["error"])

    @mock.patch("scripts.knowledge_review.relearn_file_for_user")
    @mock.patch("scripts.skills.dingtalk_bot._is_admin", return_value=False)
    def test_relearn_success(self, mock_admin, mock_relearn):
        """重新学习成功 → ok"""
        mock_relearn.return_value = {"status": "ok", "message": "已重新学习",
                                     "file_name": "a.pdf"}
        out = self._run("relearn")
        self.assertTrue(out.get("ok"))
        mock_relearn.assert_called_once_with("u1", "a.pdf", is_admin=False)

    def test_missing_user_identity(self):
        """无用户身份 → 拒绝执行"""
        set_current_user_id("")
        from scripts.tools.kb_file_manage import execute
        out = json.loads(execute({"action": "delete", "target": "a.pdf"}))
        self.assertIn("error", out)

    def test_missing_target(self):
        """缺目标 → 拒绝执行"""
        from scripts.tools.kb_file_manage import execute
        out = json.loads(execute({"action": "delete", "target": ""}))
        self.assertIn("error", out)

    def test_unknown_action(self):
        """非法 action → error"""
        out = self._run("rename")
        self.assertIn("error", out)
        self.assertIn("不支持", out["error"])


if __name__ == "__main__":
    unittest.main()
