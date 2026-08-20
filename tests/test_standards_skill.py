"""标准查询技能测试（v1.11.6 统一快速通道）—— 路由 + 三路径 handle，mock 掉真实 Chroma"""

import sys
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"

from scripts.skills import get_matched_skill  # noqa: E402
from scripts.skills import standards_query as sq  # noqa: E402


class StandardsRoutingTests(unittest.TestCase):
    """标准号不抢更高优先级技能（切斯特顿栅栏回归保护）"""

    def test_standard_id_routes_to_standards(self):
        skill = get_matched_skill("GB/T 34133-2023 的试验要求")
        self.assertEqual(skill.name, "标准查询")

    def test_kanban_not_hijacked(self):
        """含标准号的看板场景必须仍走看板（v1.11.6 前 agent 兜底即为此）"""
        skill = get_matched_skill("把 GB/T 34133 也加进每日看板")
        self.assertEqual(skill.name, "dashboard")

    def test_pcb_not_hijacked(self):
        skill = get_matched_skill("IPC-2152 5A 1oz 外层要多宽")
        self.assertEqual(skill.name, "PCB设计计算")

    def test_fault_not_hijacked(self):
        skill = get_matched_skill("帮我查 d4-1 的故障")
        self.assertEqual(skill.name, "故障查询")


class StandardsHandleTests(unittest.TestCase):
    """_handle_impl 三路径（mock search_kb，不连真实 Chroma）"""

    def test_exact_match_single_result(self):
        exact = [{"std_id": "GB/T 34133", "std_title": "电化学储能电站技术规定",
                  "chapter": "6", "chapter_title": "试验方法", "page": "12",
                  "file_name": "GB-T34133.pdf", "confidence": "text",
                  "_match_type": "exact", "_score": 0.0}]
        with mock.patch.object(sq, "search_kb", return_value=exact):
            r = sq._handle_impl("GB/T 34133 的试验要求")
        self.assertIn("GB/T 34133", r["answer"])
        self.assertEqual(r["source"], "标准编号快速匹配")

    def test_semantic_multiple_results(self):
        fake = [{"std_id": "GB/T 34133", "std_title": "标准", "chapter": "6",
                 "chapter_title": "试验方法", "page": "12",
                 "file_name": "f.pdf", "confidence": "text",
                 "_content": "并网电压范围...", "_match_type": "semantic",
                 "_score": 0.3}]
        with mock.patch.object(sq, "search_kb", return_value=fake):
            r = sq._handle_impl("储能变流器并网要求")
        self.assertIn("找到 1 条", r["answer"])
        self.assertEqual(r["source"], "标准语义搜索")

    def test_no_results_gives_tip(self):
        with mock.patch.object(sq, "search_kb", return_value=[]):
            r = sq._handle_impl("不存在的标准")
        self.assertIn("暂未找到", r["answer"])

    def test_empty_query(self):
        r = sq._handle_impl("   ")
        self.assertIn("请输入", r["answer"])

    def test_match_requires_standard_id(self):
        self.assertTrue(sq.StandardsQuerySkill.match("查一下 GB/T 34133-2023"))
        self.assertTrue(sq.StandardsQuerySkill.match("IEC 60664-1 要求"))
        self.assertFalse(sq.StandardsQuerySkill.match("帮我推个看板"))
        self.assertFalse(sq.StandardsQuerySkill.match("你好"))


if __name__ == "__main__":
    unittest.main()
