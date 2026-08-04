"""经验知识库查询测试（第二步 experience_kb）：search_kb / 格式化 / Agent 工具"""

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from skills.experience_query import (
    search_kb, format_experience_result, format_experience_results,
)


class ExperienceSearchTests(unittest.TestCase):
    """search_kb 语义搜索"""

    def test_search_kb_empty_query(self):
        self.assertEqual(search_kb("   "), [])

    def test_search_kb_parses_enhanced_result(self):
        fake = {
            "documents": [["IGBT 过温排查..."]],
            "metadatas": [[{
                "std_title": "经验条目：IGBT过温", "chapter_title": "解决方案",
                "file_name": "逆变器IGBT过温.md",
            }]],
            "distances": [[0.35]],
        }
        with mock.patch("skills.experience_query.enhanced_query",
                        return_value=fake) as m:
            results = search_kb("IGBT 过温怎么排查")
        m.assert_called_once()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["_score"], 0.35)
        self.assertEqual(results[0]["_match_type"], "semantic")
        self.assertIn("_content", results[0])

    def test_search_kb_no_results(self):
        fake = {"documents": [[]], "metadatas": [[]], "distances": [[]]}
        with mock.patch("skills.experience_query.enhanced_query",
                        return_value=fake):
            self.assertEqual(search_kb("不存在的经验"), [])

    def test_search_kb_exception_returns_empty(self):
        with mock.patch("skills.experience_query.enhanced_query",
                        side_effect=RuntimeError("boom")):
            self.assertEqual(search_kb("触发异常"), [])


class ExperienceFormatTests(unittest.TestCase):
    """格式化输出"""

    def test_format_single_result(self):
        meta = {
            "std_title": "经验条目：IGBT过温", "chapter_title": "解决方案",
            "file_name": "逆变器IGBT过温.md", "_content": "清理风道并更换风扇...",
        }
        text = format_experience_result(meta)
        self.assertIn("【标题】经验条目：IGBT过温", text)
        self.assertIn("【阶段】解决方案", text)
        self.assertIn("【内容】", text)
        self.assertIn("经验库 → 逆变器IGBT过温.md", text)

    def test_format_multi_results_with_score_tags(self):
        meta1 = {"std_title": "A", "chapter_title": "根因",
                 "file_name": "a.md", "_content": "x", "_score": 0.3}
        meta2 = {"std_title": "B", "chapter_title": "排查步骤",
                 "file_name": "b.md", "_content": "y", "_score": 0.7}
        text = format_experience_results([meta1, meta2], "q")
        self.assertIn("找到 2 条相关经验", text)
        self.assertIn("────────── 结果 1 [高匹配] ──────────", text)
        self.assertIn("────────── 结果 2 [中匹配] ──────────", text)

    def test_format_empty_returns_none(self):
        self.assertIsNone(format_experience_results([], "q"))


class SearchExperienceToolTests(unittest.TestCase):
    """Agent 工具 search_experience_kb"""

    def test_tool_registered(self):
        from tools import get_tool_names
        self.assertIn("search_experience_kb", get_tool_names())

    def test_tool_empty_query_error(self):
        from tools.search_experience_kb import execute
        r = json.loads(execute({"query": "  "}))
        self.assertIn("error", r)

    def test_tool_found_structure(self):
        from tools.search_experience_kb import execute
        fake_results = [{
            "std_title": "经验条目：过温", "_content": "清理风扇",
            "chapter_title": "解决方案", "_score": 0.3, "_match_type": "semantic",
        }]
        with mock.patch("skills.experience_query.search_kb",
                        return_value=fake_results):
            r = json.loads(execute({"query": "过温"}))
        self.assertTrue(r["found"])
        self.assertEqual(len(r["results"]), 1)
        # 内部字段清理：_content 重命名为 content_summary 供 LLM 参考
        self.assertIn("content_summary", r["results"][0])
        self.assertNotIn("_match_type", r["results"][0])

    def test_tool_not_found(self):
        from tools.search_experience_kb import execute
        with mock.patch("skills.experience_query.search_kb", return_value=[]):
            r = json.loads(execute({"query": "xxx"}))
        self.assertFalse(r["found"])


if __name__ == "__main__":
    unittest.main()
