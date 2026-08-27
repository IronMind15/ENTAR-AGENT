"""v1.11.0 钉钉文档客户端测试：链接解析 + operatorId 解析 + 多链接聚合（纯逻辑，无需真实 API）"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"

from scripts.dingtalk_doc_client import (  # noqa: E402
    DingTalkDocClient, DingTalkDocPermissionError, _ALIDOCS_NODE_RE,
    _DOC_SUMMARY_BLOCKS, _DOC_FULL_BLOCKS, SUMMARY_RECORD_LIMIT,
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


class DocumentMetadataTests(unittest.TestCase):
    def test_dws_metadata_uses_real_document_name_and_cache(self):
        client = DingTalkDocClient()
        completed = type("Completed", (), {
            "returncode": 0,
            "stdout": '{"success":true,"name":"31-32周部门周报","extension":"adoc"}',
        })()
        with patch("scripts.dingtalk_doc_client.shutil.which", return_value="dws.cmd"), \
             patch("scripts.dingtalk_doc_client.subprocess.run", return_value=completed) as run:
            self.assertEqual(client.get_document_name("node-1"), "31-32周部门周报")
            self.assertEqual(client.get_document_name("node-1"), "31-32周部门周报")
        run.assert_called_once()


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
        with patch("scripts.contact_api.get_contact_client") as mock_get:
            mock_client = mock_get.return_value
            mock_client.get_user_detail.return_value = {"unionid": "union-from-api"}
            got = self.client.resolve_operator_id(operator_id="", staff_id="staff-1")
        self.assertEqual("union-from-api", got)
        # 二次调用命中缓存，不再请求
        with patch("scripts.contact_api.get_contact_client") as mock_get2:
            got2 = self.client.resolve_operator_id(operator_id="", staff_id="staff-1")
        mock_get2.assert_not_called()
        self.assertEqual("union-from-api", got2)

    def test_staff_id_no_unionid_raises(self):
        with patch("scripts.contact_api.get_contact_client") as mock_get:
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
        from scripts.dingtalk_doc_client import DingTalkDocPermissionError
        with patch.object(self.client, "list_sheets",
                          side_effect=DingTalkDocPermissionError("无权限")):
            with self.assertRaises(DingTalkDocPermissionError):
                self.client.detect_kind("base1", "op1")


class BotDocLinkTests(unittest.TestCase):
    """dingtalk_bot._handle_dingtalk_doc_link 自动识别逻辑（v1.11.0）"""

    def setUp(self):
        # 隔离文档候选存储，避免污染真实 data/user_store.db
        from scripts.dashboard.doc_candidates import DocCandidateStore
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._db_path = path
        self._cand = DocCandidateStore(db_path=path)
        self._patch_cand = patch("scripts.dashboard.doc_candidates.get_candidate_store",
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
        from scripts.skills import dingtalk_bot
        return object.__new__(dingtalk_bot.ErrorQueryHandler)

    def test_plain_text_returns_none(self):
        handler = self._make_handler()
        self.assertIsNone(handler._handle_dingtalk_doc_link("今天天气如何", "u1", "s1"))

    def test_doc_link_reads_and_replies(self):
        handler = self._make_handler()
        fake = {"ok": True, "kind": "notable", "node_id": "n1",
                "records": [{"fields": {"名称": "A", "状态": "滞后"}}],
                "message": "读取成功"}
        with patch("scripts.dingtalk_doc_client.get_doc_client") as mock_get:
            mock_get.return_value.resolve_operator_id.return_value = "union1"
            mock_get.return_value.read_document.return_value = fake
            answer, _ = handler._handle_dingtalk_doc_link(
                "https://alidocs.dingtalk.com/i/nodes/n1", "u1", "s1")
        self.assertIsNotNone(answer)
        # v1.13.3：回复改为「先收录 → 主动问去向」流（header 报份数 + 可读预览）
        self.assertIn("已收录", answer)
        self.assertIn("接下来怎么用", answer)
        self.assertIn("AI表格", answer)
        self.assertIn("帮我学习", answer)
        # 交互原则：只主动问学习/绑定，不推销推送/订阅
        self.assertNotIn("每日推送", answer)
        self.assertNotIn("订阅", answer)
        # 可读预览：不再是 JSON 整块
        self.assertNotIn("{", answer)
        self.assertIn("名称：A", answer)
        # 看板入口存在（做成每日看板 / 加进看板）
        self.assertIn("看板", answer)

    def test_doc_link_read_failure(self):
        handler = self._make_handler()
        with patch("scripts.dingtalk_doc_client.get_doc_client") as mock_get:
            mock_get.return_value.resolve_operator_id.return_value = "union1"
            mock_get.return_value.read_document.side_effect = RuntimeError("无权限")
            answer, _ = handler._handle_dingtalk_doc_link(
                "https://alidocs.dingtalk.com/i/nodes/n1", "u1", "s1")
        self.assertIn("读取失败", answer)

    def test_doc_link_unsupported_kind(self):
        handler = self._make_handler()
        fake = {"ok": False, "kind": "workbook", "node_id": "n1",
                "message": "暂仅支持 AI表格"}
        with patch("scripts.dingtalk_doc_client.get_doc_client") as mock_get:
            mock_get.return_value.resolve_operator_id.return_value = "union1"
            mock_get.return_value.read_document.return_value = fake
            answer, _ = handler._handle_dingtalk_doc_link(
                "https://alidocs.dingtalk.com/i/nodes/n1", "u1", "s1")
        self.assertIn("暂无法读取", answer)

    def test_multi_links_aggregated(self):
        """多条链接逐条读取 + 聚合摘要（v1.11.3：不限 5 个上限，发多少识别多少）"""
        handler = self._make_handler()
        fakes = [{"ok": True, "kind": "notable", "node_id": f"n{i}",
                  "records": [{"fields": {"名称": f"名{i}"}}], "message": "ok"}
                 for i in range(1, 7)]  # 6 份验证已去 5 个上限
        with patch("scripts.dingtalk_doc_client.get_doc_client") as mock_get:
            mock_get.return_value.resolve_operator_id.return_value = "union1"
            mock_get.return_value.read_document.side_effect = fakes
            answer, _ = handler._handle_dingtalk_doc_link(
                "六份：" + " ".join(
                    f"https://alidocs.dingtalk.com/i/nodes/n{i}" for i in range(1, 7)),
                "u1", "s1")
        for i in range(1, 7):
            self.assertIn(f"名{i}", answer)  # 每份概要都出现
        self.assertEqual(mock_get.return_value.read_document.call_count, 6)

    def test_registers_candidate_for_learn(self):
        """读取成功 → 登记候选，供「帮我学习」入库"""
        handler = self._make_handler()
        fake = {"ok": True, "kind": "notable", "node_id": "n1",
                "records": [{"fields": {"名称": "A"}}], "message": "ok"}
        with patch("scripts.dingtalk_doc_client.get_doc_client") as mock_get:
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
        with patch("scripts.dingtalk_doc_client.get_doc_client") as mock_get:
            mock_get.return_value.resolve_operator_id.return_value = "union1"
            mock_get.return_value.read_document.return_value = fake
            answer, _ = handler._handle_dingtalk_doc_link(
                "这是测试记录表 https://alidocs.dingtalk.com/i/nodes/n1 你看下",
                "u1", "s1")
        self.assertIsNotNone(answer)
        self.assertIn("0 条记录", answer)

    def test_doc_link_with_kanban_intent_becomes_task_draft(self):
        """链接 + 建每日任务 → 草稿优先，不能只回轻量文档概要。"""
        handler = self._make_handler()
        fake = {"ok": True, "kind": "notable", "node_id": "n1",
                "records": [{"fields": {"名称": "A"}}], "message": "ok"}
        with patch("scripts.dingtalk_doc_client.get_doc_client") as mock_get, \
             patch("scripts.skills.dashboard.DashboardSkill._handle_doc_create",
                   return_value={"answer": "好的，将按以下文档做每日看板，请确认："
                                        "📋 数据板块：…\n回复「确认」即可订阅。"}) as create:
            mock_get.return_value.resolve_operator_id.return_value = "union1"
            mock_get.return_value.read_document.return_value = fake
            answer = handler._handle_doc_link_with_kanban(
                "帮我把这几个文件做成每日看板 https://alidocs.dingtalk.com/i/nodes/n1",
                "u1", "s1")
        self.assertIn("已识别并纳入", answer)
        self.assertIn("做每日看板，请确认", answer)
        self.assertEqual(create.call_args.kwargs["source_candidate_ids"], [1])
        self.assertIn("request_text", create.call_args.kwargs)

    def test_long_request_preserves_time_and_report_intent(self):
        """真实式长请求（链接+任务+八点半+三段式）必须交给任务草稿。"""
        handler = self._make_handler()
        fake = {"ok": True, "kind": "folder", "node_id": "f1", "name": "部门周报",
                "children": [{"nodeId": "a1", "name": "周报", "nodeType": "file"}]}
        request = ("把这个文件夹建立一个每日看板任务，每天早晨八点半发给我，"
                   "给出结论、需要关注的问题和需要协调的事情 "
                   "https://alidocs.dingtalk.com/i/nodes/f1")
        with patch("scripts.dingtalk_doc_client.get_doc_client") as mock_get, \
             patch("scripts.skills.dashboard.DashboardSkill._handle_doc_create",
                   return_value={"answer": "任务草稿：08:30，管理晨报模板，回复确认。"}) as create:
            mock_get.return_value.resolve_operator_id.return_value = "union1"
            mock_get.return_value.read_document.return_value = fake
            answer = handler._handle_doc_link_with_kanban(request, "u1", "s1")
        self.assertIn("任务草稿", answer)
        self.assertNotIn("回复「把这个文件夹", answer)
        self.assertEqual(create.call_args.kwargs["request_text"], request)

    def test_doc_link_without_kanban_no_merge(self):
        """v1.11.2：只发文档不涉及看板 → 只回文档摘要，不触发看板技能"""
        handler = self._make_handler()
        fake = {"ok": True, "kind": "notable", "node_id": "n1",
                "records": [{"fields": {"名称": "A"}}], "message": "ok"}
        with patch("scripts.dingtalk_doc_client.get_doc_client") as mock_get, \
             patch("scripts.skills.dashboard.DashboardSkill._handle_doc_create") as m_create:
            mock_get.return_value.resolve_operator_id.return_value = "union1"
            mock_get.return_value.read_document.return_value = fake
            answer = handler._handle_doc_link_with_kanban(
                "看看这份 https://alidocs.dingtalk.com/i/nodes/n1", "u1", "s1")
        self.assertIn("已收录", answer)
        m_create.assert_not_called()

    def test_interactive_card_with_kanban_merges(self):
        """v1.11.2：interactiveCard 消息（文档卡片+做看板）→ 回复合并摘要"""
        from unittest import mock as _mock
        handler = self._make_handler()
        handler.reply_markdown = _mock.MagicMock()
        fake = {"ok": True, "kind": "notable", "node_id": "n1",
                "records": [{"fields": {"名称": "A"}}], "message": "ok"}
        content = {"text": "帮我把这几个文件做成每日看板，每天九点发日报 "
                           "https://alidocs.dingtalk.com/i/nodes/n1"}
        bot_msg = _mock.MagicMock()
        with patch("scripts.dingtalk_doc_client.get_doc_client") as mock_get, \
             patch("scripts.skills.dashboard.DashboardSkill._handle_doc_create",
                   return_value={"answer": "好的，将按以下文档做每日看板，请确认：…"}):
            mock_get.return_value.resolve_operator_id.return_value = "union1"
            mock_get.return_value.read_document.return_value = fake
            handler._handle_interactive_card_message(content, bot_msg, "u1", "s1", "s")
        call = handler.reply_markdown.call_args
        call_text = call.kwargs.get("text") if call.kwargs else call[0][1]
        self.assertIn("已识别并纳入", call_text)
        self.assertIn("做每日看板，请确认", call_text)

    # ===== v1.13.3（建议4）：先收录 → 主动问去向 =====

    def test_doc_link_reply_uses_收录_header_and_numbering(self):
        """多份文档逐份编号 + header 报总份数（不再无编号堆「📑」前缀）"""
        handler = self._make_handler()
        fakes = [{"ok": True, "kind": "notable", "node_id": f"n{i}",
                  "name": f"数据表{i}",
                  "records": [{"fields": {"名称": f"名{i}"}}], "message": "ok"}
                 for i in (1, 2)]
        with patch("scripts.dingtalk_doc_client.get_doc_client") as mock_get:
            mock_get.return_value.resolve_operator_id.return_value = "union1"
            mock_get.return_value.read_document.side_effect = fakes
            answer, _ = handler._handle_dingtalk_doc_link(
                "两份：https://alidocs.dingtalk.com/i/nodes/n1 "
                "https://alidocs.dingtalk.com/i/nodes/n2", "u1", "s1")
        self.assertIn("已收录 2 份文档", answer)
        self.assertIn("1. 《数据表1》", answer)
        self.assertIn("2. 《数据表2》", answer)

    def test_doc_preview_readable_not_json(self):
        """预览是「字段：值」分号分隔，不再是一坨 JSON"""
        from scripts.skills.dingtalk_bot import ErrorQueryHandler
        records = [{"fields": {"名称": "A", "状态": "滞后", "负责人": "张三"}}]
        preview = ErrorQueryHandler._doc_preview(records)
        self.assertNotIn("{", preview)
        self.assertIn("名称：A", preview)
        self.assertIn("状态：滞后", preview)
        self.assertIn("负责人：张三", preview)

    def test_proactive_unbound_ask_shown_when_user_has_tasks(self):
        """用户已有看板任务 → 主动询问新收录文档是否绑定到最近任务"""
        handler = self._make_handler()
        fake = {"ok": True, "kind": "notable", "node_id": "n1",
                "records": [{"fields": {"名称": "A"}}], "message": "ok"}
        with patch("scripts.dingtalk_doc_client.get_doc_client") as mock_get, \
             patch("scripts.dashboard.subscription_store.get_subscription_store") as m_store:
            m_store.return_value.list_for_owner.return_value = [object(), object()]
            mock_get.return_value.resolve_operator_id.return_value = "union1"
            mock_get.return_value.read_document.return_value = fake
            answer, _ = handler._handle_dingtalk_doc_link(
                "https://alidocs.dingtalk.com/i/nodes/n1", "u1", "s1")
        self.assertIn("你已有 2 个看板任务", answer)
        self.assertIn("加进看板", answer)

    def test_proactive_unbound_ask_hidden_without_tasks(self):
        """用户没有看板任务 → 不出现「把《文档名》加进看板」的绑定入口"""
        handler = self._make_handler()
        fake = {"ok": True, "kind": "notable", "node_id": "n1",
                "records": [{"fields": {"名称": "A"}}], "message": "ok"}
        with patch("scripts.dingtalk_doc_client.get_doc_client") as mock_get, \
             patch("scripts.dashboard.subscription_store.get_subscription_store") as m_store:
            m_store.return_value.list_for_owner.return_value = []
            mock_get.return_value.resolve_operator_id.return_value = "union1"
            mock_get.return_value.read_document.return_value = fake
            answer, _ = handler._handle_dingtalk_doc_link(
                "https://alidocs.dingtalk.com/i/nodes/n1", "u1", "s1")
        self.assertIn("接下来怎么用", answer)
        self.assertNotIn("你已有", answer)
        self.assertNotIn("加进看板", answer)


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


class BlocksToMarkdownTests(unittest.TestCase):
    """v1.11.1 blocks → Markdown：段落原文、表格转 Markdown 表格、单元格 \n 转 <br>"""

    def setUp(self):
        from unittest import mock as _mock
        self.client = DingTalkDocClient(
            client_id="cid", client_secret="csecret", session=_mock.Mock())

    def test_paragraph_and_table(self):
        blocks = [
            {"blockType": "paragraph",
             "paragraph": {"text": "要求：每日汇总各项目进展"}},
            {"blockType": "table", "table": {"cells": [
                ["产品型号", "项目简称"],
                ["ET-PCS150K12BM", "150kW A1+A2"],
            ]}},
            {"blockType": "paragraph",
             "paragraph": {"text": "  "}},  # 空白段落应被跳过
        ]
        md = self.client._blocks_to_markdown(blocks)
        self.assertIn("要求：每日汇总各项目进展", md)
        self.assertIn("| 产品型号 | 项目简称 |", md)
        self.assertIn("| ET-PCS150K12BM | 150kW A1+A2 |", md)
        self.assertIn("| --- | --- |", md)
        # 空段落不产生多余空行
        self.assertNotIn("要求：每日汇总各项目进展\n\n\n", md)

    def test_table_cell_newline_to_br(self):
        cells = [["型号", "说明"], ["A1", "第一行\n第二行"]]
        md = self.client._table_to_markdown(cells)
        self.assertIn("第一行<br>第二行", md)

    def test_ragged_rows_padded(self):
        cells = [["a", "b", "c"], ["x", "y"]]
        md = self.client._table_to_markdown(cells)
        lines = md.split("\n")
        self.assertEqual(lines[0].count("|"), 4)  # 3 列 → 4 个分隔符
        self.assertIn("| x | y |  |", lines[2])

    def test_empty_blocks_no_markdown(self):
        self.assertEqual(self.client._blocks_to_markdown([]), "")
        self.assertEqual(self.client._table_to_markdown([]), "")
        self.assertEqual(self.client._table_to_markdown([[""] * 0]), "")


class ReadDocContentTests(unittest.TestCase):
    """v1.11.1 read_doc_content：/v1.0/doc/suites/documents/{node}/blocks"""

    def setUp(self):
        from unittest import mock as _mock
        self.client = DingTalkDocClient(
            client_id="cid", client_secret="csecret", session=_mock.Mock())

    def test_success_returns_markdown_and_blocks(self):
        fake = {"result": {"data": [
            {"blockType": "paragraph", "paragraph": {"text": "标题行"}},
            {"blockType": "table", "table": {"cells": [["a", "b"], ["1", "2"]]}},
        ]}}
        with patch.object(self.client, "_request", return_value=fake) as m:
            out = self.client.read_doc_content("node1", "op1")
        self.assertTrue(out["ok"])
        self.assertEqual(len(out["blocks"]), 2)
        self.assertIn("标题行", out["markdown"])
        self.assertIn("| 1 | 2 |", out["markdown"])
        self.assertIn("/suites/documents/node1/blocks", m.call_args[0][1])

    def test_empty_blocks_returns_ok_false(self):
        with patch.object(self.client, "_request", return_value={"result": {"data": []}}):
            out = self.client.read_doc_content("node1")
        self.assertFalse(out["ok"])
        self.assertIn("无内容", out["message"])

    def test_permission_error_propagates(self):
        with patch.object(self.client, "_request",
                          side_effect=DingTalkDocPermissionError("未开通 Storage.File.Read")):
            with self.assertRaises(DingTalkDocPermissionError):
                self.client.read_doc_content("node1", "op1")

    def test_other_failure_returns_ok_false(self):
        with patch.object(self.client, "_request",
                          side_effect=RuntimeError("网络超时")):
            out = self.client.read_doc_content("node1")
        self.assertFalse(out["ok"])
        self.assertIn("网络超时", out["message"])

    def test_max_blocks_cap(self):
        data = [{"blockType": "paragraph", "paragraph": {"text": f"p{i}"}}
                for i in range(600)]
        with patch.object(self.client, "_request",
                          return_value={"result": {"data": data}}):
            out = self.client.read_doc_content("node1", max_blocks=500)
        self.assertEqual(len(out["blocks"]), 500)


class DetectKindDocTests(unittest.TestCase):
    """v1.11.1 类型探测：notable/workbook 失败后回退 doc"""

    def setUp(self):
        self.client = DingTalkDocClient(
            client_id="cid", client_secret="csecret", api_base="https://x")

    def test_falls_back_to_doc(self):
        with patch.object(self.client, "list_sheets",
                          side_effect=RuntimeError("notable 不符")), \
             patch.object(self.client, "list_workbook_sheets",
                          side_effect=RuntimeError("workbook 不符")), \
             patch.object(self.client, "read_doc_content",
                          return_value={"ok": True, "blocks": [{"blockType": "paragraph"}]}):
            self.assertEqual(self.client.detect_kind("n1", "op"), "doc")

    def test_doc_permission_error_propagates(self):
        with patch.object(self.client, "list_sheets",
                          side_effect=RuntimeError("notable 不符")), \
             patch.object(self.client, "list_workbook_sheets",
                          side_effect=RuntimeError("workbook 不符")), \
             patch.object(self.client, "read_doc_content",
                          side_effect=DingTalkDocPermissionError("无权限")):
            with self.assertRaises(DingTalkDocPermissionError):
                self.client.detect_kind("n1", "op")

    def test_all_fail_unknown(self):
        with patch.object(self.client, "list_sheets",
                          side_effect=RuntimeError("a")), \
             patch.object(self.client, "list_workbook_sheets",
                          side_effect=RuntimeError("b")), \
             patch.object(self.client, "read_doc_content",
                          return_value={"ok": False, "blocks": []}):
            self.assertEqual(self.client.detect_kind("n1", "op"), "unknown")


class ReadDocumentDocTests(unittest.TestCase):
    """v1.11.1 read_document doc 分支：kind=doc 返回 markdown + 逐块 records"""

    def setUp(self):
        from unittest import mock as _mock
        self.client = DingTalkDocClient(
            client_id="cid", client_secret="csecret", session=_mock.Mock())

    def test_doc_branch_returns_markdown_and_records(self):
        blocks = [
            {"blockType": "paragraph", "paragraph": {"text": "周会记录"}},
            {"blockType": "table", "table": {"cells": [["项", "状"], ["A", "滞后"]]}},
        ]
        doc_content = {"ok": True, "markdown": "周会记录\n\n| 项 | 状 |\n| --- | --- |\n| A | 滞后 |",
                       "blocks": blocks, "message": "ok"}
        with patch.object(self.client, "detect_kind", return_value="doc"), \
             patch.object(self.client, "read_doc_content", return_value=doc_content):
            result = self.client.read_document(
                "https://alidocs.dingtalk.com/i/nodes/n1", operator_id="op")
        self.assertTrue(result["ok"])
        self.assertEqual(result["kind"], "doc")
        self.assertIn("markdown", result)
        self.assertIn("| 项 | 状 |", result["markdown"])
        # 逐块 records：段落 + 表格1
        self.assertEqual(len(result["records"]), 2)
        self.assertEqual(result["records"][0]["类型"], "段落")
        self.assertEqual(result["records"][1]["类型"], "表格1")

    def test_doc_read_failure_degraded(self):
        with patch.object(self.client, "detect_kind", return_value="doc"), \
             patch.object(self.client, "read_doc_content",
                          return_value={"ok": False, "message": "无内容"}):
            result = self.client.read_document(
                "https://alidocs.dingtalk.com/i/nodes/n1", operator_id="op")
        self.assertFalse(result["ok"])
        self.assertEqual(result["kind"], "doc")
        self.assertIn("无内容", result["message"])

    def test_doc_permission_error_propagates(self):
        with patch.object(self.client, "detect_kind", return_value="doc"), \
             patch.object(self.client, "read_doc_content",
                          side_effect=DingTalkDocPermissionError("无权限")):
            with self.assertRaises(DingTalkDocPermissionError):
                self.client.read_document(
                    "https://alidocs.dingtalk.com/i/nodes/n1", operator_id="op")


class BotDocLinkKindDocTests(unittest.TestCase):
    """v1.11.1 bot 处理 kind=doc 链接：回复含「文档」+ 登记看板源"""

    def setUp(self):
        from scripts.dashboard.doc_candidates import DocCandidateStore
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._db_path = path
        self._cand = DocCandidateStore(db_path=path)
        self._patch_cand = patch("scripts.dashboard.doc_candidates.get_candidate_store",
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
        from scripts.skills import dingtalk_bot
        return object.__new__(dingtalk_bot.ErrorQueryHandler)

    def test_doc_kind_replies_and_registers_enabled(self):
        handler = self._make_handler()
        fake = {"ok": True, "kind": "doc", "node_id": "n1",
                "records": [{"类型": "段落", "内容": "周会记录"}],
                "markdown": "周会记录", "message": "ok"}
        with patch("scripts.dingtalk_doc_client.get_doc_client") as mock_get:
            mock_get.return_value.resolve_operator_id.return_value = "union1"
            mock_get.return_value.read_document.return_value = fake
            answer, _ = handler._handle_dingtalk_doc_link(
                "https://alidocs.dingtalk.com/i/nodes/n1", "u1", "s1")
        self.assertIn("已收录", answer)
        self.assertIn("文档", answer)  # kind 映射 doc→文档
        self.assertIn("帮我学习", answer)
        cand = self._cand.get_pending("u1")
        self.assertIsNotNone(cand)
        self.assertEqual(cand.kind, "doc")
        self.assertTrue(cand.enabled)  # v1.11.1：doc 也可做看板源
        self.assertIn("doc", self._cand.list_dashboard_ready("u1")[0].kind)


class SummaryModeTests(unittest.TestCase):
    """v1.11.3 概要模式：read_document(summary=True) 只读首屏 limit 条"""

    def setUp(self):
        from unittest import mock as _mock
        self.client = DingTalkDocClient(
            client_id="cid", client_secret="csecret", session=_mock.Mock())

    def test_notable_summary_passes_limit_and_name(self):
        calls = {}

        def fake_read(base, sheet, operator_id="", max_records=100, limit=None):
            calls["limit"] = limit
            return [{"fields": {"a": i}} for i in range(5)]

        with patch.object(self.client, "detect_kind", return_value="notable"), \
             patch.object(self.client, "list_sheets",
                          return_value=[{"sheetId": "s1", "name": "表1"}]), \
             patch.object(self.client, "read_notable_records",
                          side_effect=fake_read), \
             patch.object(self.client, "read_notable_field_names",
                          return_value={}), \
             patch.object(self.client, "_notable_sheet_name",
                          return_value="表1"):
            result = self.client.read_document(
                "https://alidocs.dingtalk.com/i/nodes/n1", operator_id="op",
                summary=True)
        self.assertTrue(result["ok"])
        self.assertEqual(calls["limit"], SUMMARY_RECORD_LIMIT)
        self.assertEqual(result["name"], "表1")

    def test_notable_full_read_no_limit(self):
        calls = {}

        def fake_read(base, sheet, operator_id="", max_records=100, limit=None):
            calls["limit"] = limit
            return []

        with patch.object(self.client, "detect_kind", return_value="notable"), \
             patch.object(self.client, "list_sheets",
                          return_value=[{"sheetId": "s1", "name": "表1"}]), \
             patch.object(self.client, "read_notable_records",
                          side_effect=fake_read), \
             patch.object(self.client, "read_notable_field_names",
                          return_value={}), \
             patch.object(self.client, "_notable_sheet_name",
                          return_value="表1"):
            self.client.read_document(
                "https://alidocs.dingtalk.com/i/nodes/n1", operator_id="op",
                summary=False)
        self.assertIsNone(calls["limit"])

    def test_doc_summary_uses_small_blocks(self):
        calls = {}

        def fake_read_doc(node, operator_id="", max_blocks=500):
            calls["max_blocks"] = max_blocks
            return {"ok": True, "markdown": "x", "blocks": [
                {"blockType": "paragraph", "paragraph": {"text": "标题行"}}]}

        with patch.object(self.client, "detect_kind", return_value="doc"), \
             patch.object(self.client, "read_doc_content",
                          side_effect=fake_read_doc):
            result = self.client.read_document(
                "https://alidocs.dingtalk.com/i/nodes/n1", operator_id="op",
                summary=True)
        self.assertEqual(calls["max_blocks"], _DOC_SUMMARY_BLOCKS)
        self.assertEqual(result["name"], "标题行")  # doc 标题取首段

    def test_doc_full_read_many_blocks(self):
        """v1.11.3 全读模式：doc 上限放宽到 _DOC_FULL_BLOCKS，600 块不再截断"""
        data = [{"blockType": "paragraph", "paragraph": {"text": f"p{i}"}}
                for i in range(600)]
        with patch.object(self.client, "_request",
                          return_value={"result": {"data": data}}):
            out = self.client.read_doc_content("node1",
                                               max_blocks=_DOC_FULL_BLOCKS)
        self.assertEqual(len(out["blocks"]), 600)

    def test_detect_kind_cached(self):
        """v1.11.3 detect_kind 实例级缓存：二次命中不重调探测 API"""
        with patch.object(self.client, "_request", return_value={"records": []}), \
             patch.object(self.client, "list_sheets",
                          return_value=[{"sheetId": "s1", "name": "表"}]) as m:
            self.assertEqual(self.client.detect_kind("n1", "op"), "notable")
            self.assertEqual(self.client.detect_kind("n1", "op"), "notable")
        self.assertEqual(m.call_count, 1)


class WorkbookPaginationTests(unittest.TestCase):
    """v1.11.3 workbook 翻页全读 + 概要 limit"""

    def setUp(self):
        from unittest import mock as _mock
        self.client = DingTalkDocClient(
            client_id="cid", client_secret="csecret", session=_mock.Mock())

    def test_paginates_two_pages(self):
        responses = [
            {"records": [{"fields": {"a": 1}}, {"fields": {"a": 2}}],
             "hasMore": True, "nextToken": "tok2"},
            {"records": [{"fields": {"a": 3}}], "hasMore": False},
        ]
        with patch.object(self.client, "_request",
                          side_effect=responses) as m:
            records = self.client.read_workbook_records("b1", "s1", "op")
        self.assertEqual(len(records), 3)
        # 第二次请求带 nextToken 续取
        self.assertEqual(m.call_args_list[1].kwargs["body"]["nextToken"], "tok2")

    def test_limit_stops_early(self):
        responses = [
            {"records": [{"fields": {"a": 1}}, {"fields": {"a": 2}},
                         {"fields": {"a": 3}}],
             "hasMore": True, "nextToken": "t"},
        ]
        with patch.object(self.client, "_request",
                          side_effect=responses) as m:
            records = self.client.read_workbook_records("b1", "s1", "op",
                                                        limit=2)
        self.assertEqual(len(records), 2)
        self.assertEqual(m.call_count, 1)  # 概要模式只读一次即返回

    def test_safety_cap_stops_endless_cursor(self):
        def endless(method, path, body=None, operator_id=""):
            return {"records": [{"fields": {"a": 1}}],
                    "hasMore": True, "nextToken": "x"}

        with patch.object(self.client, "_request", side_effect=endless):
            records = self.client.read_workbook_records("b1", "s1")
        self.assertGreater(len(records), 10000)
        self.assertLess(len(records), 10020)  # 安全上限截断，不无限循环


class BotSummaryModeTests(unittest.TestCase):
    """v1.11.3 bot 识别时传 summary=True + 回复含名字概要"""

    def setUp(self):
        from scripts.dashboard.doc_candidates import DocCandidateStore
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._db_path = path
        self._cand = DocCandidateStore(db_path=path)
        self._patch_cand = patch("scripts.dashboard.doc_candidates.get_candidate_store",
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
        from scripts.skills import dingtalk_bot
        return object.__new__(dingtalk_bot.ErrorQueryHandler)

    def test_reading_uses_summary_mode(self):
        handler = self._make_handler()
        fake = {"ok": True, "kind": "notable", "node_id": "n1",
                "records": [{"fields": {"名称": "A"}}], "message": "ok"}
        with patch("scripts.dingtalk_doc_client.get_doc_client") as mock_get:
            mock_get.return_value.resolve_operator_id.return_value = "union1"
            mock_get.return_value.read_document.return_value = fake
            handler._handle_dingtalk_doc_link(
                "https://alidocs.dingtalk.com/i/nodes/n1", "u1", "s1")
        kwargs = mock_get.return_value.read_document.call_args[1]
        self.assertTrue(kwargs.get("summary"))

    def test_reply_contains_doc_name(self):
        handler = self._make_handler()
        fake = {"ok": True, "kind": "notable", "node_id": "n1", "name": "33周汇总",
                "records": [{"fields": {"名称": "项目A"}}], "message": "ok"}
        with patch("scripts.dingtalk_doc_client.get_doc_client") as mock_get:
            mock_get.return_value.resolve_operator_id.return_value = "union1"
            mock_get.return_value.read_document.return_value = fake
            answer, _ = handler._handle_dingtalk_doc_link(
                "https://alidocs.dingtalk.com/i/nodes/n1", "u1", "s1")
        self.assertIn("33周汇总", answer)
        self.assertIn("AI表格", answer)
        # 登记候选也带上真名（顺带收益）
        cand = self._cand.get_pending("u1")
        self.assertEqual(cand.name, "33周汇总")

    def test_rich_text_writes_memory(self):
        """v1.11.3：富文本卡片识别后写会话记忆，LLM 才能看到文档"""
        from unittest import mock as _mock
        handler = self._make_handler()
        handler.reply_markdown = _mock.MagicMock()
        handler._rich_text_plain = _mock.MagicMock(
            return_value="https://alidocs.dingtalk.com/i/nodes/n1")
        fake = {"ok": True, "kind": "notable", "node_id": "n1",
                "records": [{"fields": {"名称": "A"}}], "message": "ok"}
        bot_msg = _mock.MagicMock()
        with patch("scripts.dingtalk_doc_client.get_doc_client") as mock_get, \
             patch("scripts.skills.memory.add") as m_add:
            mock_get.return_value.resolve_operator_id.return_value = "union1"
            mock_get.return_value.read_document.return_value = fake
            handler._handle_rich_text_message(bot_msg, "u1", "s1", "s")
        calls = [c[0] for c in m_add.call_args_list]
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0], "u1")
        self.assertEqual(calls[0][1], "user")
        self.assertEqual(calls[1][1], "assistant")
        self.assertIn("已收录", calls[1][2])  # 助理记忆含文档识别回复

    def test_interactive_card_writes_memory(self):
        from unittest import mock as _mock
        handler = self._make_handler()
        handler.reply_markdown = _mock.MagicMock()
        fake = {"ok": True, "kind": "notable", "node_id": "n1",
                "records": [{"fields": {"名称": "A"}}], "message": "ok"}
        content = {"text": "https://alidocs.dingtalk.com/i/nodes/n1"}
        bot_msg = _mock.MagicMock()
        with patch("scripts.dingtalk_doc_client.get_doc_client") as mock_get, \
             patch("scripts.skills.memory.add") as m_add:
            mock_get.return_value.resolve_operator_id.return_value = "union1"
            mock_get.return_value.read_document.return_value = fake
            handler._handle_interactive_card_message(content, bot_msg, "u1", "s1", "s")
        calls = [c[0] for c in m_add.call_args_list]
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][1], "user")
        self.assertEqual(calls[1][1], "assistant")


class ReadDocumentUnknownKindTests(unittest.TestCase):
    """v1.11.10：read_document 对未知文档类型给出用户可操作的提示"""

    def test_unknown_kind_friendly_message(self):
        """同事杨妍发钉盘共享文件链接，三类接口全 400 → detect_kind unknown，
        提示不能裸内部类型名「（unknown）」，要给出钉盘/视图/子表线索与替代路径"""
        from scripts.dingtalk_doc_client import DingTalkDocClient
        client = object.__new__(DingTalkDocClient)
        client.parse_doc_url = lambda url: {"node_id": "n1", "sheet_id": ""}
        client.get_document_name = lambda node_id: ""
        client.resolve_operator_id = lambda *a, **k: ""
        client.detect_kind = lambda node_id, operator_id="": "unknown"
        result = client.read_document("https://alidocs.dingtalk.com/i/nodes/n1")
        self.assertFalse(result["ok"])
        self.assertNotIn("（unknown）", result["message"])
        self.assertIn("钉盘", result["message"])


class DetectKindFolderTests(unittest.TestCase):
    """v1.12.3：dws 权威元信息识别文件夹（nodeType=folder 前置探测，三类 API 不再白试）"""

    def _client_with_meta(self, meta: dict):
        from scripts.dingtalk_doc_client import DingTalkDocClient
        client = object.__new__(DingTalkDocClient)
        client._kind_cache = {}
        client._metadata_cache = {}
        client._allow_dws_metadata = True
        client.get_document_metadata = lambda node_id: dict(meta)
        client.list_sheets = lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("notable 不符"))
        client.list_workbook_sheets = lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("workbook 不符"))
        client.read_doc_content = lambda *a, **k: {"ok": False, "message": "doc 不符"}
        return client

    def test_folder_node_type_detected_first(self):
        """元信息 nodeType=folder → detect_kind 直接返回 folder（三类 API 不调用）"""
        client = self._client_with_meta(
            {"nodeType": "folder", "name": "部门周报", "workspaceId": "ws1"})
        calls = {"n": 0}

        def boom(*a, **k):
            calls["n"] += 1
            raise RuntimeError("三类 API 不应被调用")
        client.list_sheets = boom
        client.list_workbook_sheets = boom
        client.read_doc_content = boom
        self.assertEqual(client.detect_kind("f1"), "folder")
        self.assertEqual(calls["n"], 0)

    def test_non_folder_falls_through_to_triple_api(self):
        """元信息是文件/无 nodeType → 走原三类 API 探测"""
        client = self._client_with_meta({"nodeType": "file", "name": "文档"})
        self.assertEqual(client.detect_kind("n1"), "unknown")


class ListFolderChildrenTests(unittest.TestCase):
    """v1.12.3：dws drive list 枚举文件夹子节点（folder 参数用 nodeId 作 dentryUuid）"""

    def test_parses_children_and_filters_file(self):
        from scripts.dingtalk_doc_client import DingTalkDocClient
        client = object.__new__(DingTalkDocClient)
        client._metadata_cache = {}
        client._allow_dws_metadata = True
        client.get_document_metadata = lambda node_id: {
            "nodeType": "folder", "name": "部门周报", "workspaceId": "ws1"}
        captured = {}

        class FakeCompleted:
            returncode = 0
            stdout = '{"nodes": [{"nodeId": "a1", "name": "33周部门周报", "nodeType": "file", "updateTime": 100}, {"nodeId": "f1", "name": "子文件夹", "nodeType": "folder"}, {"nodeId": "a2", "name": "29-30周部门周报", "nodeType": "file", "updateTime": 50}]}'

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            return FakeCompleted()
        with patch("scripts.dingtalk_doc_client.shutil.which", return_value="dws"), \
             patch("scripts.dingtalk_doc_client.subprocess.run", side_effect=fake_run):
            children = client.list_folder_children("QBnd5ExVEvq")
        # folder 参数须用文件夹 nodeId（dentryUuid），非 folderId
        self.assertIn("--folder", captured["cmd"])
        self.assertIn("QBnd5ExVEvq", captured["cmd"])
        self.assertIn("--workspace", captured["cmd"])
        self.assertEqual(len(children), 2)
        self.assertEqual(children[0]["nodeId"], "a1")
        self.assertEqual(children[0]["name"], "33周部门周报")
        # nodeType!=file 的子文件夹被过滤
        self.assertNotIn("f1", [c["nodeId"] for c in children])

    def test_non_folder_returns_empty(self):
        from scripts.dingtalk_doc_client import DingTalkDocClient
        client = object.__new__(DingTalkDocClient)
        client._metadata_cache = {}
        client._allow_dws_metadata = True
        client.get_document_metadata = lambda node_id: {"nodeType": "file"}
        self.assertEqual(client.list_folder_children("n1"), [])

    def test_failure_degrades_to_empty(self):
        from scripts.dingtalk_doc_client import DingTalkDocClient
        client = object.__new__(DingTalkDocClient)
        client._metadata_cache = {}
        client._allow_dws_metadata = True
        client.get_document_metadata = lambda node_id: {
            "nodeType": "folder", "workspaceId": "ws1"}

        class FakeFailed:
            returncode = 1
            stdout = ""
            stderr = "RESOURCE_NOT_FOUND"

        with patch("scripts.dingtalk_doc_client.shutil.which", return_value="dws"), \
             patch("scripts.dingtalk_doc_client.subprocess.run",
                   return_value=FakeFailed()):
            self.assertEqual(client.list_folder_children("f1"), [])


class ReadDocumentFolderTests(unittest.TestCase):
    """v1.12.3：read_document folder 分支返回子节点枚举 + 文件夹名"""

    def test_folder_branch_returns_children_and_name(self):
        from scripts.dingtalk_doc_client import DingTalkDocClient
        client = object.__new__(DingTalkDocClient)
        client.parse_doc_url = lambda url: {"node_id": "f1", "sheet_id": ""}
        client.get_document_name = lambda node_id: "部门周报"
        client.resolve_operator_id = lambda *a, **k: ""
        client.detect_kind = lambda node_id, operator_id="": "folder"
        client.list_folder_children = lambda node_id: [
            {"nodeId": "a1", "name": "33周部门周报", "nodeType": "file", "updateTime": 100},
            {"nodeId": "a2", "name": "29-30周部门周报", "nodeType": "file", "updateTime": 50},
        ]
        result = client.read_document("https://alidocs.dingtalk.com/i/nodes/f1")
        self.assertTrue(result["ok"])
        self.assertEqual(result["kind"], "folder")
        self.assertEqual(result["name"], "部门周报")
        self.assertEqual(len(result["children"]), 2)
        # children 按 updateTime 降序（最新在前）
        self.assertEqual(result["children"][0]["name"], "33周部门周报")
        self.assertIn("共 2 份文档", result["message"])
        self.assertIn("33周部门周报", result["message"])

    def test_folder_empty_children_message(self):
        from scripts.dingtalk_doc_client import DingTalkDocClient
        client = object.__new__(DingTalkDocClient)
        client.parse_doc_url = lambda url: {"node_id": "f1", "sheet_id": ""}
        client.get_document_name = lambda node_id: "部门周报"
        client.resolve_operator_id = lambda *a, **k: ""
        client.detect_kind = lambda node_id, operator_id="": "folder"
        client.list_folder_children = lambda node_id: []
        result = client.read_document("https://alidocs.dingtalk.com/i/nodes/f1")
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["children"]), 0)
        self.assertNotIn("最新", result["message"])


class BotFolderLinkTests(unittest.TestCase):
    """v1.12.3 bot 识别文件夹链接：回复文件夹概要 + 登记看板源（enabled）"""

    def setUp(self):
        from scripts.dashboard.doc_candidates import DocCandidateStore
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._db_path = path
        self._cand = DocCandidateStore(db_path=path)
        self._patch_cand = patch("scripts.dashboard.doc_candidates.get_candidate_store",
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
        from scripts.skills import dingtalk_bot
        return object.__new__(dingtalk_bot.ErrorQueryHandler)

    def test_folder_link_replies_and_registers_enabled(self):
        handler = self._make_handler()
        fake = {"ok": True, "kind": "folder", "node_id": "f1",
                "name": "部门周报", "message": "识别到文件夹「部门周报」，共 2 份文档",
                "children": [
                    {"nodeId": "a1", "name": "33周部门周报", "nodeType": "file", "updateTime": 100},
                    {"nodeId": "a2", "name": "29-30周部门周报", "nodeType": "file", "updateTime": 50},
                ]}
        with patch("scripts.dingtalk_doc_client.get_doc_client") as mock_get:
            mock_get.return_value.resolve_operator_id.return_value = "union1"
            mock_get.return_value.read_document.return_value = fake
            answer, _ = handler._handle_dingtalk_doc_link(
                "https://alidocs.dingtalk.com/i/nodes/f1", "u1", "s1")
        self.assertIsNotNone(answer)
        self.assertIn("文件夹", answer)
        self.assertIn("部门周报", answer)
        self.assertIn("2 份文档", answer)
        # 文件夹不逐条预览，也不提示「帮我学习」入库（本次只做看板）
        self.assertNotIn("帮我学习", answer)
        # 登记为看板源（enabled + kind=folder + list_dashboard_ready 可见）
        cand = self._cand.get_pending("u1")
        self.assertIsNotNone(cand)
        self.assertEqual(cand.kind, "folder")
        self.assertTrue(cand.enabled)
        self.assertEqual(self._cand.list_dashboard_ready("u1")[0].kind, "folder")


if __name__ == "__main__":
    unittest.main()
