"""文档监测状态语义测试。"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from scripts.dashboard.monitoring import evaluate, render_banner  # noqa: E402


def _snap(value="A"):
    return [{
        "source_key": "doc_1",
        "source_meta": {"source_name": "共享周报"},
        "total": 1,
        "status_counts": {},
        "attention": [],
        "normal": [],
        "records": [{
            "record_key": "r1", "record_id": "r1",
            "fields": {"内容": value}, "evidence": {},
        }],
    }]


class DashboardMonitoringTests(unittest.TestCase):
    def test_first_check_establishes_baseline(self):
        result = evaluate(None, _snap())
        self.assertEqual(result.status, "baseline")
        self.assertIn("基线", result.label)

    def test_changed_check_reports_changes(self):
        result = evaluate(_snap(), _snap("B"))
        self.assertEqual(result.status, "changed")
        self.assertTrue(result.changes)

    def test_unchanged_check_is_explicit(self):
        result = evaluate(_snap(), _snap())
        self.assertEqual(result.status, "unchanged")
        self.assertIn("未发现", result.label)

    def test_partial_check_keeps_valid_data_and_error(self):
        result = evaluate(_snap(), _snap("B"), ["另一个文档：权限不足"])
        self.assertEqual(result.status, "partial")
        self.assertIn("权限不足", result.reason)

    def test_empty_check_is_failure(self):
        result = evaluate(None, [], ["文档无法访问"])
        self.assertEqual(result.status, "failed")
        self.assertIn("无法访问", result.reason)

    def test_banner_explains_result_and_integrity(self):
        result = evaluate(_snap(), _snap("B"), ["附件读取失败"])
        banner = render_banner(result, {"doc_1": "共享周报"})
        self.assertIn("检查结果", banner)
        self.assertIn("变化摘要", banner)
        self.assertIn("附件读取失败", banner)


if __name__ == "__main__":
    unittest.main()
