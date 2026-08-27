"""看板变化检测测试（v1.11.0）—— 快照去噪 + diff（纯逻辑）"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"

from scripts.dashboard.alerts import (  # noqa: E402
    change_banner, diff_summary, has_changes, make_snapshot,
)


def _res(**kw):
    base = {
        "source_key": "s1", "name": "板块一", "table_name": "33周", "total": 2,
        "status_counts": {"滞后": 1, "正常": 1},
        "attention_items": [{"项目名称": "项目A", "状态": "滞后", "本周进展": "x"}],
        "normal_items": [{"项目名称": "项目B", "状态": "正常"}],
        "other_items": [],
    }
    base.update(kw)
    return base


class MakeSnapshotTests(unittest.TestCase):
    def test_structure(self):
        snap = make_snapshot([_res()])
        self.assertEqual(len(snap), 1)
        s = snap[0]
        self.assertEqual(s["source_key"], "s1")
        self.assertEqual(s["table_name"], "33周")
        self.assertEqual(s["total"], 2)
        self.assertEqual(s["status_counts"], {"滞后": 1, "正常": 1})
        # attention 归一为 {status, title}，去噪丢弃长文本字段
        self.assertEqual(s["attention"], [
            {"status": "滞后", "title": "项目A"},
        ])
        self.assertEqual(s["normal"], [
            {"status": "正常", "title": "项目B"},
        ])

    def test_json_serializable(self):
        import json
        snap = make_snapshot([_res()])[0]
        # 审查修复（v1.13.4）：此前仅 json.dumps 不抛异常（弱断言）。
        # 补真实行为断言——快照可 JSON round-trip，且关键字段往返后不丢。
        dumped = json.dumps(snap)
        loaded = json.loads(dumped)
        self.assertEqual(loaded["status_counts"], snap["status_counts"])
        self.assertEqual(loaded["attention"], snap["attention"])
        self.assertIn("normal", loaded)

    def test_long_text_not_in_attention_snapshot(self):
        """只有标题+状态进关注快照（changes_only 去噪），易变长文本不进"""
        s = make_snapshot([_res(attention_items=[{
            "项目名称": "项目A", "状态": "滞后", "本周进展": "x" * 1000,
        }])])[0]
        self.assertEqual(s["attention"], [{"status": "滞后", "title": "项目A"}])


class HasChangesTests(unittest.TestCase):
    def test_same_snapshot_no_change(self):
        a = make_snapshot([_res()])
        b = make_snapshot([_res()])
        self.assertFalse(has_changes(a, b))

    def test_total_change_detected(self):
        a = make_snapshot([_res()])
        b = make_snapshot([_res(total=3)])
        self.assertTrue(has_changes(a, b))

    def test_first_snapshot_is_change(self):
        b = make_snapshot([_res()])
        self.assertTrue(has_changes(None, b))

    def test_legacy_snapshot_does_not_report_schema_upgrade_as_business_change(self):
        old = make_snapshot([_res()])
        enriched = _res(
            detailed_items=[{
                "fields": {"项目名称": "项目A", "本周进展": "x"},
                "evidence": {"record_id": "r1", "record_key": "r1"},
            }],
            source_meta={"source_name": "板块一", "source_url": "https://example"},
        )
        new = make_snapshot([enriched])
        self.assertFalse(has_changes(old, new))


class DiffSummaryTests(unittest.TestCase):
    def test_no_diff_empty(self):
        a = make_snapshot([_res()])
        b = make_snapshot([_res()])
        self.assertEqual(diff_summary(a, b), [])

    def test_new_block(self):
        a = make_snapshot([_res()])
        b = make_snapshot([_res(), _res(source_key="s2")])
        diff = diff_summary(a, b)
        self.assertIn({"source_key": "s2", "detail": "新增板块"}, diff)

    def test_total_change(self):
        a = make_snapshot([_res()])
        b = make_snapshot([_res(total=3)])
        diff = diff_summary(a, b)
        self.assertEqual(len(diff), 1)
        self.assertIn("总数 2→3", diff[0]["detail"])

    def test_status_count_change(self):
        a = make_snapshot([_res(status_counts={"滞后": 1, "正常": 1})])
        b = make_snapshot([_res(status_counts={"滞后": 2, "正常": 1})])
        diff = diff_summary(a, b)
        self.assertIn("滞后1→2", diff[0]["detail"])

    def test_attention_item_changed(self):
        a = make_snapshot([_res()])
        b = make_snapshot([_res(attention_items=[
            {"项目名称": "项目C", "状态": "滞后", "本周进展": "y"},
        ])])
        diff = diff_summary(a, b)
        self.assertIn("新增关注1", diff[0]["detail"])
        self.assertIn("移除关注1", diff[0]["detail"])

    def test_block_removed(self):
        a = make_snapshot([_res(), _res(source_key="s2")])
        b = make_snapshot([_res()])
        diff = diff_summary(a, b)
        self.assertIn({"source_key": "s2", "detail": "板块消失"}, diff)

    def test_ignore_long_text_change(self):
        """仅长文本变化不触发（去噪效果）"""
        a = make_snapshot([_res(attention_items=[
            {"项目名称": "项目A", "状态": "滞后", "本周进展": "昨天进展"}])])
        b = make_snapshot([_res(attention_items=[
            {"项目名称": "项目A", "状态": "滞后", "本周进展": "今天进展"}])])
        self.assertFalse(has_changes(a, b))
        self.assertEqual(diff_summary(a, b), [])


class ChangeBannerTests(unittest.TestCase):
    """每日必推顶部变化标注（v1.11.4）"""

    def test_no_change_banner(self):
        a = make_snapshot([_res()])
        b = make_snapshot([_res()])
        self.assertEqual(change_banner(a, b), "📌 今日无变化")

    def test_change_banner_with_detail(self):
        a = make_snapshot([_res()])
        b = make_snapshot([_res(total=3)])
        banner = change_banner(a, b)
        self.assertTrue(banner.startswith("📌 今日变化："))
        self.assertIn("s1", banner)             # name_by_key 缺省回退 source_key
        self.assertIn("总数 2→3", banner)

    def test_change_banner_name_mapping(self):
        """name_by_key 把 source_key 映射成板块名"""
        a = make_snapshot([_res()])
        b = make_snapshot([_res(total=3)])
        banner = change_banner(a, b, name_by_key={"s1": "研发项目现况表"})
        self.assertIn("研发项目现况表", banner)
        self.assertNotIn("s1：", banner)

    def test_change_banner_new_block(self):
        a = make_snapshot([_res()])
        b = make_snapshot([_res(), _res(source_key="s2")])
        banner = change_banner(a, b, name_by_key={"s2": "整机测试问题"})
        self.assertIn("整机测试问题：新增板块", banner)

    def test_first_snapshot_is_change(self):
        b = make_snapshot([_res()])
        banner = change_banner(None, b)
        self.assertTrue(banner.startswith("📌 今日变化："))

    def test_banner_is_bounded_for_many_sources(self):
        new = [dict(_res(), source_key=f"source_{i}") for i in range(50)]
        self.assertLessEqual(len(change_banner([], make_snapshot(new))), 350)


if __name__ == "__main__":
    unittest.main()
