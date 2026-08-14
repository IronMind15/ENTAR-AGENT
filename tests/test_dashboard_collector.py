"""看板采集层测试（v1.11.0）—— mock 客户端，不打真实 API"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from dashboard.collector import Collector  # noqa: E402
from dashboard.config_model import SourceConfig  # noqa: E402


def _source(key="s1", table_mode="latest_week", table_id="", base_id="b1",
            kind="notable", enabled=True):
    return SourceConfig(key=key, name=f"源{key}", base_id=base_id,
                        table_mode=table_mode, table_id=table_id,
                        kind=kind, enabled=enabled)


class MockDocClient:
    """替代 get_doc_client() 的假客户端，记录调用"""

    def __init__(self, sheets=None, records=None):
        self.sheets = sheets or []
        self.records = records or {}
        self.list_calls = 0
        self.read_calls = []
        self.last_operator = None

    def resolve_operator_id(self, operator_id="", staff_id=""):
        if operator_id:
            return operator_id
        if staff_id:
            return f"union_of_{staff_id}"
        return ""

    def list_sheets(self, base_id, operator_id=""):
        self.list_calls += 1
        self.last_operator = operator_id
        return self.sheets

    def read_notable_records(self, base_id, sheet_id, operator_id="", max_records=100):
        self.read_calls.append(sheet_id)
        self.last_operator = operator_id
        return self.records.get(sheet_id, [])


class FolderClient(MockDocClient):
    """文件夹场景假客户端：children 枚举 + 按 nodeId 返回各自读结果（v1.12.6 C8）"""

    def __init__(self, children, reads):
        super().__init__()
        self._children = children
        self._reads = reads
        self.read_order = []

    def list_folder_children(self, base_id):
        return self._children

    def read_doc_content(self, node_id, operator_id=""):
        self.read_order.append(node_id)
        return self._reads.get(node_id, {"ok": False, "message": "未知"})

    @staticmethod
    def _doc_blocks_to_records(blocks):
        return [{"类型": "段落", "内容": b.get("paragraph", {}).get("text", "")}
                for b in blocks]


class CollectLatestWeekTests(unittest.TestCase):
    """latest_week 模式：自动选数字最大周次分表"""

    def test_selects_newest_week_sheet(self):
        client = MockDocClient(
            sheets=[
                {"sheetId": "w32", "name": "32周"},
                {"sheetId": "w33", "name": "33周"},
                {"sheetId": "w31", "name": "31周"},
            ],
            records={"w33": [{"fields": {"f": 1}}]},
        )
        result = Collector(client).collect(_source(), operator_id="op1")
        self.assertEqual(result["error"], "")
        self.assertEqual(result["table_name"], "33周")
        self.assertEqual(result["records"], [{"fields": {"f": 1}}])
        # 只读了 33 周那个 sheet
        self.assertEqual(client.read_calls, ["w33"])
        self.assertEqual(client.list_calls, 1)

    def test_no_week_table_returns_error(self):
        client = MockDocClient(sheets=[{"sheetId": "a", "name": "汇总"}])
        result = Collector(client).collect(_source())
        self.assertNotEqual(result["error"], "")
        self.assertEqual(result["records"], [])


class CollectFixedTests(unittest.TestCase):
    """fixed 模式：直接读 table_id，不列分表"""

    def test_reads_table_id_directly(self):
        client = MockDocClient(records={"t1": [{"fields": {"x": "y"}}]})
        src = _source(table_mode="fixed", table_id="t1")
        result = Collector(client).collect(src)
        self.assertEqual(result["error"], "")
        self.assertEqual(result["records"], [{"fields": {"x": "y"}}])
        self.assertEqual(client.list_calls, 0)  # fixed 模式不调 list_sheets
        self.assertEqual(client.read_calls, ["t1"])

    def test_missing_table_id_returns_error(self):
        client = MockDocClient()
        src = _source(table_mode="fixed", table_id="")
        result = Collector(client).collect(src)
        self.assertNotEqual(result["error"], "")
        self.assertEqual(result["records"], [])


class CollectKindTests(unittest.TestCase):
    """文档类型限制"""

    def test_unknown_kind_returns_error(self):
        # v1.11.1：doc 已是合法类型，用真未知类型（如 pdf）验证拒绝
        result = Collector(MockDocClient()).collect(_source(kind="pdf"))
        self.assertIn("暂不支持", result["error"])

    def test_doc_kind_reads_full_content(self):
        """doc 数据源：不需要 table_id，直接读全文 → 逐块 records"""
        client = MockDocClient()
        client._doc_content = {"ok": True, "blocks": [
            {"blockType": "paragraph", "paragraph": {"text": "要求：每日汇报"}},
            {"blockType": "table", "table": {"cells": [["型号", "项目"], ["A1", "150kW"]]}},
        ]}

        def read_doc_content(base_id, operator_id=""):
            client.last_operator = operator_id
            return client._doc_content
        client.read_doc_content = read_doc_content
        client._doc_blocks_to_records = lambda blocks: [
            {"类型": "段落", "内容": "要求：每日汇报"},
            {"类型": "表格1", "内容": "| 型号 | 项目 |\n| --- | --- |\n| A1 | 150kW |"},
        ]
        result = Collector(client).collect(_source(kind="doc", table_mode="fixed",
                                                  table_id=""))
        self.assertEqual(result["error"], "")
        self.assertEqual(len(result["records"]), 2)
        self.assertEqual(result["records"][0]["内容"], "要求：每日汇报")

    def test_folder_kind_reads_all_children(self):
        """v1.12.6（C8）：folder 数据源 → 枚举子文档逐个解读全部（不再只取最新）"""
        client = FolderClient(
            children=[
                {"nodeId": "a1", "name": "33周部门周报", "nodeType": "file", "updateTime": 100},
                {"nodeId": "a2", "name": "29-30周部门周报", "nodeType": "file", "updateTime": 50},
            ],
            reads={
                "a1": {"ok": True, "blocks": [
                    {"blockType": "paragraph", "paragraph": {"text": "本周完成交付验收"}}]},
                "a2": {"ok": True, "blocks": [
                    {"blockType": "paragraph", "paragraph": {"text": "上周完成设计评审"}}]},
            },
        )
        result = Collector(client).collect(
            _source(kind="folder", table_mode="fixed", table_id=""))
        self.assertEqual(result["error"], "")
        self.assertEqual(len(result["records"]), 2)
        # 两份子文档都读到，每份记录带「来源文件」标签
        by_file = {r["来源文件"]: r["内容"] for r in result["records"]}
        self.assertEqual(by_file, {
            "33周部门周报": "本周完成交付验收",
            "29-30周部门周报": "上周完成设计评审",
        })
        # 按 updateTime 升序逐个读（旧→新）
        self.assertEqual(client.read_order, ["a2", "a1"])
        # 源名即文件夹名，不再标注「最新」
        self.assertEqual(result["name"], "源s1")

    def test_folder_partial_failure_keeps_records_and_surfaces_error(self):
        """v1.12.6（C8）：文件夹部分子文档失败 → 成功记录保留，错误随 records 上报"""
        client = FolderClient(
            children=[
                {"nodeId": "a1", "name": "33周部门周报", "nodeType": "file", "updateTime": 100},
                {"nodeId": "a2", "name": "29-30周部门周报", "nodeType": "file", "updateTime": 50},
            ],
            reads={
                "a1": {"ok": False, "message": "无权限读取"},
                "a2": {"ok": True, "blocks": [
                    {"blockType": "paragraph", "paragraph": {"text": "上周完成设计评审"}}]},
            },
        )
        result = Collector(client).collect(
            _source(kind="folder", table_mode="fixed", table_id=""))
        # 成功的记录仍在，且打上来源标签
        self.assertEqual(len(result["records"]), 1)
        self.assertEqual(result["records"][0]["来源文件"], "29-30周部门周报")
        # 失败的子文档体现在 error 里（供 collect_and_parse 上报不中断）
        self.assertIn("1/2", result["error"])
        self.assertIn("33周部门周报", result["error"])

    def test_folder_all_children_fail_returns_error(self):
        """v1.12.6（C8）：文件夹全部子文档失败 → error + 无 records（整源跳过）"""
        client = FolderClient(
            children=[
                {"nodeId": "a1", "name": "33周部门周报", "nodeType": "file", "updateTime": 100},
                {"nodeId": "a2", "name": "29-30周部门周报", "nodeType": "file", "updateTime": 50},
            ],
            reads={
                "a1": {"ok": False, "message": "无权限读取"},
                "a2": {"ok": False, "message": "网络超时"},
            },
        )
        result = Collector(client).collect(
            _source(kind="folder", table_mode="fixed", table_id=""))
        self.assertNotEqual(result["error"], "")
        self.assertIn("全部", result["error"])
        self.assertEqual(result["records"], [])

    def test_folder_empty_children_returns_error(self):
        """文件夹枚举失败/为空 → 记 error 不崩"""
        client = MockDocClient()
        client.list_folder_children = lambda base_id: []
        result = Collector(client).collect(
            _source(kind="folder", table_mode="fixed", table_id=""))
        self.assertNotEqual(result["error"], "")
        self.assertEqual(result["records"], [])

    def test_staff_id_fallback_to_union_id(self):
        """无显式 operator_id 时用 staff_id → unionId 兜底（定时/工具场景）"""
        client = MockDocClient(
            sheets=[{"sheetId": "w1", "name": "33周"}],
            records={"w1": [{"fields": {"a": 1}}]},
        )
        result = Collector(client).collect(_source(), staff_id="staff9")
        self.assertEqual(result["error"], "")
        self.assertEqual(client.last_operator, "union_of_staff9")


class CollectAllTests(unittest.TestCase):
    """并发采集：单源失败不拖累整体"""

    def test_all_sources_collected_in_config_order(self):
        client = MockDocClient(
            sheets=[{"sheetId": "w1", "name": "33周"}],
            records={"w1": [{"fields": {"a": 1}}]},
        )
        s1 = _source("a", table_mode="latest_week")
        s2 = _source("b", table_mode="fixed", table_id="t2")
        client.records["t2"] = [{"fields": {"b": 2}}]
        results = Collector(client).collect_all([s1, s2], operator_id="op")
        self.assertEqual([r["source_key"] for r in results], ["a", "b"])
        self.assertTrue(all(r["error"] == "" for r in results))

    def test_single_failure_does_not_break_others(self):
        class FlakyClient(MockDocClient):
            def read_notable_records(self, base_id, sheet_id, operator_id="",
                                     max_records=100):
                if sheet_id == "bad":
                    raise RuntimeError("权限不足")
                return [{"fields": {"ok": 1}}]

        client = FlakyClient(sheets=[{"sheetId": "bad", "name": "33周"}])
        s_bad = _source("bad", table_mode="latest_week")
        s_ok = _source("ok", table_mode="fixed", table_id="good")
        results = Collector(client).collect_all([s_bad, s_ok])
        by_key = {r["source_key"]: r for r in results}
        self.assertIn("权限不足", by_key["bad"]["error"])
        self.assertEqual(by_key["ok"]["error"], "")
        self.assertEqual(by_key["ok"]["records"], [{"fields": {"ok": 1}}])

    def test_disabled_source_skipped(self):
        client = MockDocClient(sheets=[{"sheetId": "w1", "name": "33周"}])
        s_off = _source("off", enabled=False)
        results = Collector(client).collect_all([s_off])
        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
