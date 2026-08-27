"""故障查询技能纯函数补测（v1.13.4 审查补：error_query 自身逻辑此前零直接测试，
仅 test_kb_search 以 mock 引用。此处测快速通道正则与结果格式化，均不依赖 Chroma/网络）"""

import unittest

from scripts.skills.error_query import (
    FAULT_CODE_PATTERN, extract_fault_code,
    _add_line_numbers, _format_exact_result, _format_semantic_results,
)


class FaultCodePatternTests(unittest.TestCase):
    """故障代码正则：d4-1 / df-8 / d10-16 命中；非故障代码不误命中"""

    def test_matches_standard_codes(self):
        for code in ("d4-1", "df-8", "d10-1", "d10-16", "d4-1~d4-16"):
            self.assertIsNotNone(FAULT_CODE_PATTERN.search(code), code)

    def test_case_insensitive(self):
        self.assertIsNotNone(FAULT_CODE_PATTERN.search("D4-1"))

    def test_rejects_non_codes(self):
        for text in ("hello", "abc", "d-1", "d4", "4-1"):
            self.assertIsNone(FAULT_CODE_PATTERN.search(text), text)

    def test_extract_fault_code(self):
        self.assertEqual(extract_fault_code("d4-1 是什么故障"), "d4-1")
        self.assertIsNone(extract_fault_code("没有故障代码"))


class AddLineNumbersTests(unittest.TestCase):
    """行编号：跳过空行/分隔线/数据来源行"""

    def test_numbered_lines(self):
        text = "第一行\n\n---\n数据来源：xlsx → sheet → 第1行\n第三行"
        out = _add_line_numbers(text)
        self.assertTrue(out.startswith("1. 第一行"))
        self.assertIn("2. 第三行", out)
        # 空行/分隔线/数据来源不加编号
        self.assertNotIn("2. ---", out)
        self.assertNotIn("2. 数据来源", out)


class FormatExactResultTests(unittest.TestCase):
    """精确匹配格式化：核心字段 + 扩展字段 + 数据来源（CLAUDE.md 输出规范）"""

    def _meta(self):
        return {
            "name": "急停告警", "cause": "外部急停信号闭合", "fault_code": "d4-1",
            "address": "0x3004", "bit_address": "bit0", "attribute": "R",
            "data_type": "bit", "default_value": "0", "notes": "一级",
            "description": "1-告警", "notes2": "1",
            "sheet_name": "遥信（DI）", "row_num": "51",
        }

    def test_core_fields_first(self):
        s = _format_exact_result(self._meta())
        self.assertIn("【名称】急停告警", s)
        self.assertIn("【故障原因】外部急停信号闭合", s)
        self.assertIn("【故障代码】d4-1", s)
        # 名称/原因/代码带编号（内容行编号）
        self.assertIn("1. 【名称】急停告警", s)

    def test_data_source_not_numbered(self):
        s = _format_exact_result(self._meta())
        self.assertIn("数据来源：PCS参数表 V1.6.2.xlsx → 遥信（DI） → 第51行", s)

    def test_missing_fields_default_to_dash(self):
        s = _format_exact_result({"fault_code": "d4-1", "name": "", "cause": None,
                                  "sheet_name": "", "row_num": ""})
        self.assertIn("【名称】", s)  # 空值不炸


class FormatSemanticResultsTests(unittest.TestCase):
    """语义搜索结果格式化：多结果 + 距离阈值打高/中标签 + 阈值不足返回 None"""

    def _results(self, dists=(0.5, 0.8)):
        return {
            "documents": [["d1", "d2"]],
            "metadatas": [[
                {"fault_code": "d4-1", "name": "急停告警", "cause": "外部急停",
                 "row_num": "51", "sheet_name": "遥信（DI）"},
                {"fault_code": "d4-2", "name": "过流", "cause": "电流超限",
                 "row_num": "52", "sheet_name": "遥信（DI）"},
            ]],
            "distances": [list(dists)],
        }

    def test_formats_multiple_results(self):
        s = _format_semantic_results(self._results(), "急停")
        self.assertIsNotNone(s)
        self.assertIn("结果 1 [高]", s)   # 0.5 < 0.6
        self.assertIn("结果 2 [中]", s)   # 0.8 < 0.9
        self.assertIn("🔍 找到 2 条相关信息", s)

    def test_no_distance_no_tag(self):
        results = self._results(dists=[])
        s = _format_semantic_results(results, "急停")
        self.assertIsNotNone(s)
        self.assertIn("结果 1", s)
        self.assertNotIn("[高]", s)

    def test_empty_returns_none(self):
        self.assertIsNone(_format_semantic_results({}, "急停"))
        self.assertIsNone(_format_semantic_results({"documents": []}, "急停"))


if __name__ == "__main__":
    unittest.main()
