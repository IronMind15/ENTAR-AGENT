"""v1.11.0 钉钉文档客户端测试：链接解析 + operatorId 解析 + 多链接聚合（纯逻辑，无需真实 API）"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from dingtalk_doc_client import (  # noqa: E402
    DingTalkDocClient, DingTalkDocPermissionError, _ALIDOCS_NODE_RE,
)


class ParseDocUrlTests(unittest.TestCase):
    """钉钉文档链接解析"""

    def test_standard_node_link(self):
        parsed = DingTalkDocClient.parse_doc_url(
            "https://alidocs.dingtalk.com/i/nodes/np9zOoBVBYQe06entLExnXArW1DK0g6l")
        self.assertIsNotNone(parsed)
        self.assertEqual("np9zOoBVBYQe06entLExnXArW1DK0g6l", parsed["node_id"])
        self.assertEqual("", parsed["sheet_id"])

    def test_link_with_sheet_param(self):
        parsed = DingTalkDocClient.parse_doc_url(
            "https://alidocs.dingtalk.com/i/nodes/abc123?sheet=xyz789")
        self.assertEqual("abc123", parsed["node_id"])
        self.assertEqual("xyz789", parsed["sheet_id"])

    def test_link_with_sheetId_param(self):
        parsed = DingTalkDocClient.parse_doc_url(
            "https://alidocs.dingtalk.com/i/nodes/abc?sheetId=s1")
        self.assertEqual("s1", parsed["sheet_id"])

    def test_link_with_view_segment(self):
        parsed = DingTalkDocClient.parse_doc_url(
            "https://alidocs.dingtalk.com/i/nodes/base123/view?sheet=s1")
        self.assertEqual("base123", parsed["node_id"])
        self.assertEqual("s1", parsed["sheet_id"])

    def test_non_dingtalk_link(self):
        self.assertIsNone(DingTalkDocClient.parse_doc_url("https://example.com/foo"))
        self.assertIsNone(DingTalkDocClient.parse_doc_url("百度一下"))

    def test_empty_and_none(self):
        self.assertIsNone(DingTalkDocClient.parse_doc_url(""))
        self.assertIsNone(DingTalkDocClient.parse_doc_url(None))

    def test_www_subdomain(self):
        parsed = DingTalkDocClient.parse_doc_url(
            "https://www.alidocs.dingtalk.com/i/nodes/node1")
        self.assertEqual("node1", parsed["node_id"])


class ResolveOperatorIdTests(unittest.TestCase):
    """operatorId（unionId）解析：显式优先，staff_id 兜底"""

    def setUp(self):
        self.client = DingTalkDocClient(
            client_id="cid", client_secret="csecret", api_base="https://x")

    def test_explicit_operator_id_wins(self):
        # 显式传 operator_id（= 钉钉 Stream 的 sender_id/unionId）直接返回
        self.assertEqual("union-abc", self.client.resolve_operator_id(
            operator_id="union-abc", staff_id="staff-1"))

    def test_staff_id_falls_back_to_unionid(self):
        # staff_id → contact_api.get_user_detail → unionid
        with patch("contact_api.get_contact_client") as mock_get:
            mock_client = mock_get.return_value
            mock_client.get_user_detail.return_value = {"unionid": "union-from-api"}
            got = self.client.resolve_operator_id(operator_id="", staff_id="staff-1")
        self.assertEqual("union-from-api", got)
        # 二次调用命中缓存，不再请求
        with patch("contact_api.get_contact_client") as mock_get2:
            got2 = self.client.resolve_operator_id(operator_id="", staff_id="staff-1")
        mock_get2.assert_not_called()
        self.assertEqual("union-from-api", got2)

    def test_staff_id_no_unionid_raises(self):
        with patch("contact_api.get_contact_client") as mock_get:
            mock_client = mock_get.return_value
            mock_client.get_user_detail.return_value = {}
            with self.assertRaises(RuntimeError) as ctx:
                self.client.resolve_operator_id(operator_id="", staff_id="staff-1")
        self.assertIn("operatorId", str(ctx.exception))

    def test_nothing_raises(self):
        with self.assertRaises(RuntimeError):
            self.client.resolve_operator_id(operator_id="", staff_id="")


class DetectKindErrorHandlingTests(unittest.TestCase):
    """类型探测：权限错误不降级"""

    def setUp(self):
        self.client = DingTalkDocClient(
            client_id="cid", client_secret="csecret", api_base="https://x")

    def test_permission_error_propagates(self):
        from dingtalk_doc_client import DingTalkDocPermissionError
        with patch.object(self.client, "list_sheets",
                          side_effect=DingTalkDocPermissionError("无权限")):
            with self.assertRaises(DingTalkDocPermissionError):
                self.client.detect_kind("base1", "op1")


class BotDocLinkTests(unittest.TestCase):
    """dingtalk_bot._handle_dingtalk_doc_link 自动识别逻辑（v1.11.0）"""

    def setUp(self):
        # 隔离文档候选存储，避免污染真实 data/user_store.db
        from dashboard.doc_candidates import DocCandidateStore
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._db_path = path
        self._cand = DocCandidateStore(db_path=path)
        self._patch_cand = patch("dashboard.doc_candidates.get_candidate_store",
                                 return_value=self._cand)
        self._patch_cand.start()
        self.addCleanup(self._patch_cand.stop)
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

    def _make_handler(self):
        from skills import dingtalk_bot
        return object.__new__(dingtalk_bot.ErrorQueryHandler)

    def test_plain_text_returns_none(self):
        handler = self._make_handler()
        self.assertIsNone(handler._handle_dingtalk_doc_link("今天天气如何", "u1", "s1"))

    def test_doc_link_reads_and_replies(self):
        handler = self._make_handler()
        fake = {"ok": True, "kind": "notable", "node_id": "n1",
                "records": [{"fields": {"名称": "A", "状态": "滞后"}}],
                "message": "读取成功"}
        with patch("dingtalk_doc_client.get_doc_client") as mock_get:
            mock_get.return_value.resolve_operator_id.return_value = "union1"
            mock_get.return_value.read_document.return_value = fake
            answer = handler._handle_dingtalk_doc_link(
                "https://alidocs.dingtalk.com/i/nodes/n1", "u1", "s1")
        self.assertIsNotNone(answer)
        self.assertIn("已识别钉钉文档", answer)
        self.assertIn("帮我学习", answer)
        # 交互原则：只主动问学习，不推销推送
        self.assertNotIn("每日推送", answer)
        self.assertNotIn("看板", answer)

    def test_doc_link_read_failure(self):
        handler = self._make_handler()
        with patch("dingtalk_doc_client.get_doc_client") as mock_get:
            mock_get.return_value.resolve_operator_id.return_value = "union1"
            mock_get.return_value.read_document.side_effect = RuntimeError("无权限")
            answer = handler._handle_dingtalk_doc_link(
                "https://alidocs.dingtalk.com/i/nodes/n1", "u1", "s1")
        self.assertIn("读取失败", answer)

    def test_doc_link_unsupported_kind(self):
        handler = self._make_handler()
        fake = {"ok": False, "kind": "workbook", "node_id": "n1",
                "message": "暂仅支持 AI表格"}
        with patch("dingtalk_doc_client.get_doc_client") as mock_get:
            mock_get.return_value.resolve_operator_id.return_value = "union1"
            mock_get.return_value.read_document.return_value = fake
            answer = handler._handle_dingtalk_doc_link(
                "https://alidocs.dingtalk.com/i/nodes/n1", "u1", "s1")
        self.assertIn("暂无法读取", answer)

    def test_multi_links_aggregated(self):
        """多条链接逐条读取 + 聚合摘要"""
        handler = self._make_handler()
        fake1 = {"ok": True, "kind": "notable", "node_id": "n1",
                 "records": [{"fields": {"名称": "A"}}], "message": "ok"}
        fake2 = {"ok": True, "kind": "notable", "node_id": "n2",
                 "records": [{"fields": {"名称": "B"}}], "message": "ok"}
        with patch("dingtalk_doc_client.get_doc_client") as mock_get:
            mock_get.return_value.resolve_operator_id.return_value = "union1"
            mock_get.return_value.read_document.side_effect = [fake1, fake2]
            answer = handler._handle_dingtalk_doc_link(
                "看这两份：https://alidocs.dingtalk.com/i/nodes/n1 "
                "和 https://alidocs.dingtalk.com/i/nodes/n2",
                "u1", "s1")
        self.assertIn("已识别钉钉文档", answer)
        self.assertIn("n1", answer)
        self.assertIn("n2", answer)
        self.assertEqual(mock_get.return_value.read_document.call_count, 2)

    def test_registers_candidate_for_learn(self):
        """读取成功 → 登记候选，供「帮我学习」入库"""
        handler = self._make_handler()
        fake = {"ok": True, "kind": "notable", "node_id": "n1",
                "records": [{"fields": {"名称": "A"}}], "message": "ok"}
        with patch("dingtalk_doc_client.get_doc_client") as mock_get:
            mock_get.return_value.resolve_operator_id.return_value = "union1"
            mock_get.return_value.read_document.return_value = fake
            handler._handle_dingtalk_doc_link(
                "https://alidocs.dingtalk.com/i/nodes/n1", "u1", "s1")
        cand = self._cand.get_pending("u1")
        self.assertIsNotNone(cand)
        self.assertEqual(cand.node_id, "n1")
        self.assertEqual(cand.kind, "notable")
        self.assertFalse(cand.learned)

    def test_link_with_text_and_url_ok(self):
        """链接夹杂说明文字也能提取（不只整条消息是 URL）"""
        handler = self._make_handler()
        fake = {"ok": True, "kind": "notable", "node_id": "n1", "records": []}
        with patch("dingtalk_doc_client.get_doc_client") as mock_get:
            mock_get.return_value.resolve_operator_id.return_value = "union1"
            mock_get.return_value.read_document.return_value = fake
            answer = handler._handle_dingtalk_doc_link(
                "这是测试记录表 https://alidocs.dingtalk.com/i/nodes/n1 你看下",
                "u1", "s1")
        self.assertIsNotNone(answer)
        self.assertIn("0 条记录", answer)


class ListSheetsTests(unittest.TestCase):
    """AI表格工作表列表：钉钉实测返回 {"value":[...]}（非 {"sheets":[...]}）"""

    def setUp(self):
        from unittest import mock as _mock
        self.client = DingTalkDocClient(
            client_id="cid", client_secret="csecret", session=_mock.Mock())

    def test_value_format(self):
        """实测格式：{"value":[{"name","id"}]}，须解析 value"""
        fake = {"value": [{"name": "数据表", "id": "hERWDMS"}]}
        with patch.object(self.client, "_request", return_value=fake) as m:
            sheets = self.client.list_sheets("b1", "op1")
        self.assertEqual(sheets, [{"sheetId": "hERWDMS", "name": "数据表"}])
        self.assertIn("/sheets", m.call_args[0][1])

    def test_old_format_compat(self):
        fake = {"sheets": [{"sheetId": "s1", "name": "旧格式"}]}
        with patch.object(self.client, "_request", return_value=fake):
            sheets = self.client.list_sheets("b1")
        self.assertEqual(sheets, [{"sheetId": "s1", "name": "旧格式"}])

    def test_empty_no_sheets(self):
        with patch.object(self.client, "_request", return_value={"value": []}):
            self.assertEqual(self.client.list_sheets("b1"), [])


class ListFieldsTests(unittest.TestCase):
    """list_fields / read_notable_field_names：字段元数据 + 降级"""

    def setUp(self):
        from unittest import mock as _mock
        self.client = DingTalkDocClient(
            client_id="cid", client_secret="csecret", session=_mock.Mock())

    def test_success_returns_fields(self):
        fake = {"fields": [
            {"fieldId": "01ZM8y7", "name": "项目名称", "type": "String"},
            {"fieldId": "YQnOvE5", "name": "状态", "type": "SingleSelect"},
        ]}
        with patch.object(self.client, "_request", return_value=fake) as m:
            fields = self.client.list_fields("b1", "s1", "op1")
        self.assertEqual(len(fields), 2)
        self.assertEqual(fields[0]["fieldId"], "01ZM8y7")
        self.assertEqual(fields[0]["name"], "项目名称")
        self.assertEqual(fields[0]["type"], "String")
        self.assertIn("/fields", m.call_args[0][1])

    def test_value_format(self):
        """实测格式：{"value":[{"name","id","type"}]}，须解析 value"""
        fake = {"value": [
            {"name": "标题", "id": "01ZM8y7", "type": "primaryDoc"},
            {"name": "状态", "id": "YQnOvE5", "type": "SingleSelect"},
        ]}
        with patch.object(self.client, "_request", return_value=fake) as m:
            fields = self.client.list_fields("b1", "s1", "op1")
        self.assertEqual(len(fields), 2)
        self.assertEqual(fields[0]["fieldId"], "01ZM8y7")
        self.assertEqual(fields[0]["name"], "标题")
        self.assertEqual(fields[0]["type"], "primaryDoc")
        self.assertIn("/fields", m.call_args[0][1])

    def test_all_paths_fail_returns_empty(self):
        with patch.object(self.client, "_request",
                          side_effect=RuntimeError("接口不存在")):
            self.assertEqual(self.client.list_fields("b1", "s1"), [])

    def test_permission_error_not_degraded(self):
        with patch.object(self.client, "_request",
                          side_effect=DingTalkDocPermissionError("无权限")):
            with self.assertRaises(DingTalkDocPermissionError):
                self.client.list_fields("b1", "s1")

    def test_field_names_mapping(self):
        fake = {"fields": [
            {"fieldId": "a1", "name": "名称", "type": "Text"},
            {"fieldId": "b2", "name": "状态", "type": "SingleSelect"},
        ]}
        with patch.object(self.client, "_request", return_value=fake):
            names = self.client.read_notable_field_names("b1", "s1")
        self.assertEqual(names, {"a1": "名称", "b2": "状态"})

    def test_field_names_failure_empty(self):
        with patch.object(self.client, "_request", side_effect=RuntimeError("x")):
            self.assertEqual(self.client.read_notable_field_names("b1", "s1"), {})


class ReadWorkbookTests(unittest.TestCase):
    """workbook（在线表格）读取 + 降级"""

    def setUp(self):
        from unittest import mock as _mock
        self.client = DingTalkDocClient(
            client_id="cid", client_secret="csecret", session=_mock.Mock())

    def test_read_document_workbook_success(self):
        with patch.object(self.client, "detect_kind", return_value="workbook"), \
             patch.object(self.client, "list_workbook_sheets",
                          return_value=[{"sheetId": "w1", "name": "表1"}]), \
             patch.object(self.client, "read_workbook_records",
                          return_value=[{"fields": {"a": 1}}]):
            result = self.client.read_document(
                "https://alidocs.dingtalk.com/i/nodes/n1", operator_id="op")
        self.assertTrue(result["ok"])
        self.assertEqual(result["kind"], "workbook")
        self.assertEqual(result["records"], [{"fields": {"a": 1}}])
        self.assertEqual(result["sheet_id"], "w1")

    def test_read_document_workbook_failure_degraded(self):
        with patch.object(self.client, "detect_kind", return_value="workbook"), \
             patch.object(self.client, "list_workbook_sheets",
                          side_effect=RuntimeError("接口不可用")):
            result = self.client.read_document(
                "https://alidocs.dingtalk.com/i/nodes/n1", operator_id="op")
        self.assertFalse(result["ok"])
        self.assertIn("暂不可用", result["message"])

    def test_detect_kind_falls_back_to_workbook(self):
        with patch.object(self.client, "list_sheets",
                          side_effect=RuntimeError("notable 不符")), \
             patch.object(self.client, "list_workbook_sheets",
                          return_value=[{"sheetId": "w1", "name": "表"}]):
            self.assertEqual(self.client.detect_kind("n1", "op"), "workbook")


class ReadDocumentFieldNamesTests(unittest.TestCase):
    """read_document notable 分支携带 field_names"""

    def setUp(self):
        from unittest import mock as _mock
        self.client = DingTalkDocClient(
            client_id="cid", client_secret="csecret", session=_mock.Mock())

    def test_notable_includes_field_names(self):
        with patch.object(self.client, "detect_kind", return_value="notable"), \
             patch.object(self.client, "read_notable_records",
                          return_value=[{"fields": {"a1": "x"}}]), \
             patch.object(self.client, "read_notable_field_names",
                          return_value={"a1": "名称"}):
            result = self.client.read_document(
                "https://alidocs.dingtalk.com/i/nodes/n1?sheet=s1", operator_id="op")
        self.assertTrue(result["ok"])
        self.assertEqual(result["field_names"], {"a1": "名称"})

    def test_notable_field_names_failure_empty(self):
        """list_fields 失败 → read_notable_field_names 内部降级 {}，read_document 不崩"""
        with patch.object(self.client, "detect_kind", return_value="notable"), \
             patch.object(self.client, "read_notable_records",
                          return_value=[{"fields": {"a1": "x"}}]), \
             patch.object(self.client, "list_fields",
                          side_effect=RuntimeError("x")):
            result = self.client.read_document(
                "https://alidocs.dingtalk.com/i/nodes/n1?sheet=s1", operator_id="op")
        self.assertTrue(result["ok"])
        self.assertEqual(result["field_names"], {})


if __name__ == "__main__":
    unittest.main()
