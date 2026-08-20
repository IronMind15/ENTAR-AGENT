"""看板证据链与 LLM 整理流水线测试。"""

import json
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"

from scripts.dashboard.alerts import field_diff, make_snapshot  # noqa: E402
from scripts.dashboard.config_model import FieldSpec, SourceConfig  # noqa: E402
from scripts.dashboard.llm_pipeline import (  # noqa: E402
    _fallback_claims_from_units, build_dashboard_report,
)
from scripts.dashboard.parser import parse_source_records  # noqa: E402


def _source(**overrides):
    values = {
        "key": "future_board",
        "name": "未来新增看板",
        "kind": "notable",
        "base_id": "node-1",
        "source_url": "https://alidocs.dingtalk.com/i/nodes/node-1",
        "field_map": {
            "name": FieldSpec(label="事项名称", type="string", max_len=10),
            "progress": FieldSpec(label="详细进展", type="string", max_len=10),
            "owner": FieldSpec(label="负责人", type="string", max_len=20),
        },
    }
    values.update(overrides)
    return SourceConfig(**values)


class DetailedEvidenceTests(unittest.TestCase):
    def test_display_can_truncate_but_llm_data_keeps_full_value_and_source(self):
        long_progress = "完成了三轮联调，并发现供电波动导致重启，需要周三前完成整改"
        result = parse_source_records(
            _source(),
            [{"recordId": "rec-9", "fields": {
                "name": "整机联调", "progress": long_progress, "owner": "张三",
            }}],
        )
        self.assertTrue(result["items"][0]["详细进展"].endswith("…"))
        detailed = result["detailed_items"][0]
        self.assertEqual(detailed["fields"]["详细进展"], long_progress)
        self.assertEqual(detailed["evidence"]["record_id"], "rec-9")
        self.assertEqual(detailed["evidence"]["source_key"], "future_board")
        self.assertEqual(
            detailed["evidence"]["source_url"],
            "https://alidocs.dingtalk.com/i/nodes/node-1",
        )

    def test_sensitive_fields_are_not_exposed_to_llm(self):
        source = _source(field_map={
            "name": FieldSpec(label="事项名称", type="string"),
            "token": FieldSpec(label="API Token", type="string"),
            "note": FieldSpec(label="业务说明", type="string"),
        })
        result = parse_source_records(source, [{"fields": {
            "name": "事项A", "token": "secret-value", "note": "可发送业务数据",
        }}])
        fields = result["detailed_items"][0]["fields"]
        self.assertNotIn("API Token", fields)
        self.assertEqual(fields["业务说明"], "可发送业务数据")

    def test_duplicate_business_names_still_get_unique_references(self):
        result = parse_source_records(_source(), [
            {"fields": {"name": "同名事项", "progress": "进展A"}},
            {"fields": {"name": "同名事项", "progress": "进展B"}},
        ])
        ids = [x["evidence"]["record_id"] for x in result["detailed_items"]]
        self.assertEqual(len(set(ids)), 2)


class FullSnapshotTests(unittest.TestCase):
    def test_business_field_change_is_detected_with_record_source(self):
        source = _source()
        old = parse_source_records(source, [{"recordId": "r1", "fields": {
            "name": "事项A", "progress": "昨天进展", "owner": "李四",
        }}])
        new = parse_source_records(source, [{"recordId": "r1", "fields": {
            "name": "事项A", "progress": "今天已完成", "owner": "李四",
        }}])
        changes = field_diff(make_snapshot([old]), make_snapshot([new]))
        changed = changes[0]["record_changes"][0]
        self.assertEqual(changed["record_id"], "r1")
        self.assertEqual(changed["changed_fields"]["详细进展"], {
            "before": "昨天进展", "after": "今天已完成",
        })
        self.assertEqual(changed["source_key"], "future_board")


class LlmPipelineTests(unittest.TestCase):
    def test_fallback_claims_round_robin_across_sources(self):
        """规则兜底不能让首个大文件夹耗尽全部 12 条配额。"""
        units = []
        for source in ("folder", "plan", "issue", "project"):
            for index in range(4):
                units.append({
                    "source_key": source,
                    "ref": f"{source}:r{index}",
                    "fields": {"事项": f"{source}-{index}", "进展": "推进中"},
                    "change": {},
                })
        claims = _fallback_claims_from_units(units)
        first_round_sources = {claim["refs"][0].split(":")[0] for claim in claims[:4]}
        self.assertEqual({"folder", "plan", "issue", "project"}, first_round_sources)
        self.assertEqual(12, len(claims))

    def test_document_fallback_uses_content_instead_of_generic_type(self):
        """Word 段落/表格记录不能把“类型”误当成业务标题。"""
        claims = _fallback_claims_from_units([{
            "source_key": "weekly", "ref": "weekly:r1", "change": {},
            "fields": {"类型": "表格2", "内容": "150kW 对拖测试发现 PHY 芯片损坏",
                       "来源文件": "29-30周部门周报"},
        }])
        self.assertIn("150kW 对拖测试", claims[0]["text"])
        self.assertNotIn("表格2", claims[0]["text"])
        self.assertEqual("内容", claims[0]["evidence"][0]["field"])

    def test_valid_reference_without_evidence_is_repaired_from_source_field(self):
        """模型已引用真实记录但漏填 evidence 时，应回填原值而非退化成原始块。"""
        parsed = parse_source_records(_source(), [{"recordId": "r1", "fields": {
            "name": "整机联调", "progress": "已完成对拖测试，待处理 PHY 芯片问题",
        }}])

        def fake_llm(prompt, max_tokens=4000):
            if '"stage": "reduce"' in prompt:
                return ('{"claims":[{"text":"整机联调已完成对拖测试，PHY 芯片问题待处理",'
                        '"level":"risk","refs":["future_board:r1"]}]}')
            return '{"selected_refs":["future_board:r1"]}'

        report = build_dashboard_report([parsed], None, fake_llm,
                                        batch_size=1, max_batch_chars=100000)
        self.assertIn("整机联调已完成对拖测试", report.text)
        self.assertEqual(1, report.verification["accepted_claims"])
        self.assertEqual(1, report.verification["repaired_evidence_claims"])

    def test_long_verified_summary_is_paginated_not_replaced_by_raw_fallback(self):
        """超过单条消息限制时，保留全部 LLM 结论分页，不能退回前四条原文。"""
        parsed = parse_source_records(_source(), [
            {"recordId": f"r{i}", "fields": {
                "name": f"事项{i}", "progress": "关键进展" * 120,
            }} for i in range(8)
        ])

        def fake_llm(prompt, max_tokens=4000):
            if '"stage": "reduce"' in prompt:
                claims = [{
                    "text": f"事项{i}已有可核验的精炼结论" + "，需持续跟进" * 50,
                    "level": "risk", "refs": [f"future_board:r{i}"],
                    "evidence": [{"ref": f"future_board:r{i}", "field": "详细进展"}],
                } for i in range(8)]
                return json.dumps({"claims": claims}, ensure_ascii=False)
            return json.dumps({"selected_refs": [f"future_board:r{i}" for i in range(8)]})

        report = build_dashboard_report([parsed], None, fake_llm,
                                        batch_size=20, max_batch_chars=100000)
        self.assertEqual(8, report.verification["accepted_claims"])
        self.assertEqual(0, report.verification["fallback_claims"])
        self.assertGreater(report.verification["message_pages"], 1)
        self.assertTrue(any("事项7已有可核验的精炼结论" in page for page in report.messages))

    def test_changed_records_are_prioritized_over_unchanged_records(self):
        """日报的 map 输入应先分析今日变化，不能让存量记录挤掉更新。"""
        source = _source()
        old = parse_source_records(source, [
            {"recordId": "r1", "fields": {"name": "存量", "progress": "未变化", "owner": "甲"}},
            {"recordId": "r2", "fields": {"name": "重点", "progress": "昨天", "owner": "乙"}},
        ])
        new = parse_source_records(source, [
            {"recordId": "r1", "fields": {"name": "存量", "progress": "未变化", "owner": "甲"}},
            {"recordId": "r2", "fields": {"name": "重点", "progress": "今天完成", "owner": "乙"}},
        ])
        prompts = []

        def fake_llm(prompt, max_tokens=4000):
            prompts.append(prompt)
            if '"stage": "reduce"' in prompt:
                return ('{"claims":[{"text":"重点今天完成","level":"update",'
                        '"refs":["future_board:r2"],"evidence":['
                        '{"ref":"future_board:r2","field":"详细进展"}]}]}')
            return '{"selected_refs":["future_board:r2"]}'

        report = build_dashboard_report([new], make_snapshot([old]), fake_llm,
                                        batch_size=1, max_batch_chars=100000)
        map_payloads = [p for p in prompts if '"stage": "map"' in p]
        self.assertIn('"priority": "changed"', map_payloads[0])
        self.assertIn("重点今天完成", report.text)

    def test_table_source_never_leaks_pipe_markdown_after_fallback(self):
        """普通文档表格即使走规则兜底，也不能把 | 表格语法发到主动消息。"""
        parsed = parse_source_records(_source(), [{"recordId": "t1", "fields": {
            "name": "| 项目 | 状态 |\n| --- | --- |\n| A | 完成 |",
            "progress": "已完成", "owner": "张三",
        }}])
        report = build_dashboard_report([parsed], None, None)
        self.assertNotRegex(report.text, r"\|.*\|.*\|")

    def test_long_report_is_split_on_sections_not_hard_truncated(self):
        """长报告应产出独立消息分页，不出现半行硬切标记。"""
        parsed = parse_source_records(_source(), [
            {"recordId": f"r{i}", "fields": {
                "name": f"事项{i}", "progress": "详细进展" * 100, "owner": "张三",
            }} for i in range(30)
        ])
        report = build_dashboard_report([parsed], None, None)
        self.assertNotIn("已截断", report.text)
        self.assertGreaterEqual(len(report.messages), 1)
        self.assertTrue(all(len(message) <= 5000 for message in report.messages))
    def test_arbitrary_source_is_chunked_and_output_has_verified_references(self):
        source = _source()
        parsed = parse_source_records(source, [
            {"recordId": f"r{i}", "fields": {
                "name": f"事项{i}", "progress": f"进展{i}", "owner": "王五",
            }}
            for i in range(3)
        ])
        prompts = []

        def fake_llm(prompt, max_tokens=4000):
            prompts.append(prompt)
            if '"stage": "reduce"' in prompt:
                return ('{"headline":"今日重点", "claims":['
                        '{"text":"事项一需关注", "level":"risk",'
                        '"refs":["future_board:r1"],"evidence":['
                        '{"ref":"future_board:r1","field":"详细进展"}]},'
                        '{"text":"无来源内容", "level":"risk",'
                        '"refs":["future_board:r2"],"evidence":['
                        '{"ref":"future_board:r2","field":"不存在字段"}]}]}')
            if "r2" in prompt:
                return '{"selected_refs":["future_board:r2"]}'
            return '{"selected_refs":["future_board:r0","future_board:r1"]}'

        report = build_dashboard_report([parsed], None, fake_llm,
                                        batch_size=2, max_batch_chars=100000)
        map_prompts = [p for p in prompts if '"stage": "map"' in p]
        self.assertEqual(len(map_prompts), 2)
        # 三条详细记录均真正进入 LLM 输入，而不是只给统计数字。
        joined = "\n".join(map_prompts)
        for i in range(3):
            self.assertIn(f"进展{i}", joined)
        self.assertIn("事项一需关注", report.text)
        self.assertIn("[S1-R2]", report.text)
        self.assertIn("依据原值：详细进展：进展1", report.text)
        self.assertNotIn("无来源内容", report.text)
        self.assertIn("[查看原文]", report.text)
        self.assertGreater(report.verification["rejected_claims"], 0)

    def test_no_status_source_is_not_declared_normal_when_llm_fails(self):
        parsed = parse_source_records(_source(), [{"fields": {
            "name": "事项A", "progress": "存在尚未解决的问题", "owner": "赵六",
        }}])

        def broken_llm(prompt, max_tokens=4000):
            raise RuntimeError("temporary failure")

        report = build_dashboard_report([parsed], None, broken_llm)
        self.assertNotIn("整体正常", report.text)
        self.assertIn("事项A", report.text)
        self.assertIn("来源", report.text)

    def test_partial_collection_failure_is_visible_in_report(self):
        parsed = parse_source_records(_source(), [{"fields": {
            "name": "事项A", "progress": "推进中", "owner": "赵六",
        }}])
        report = build_dashboard_report(
            [parsed], None, None,
            collection_errors=["质量问题表：文档权限不足"],
        )
        self.assertIn("数据完整性提醒", report.text)
        self.assertIn("质量问题表：文档权限不足", report.text)


if __name__ == "__main__":
    unittest.main()
