"""看板 Markdown 组装测试（v1.11.0）—— 规则模板 + validate + LLM 兜底（纯逻辑）"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from dashboard.assembler import (  # noqa: E402
    assemble_markdown, llm_assemble, validate_markdown,
)
from dashboard.config_model import FieldSpec, SourceConfig  # noqa: E402
from dashboard.parser import parse_source_records  # noqa: E402


def _fixture_sources():
    """A1（v1.12.6）后静态配置已清空，测试自行构造 SourceConfig 双源 fixture。"""
    return [
        SourceConfig(
            key="project_status", name="研发项目现况表", kind="notable",
            base_id="b1",
            field_map={
                "01ZM8y7": FieldSpec(label="项目名称", type="list_name"),
                "7qnPz0F": FieldSpec(label="阶段", type="dict_name"),
                "YQnOvE5": FieldSpec(label="状态", type="dict_name"),
                "FjrTLFt": FieldSpec(label="产品型号", type="string"),
                "aepzDFy": FieldSpec(label="本周进展", type="string", max_len=200),
                "uWD6X8E": FieldSpec(label="风险卡点", type="string", max_len=200),
            },
            status_groups={"attention": ["滞后"], "normal": ["正常"]},
        ),
        SourceConfig(
            key="test_issues", name="整机下线测试问题", kind="notable",
            base_id="b2",
            field_map={
                "Hr3tyzt": FieldSpec(label="状态", type="dict_name"),
                "GuqYscv": FieldSpec(label="SN号", type="string"),
                "ChZTtyj": FieldSpec(label="描述", type="string"),
            },
            status_groups={"attention": ["故障", "待验证"],
                           "normal": ["已完成", "已打包"]},
        ),
    ]


def _parsed_results():
    """fixture 配置 + 千问基准记录 → 解析结果（2 个板块）"""
    proj_src, issues_src = _fixture_sources()
    proj = parse_source_records(
        proj_src,
        [{"fields": {
            "01ZM8y7": [{"name": f"项目{i}"}],
            "7qnPz0F": {"name": "样机测试"},
            "YQnOvE5": {"name": st},
            "FjrTLFt": "PCS-500",
            "aepzDFy": "本周完成xx联调",
            "uWD6X8E": "暂无风险",
        }} for i, st in enumerate(["滞后"] * 4 + ["正常"] * 3, 1)],
        table_name="33周",
    )
    groups = [("故障", 22), ("待验证", 3), ("已打包", 50), ("未开始", 67)]
    issue_records = []
    n = 0
    for st, count in groups:
        for _ in range(count):
            n += 1
            issue_records.append({"fields": {
                "Hr3tyzt": {"name": st},
                "GuqYscv": f"SN-{n:03d}",
                "ChZTtyj": f"测试描述 {n}",
            }})
    issues = parse_source_records(issues_src, issue_records)
    return [proj, issues]


class AssembleMarkdownTests(unittest.TestCase):
    """规则模板组装"""

    def setUp(self):
        self.text = assemble_markdown(_parsed_results(), date_str="2026-08-10")

    def test_title_line(self):
        self.assertTrue(self.text.startswith("# 恩特能源每日项目看板"))

    def test_date_line(self):
        self.assertIn("> 数据日期：2026-08-10", self.text)

    def test_today_points_present(self):
        """规则兜底也有「📌 今日要点」段（v1.11.4），创建示例看板不简略"""
        self.assertIn("📌 今日要点：", self.text)
        self.assertIn("研发项目现况表关注4项", self.text)
        self.assertIn("整机下线测试问题关注25项", self.text)

    def test_today_points_no_attention_normal(self):
        """无关注项 → 整体正常兜底"""
        from dashboard.parser import parse_source_records
        sources = {s.key: s for s in _fixture_sources()}
        proj = parse_source_records(
            sources["project_status"],
            [{"fields": {
                "01ZM8y7": [{"name": "项目1"}],
                "7qnPz0F": {"name": "样机测试"},
                "YQnOvE5": {"name": "正常"},
                "FjrTLFt": "PCS-500",
                "aepzDFy": "进展",
                "uWD6X8E": "无风险",
            }}],
            table_name="33周",
        )
        text = assemble_markdown([proj], date_str="2026-08-10")
        self.assertIn("📌 今日要点：整体正常，共 1 条记录", text)

    def test_source_blocks_in_order(self):
        self.assertIn("## 一、研发项目现况表", self.text)
        self.assertIn("## 二、整机下线测试问题", self.text)

    def test_attention_on_top_with_counts(self):
        """关注块置顶，标注数量"""
        self.assertIn("### 🔴 关注（4）", self.text)
        # 关注项第一行：标题 + 状态
        self.assertIn("1. 项目1（滞后）", self.text)
        # 标题字段已作行首，其余字段缩进两格
        self.assertIn("   本周进展：本周完成xx联调", self.text)

    def test_normal_block(self):
        self.assertIn("### ✅ 正常（3）", self.text)

    def test_status_stat_line(self):
        """状态统计一行"""
        self.assertIn("> 共 7 条｜滞后4 / 正常3", self.text)
        self.assertIn("> 共 142 条｜故障22 / 待验证3 / 已打包50 / 未开始67", self.text)

    def test_other_block_count(self):
        self.assertIn("### 其他（67）", self.text)

    def test_no_table_syntax(self):
        """钉钉不支持表格语法"""
        self.assertNotIn("|", self.text.replace("｜", ""))

    def test_len_within_limit(self):
        self.assertLessEqual(len(self.text), 5000)

    def test_title_field_skips_status(self):
        """test_issues 的标题字段应取 SN（跳过状态）"""
        # 关注块第一条是故障项，标题应为 SN-001
        self.assertIn("1. SN-001（故障）", self.text)


class ValidateMarkdownTests(unittest.TestCase):
    def test_valid(self):
        ok, reason = validate_markdown("# 标题\n\n正文内容")
        self.assertTrue(ok)

    def test_empty(self):
        ok, reason = validate_markdown("")
        self.assertFalse(ok)

    def test_table_syntax_rejected(self):
        ok, reason = validate_markdown("| a | b |")
        self.assertFalse(ok)
        self.assertIn("表格", reason)

    def test_too_long_rejected(self):
        ok, reason = validate_markdown("x" * 5001)
        self.assertFalse(ok)
        self.assertIn("超长", reason)


class LlmAssembleTests(unittest.TestCase):
    """LLM 组装 → validate → 规则兜底"""

    def test_llm_result_used_when_valid(self):
        def llm_func(prompt):
            self.assertIn("研发项目现况表", prompt)
            return "# LLM看板\n\n按 LLM 思路组织的内容"
        text = llm_assemble(_parsed_results(), date_str="2026-08-10",
                            llm_func=llm_func)
        self.assertEqual(text, "# LLM看板\n\n按 LLM 思路组织的内容")

    def test_llm_exception_falls_back_to_rules(self):
        def llm_func(prompt):
            raise RuntimeError("API 超时")
        text = llm_assemble(_parsed_results(), llm_func=llm_func)
        self.assertIn("## 一、研发项目现况表", text)  # 规则兜底

    def test_llm_table_syntax_falls_back(self):
        """LLM 输出表格 → validate 拦截 → 规则兜底"""
        def llm_func(prompt):
            return "| 项目 | 状态 |\n| --- | --- |"
        text = llm_assemble(_parsed_results(), llm_func=llm_func)
        self.assertNotIn("|", text.replace("｜", ""))
        self.assertIn("## 一、研发项目现况表", text)

    def test_llm_empty_falls_back(self):
        def llm_func(prompt):
            return ""
        text = llm_assemble(_parsed_results(), llm_func=llm_func)
        self.assertIn("## 一、研发项目现况表", text)

    def test_no_llm_func_uses_rules(self):
        text = llm_assemble(_parsed_results())
        self.assertIn("## 一、研发项目现况表", text)


if __name__ == "__main__":
    unittest.main()
