"""看板数据源配置加载测试（v1.11.0）"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"

import scripts.dashboard.config_model as cm  # noqa: E402


class LoadSourcesTests(unittest.TestCase):
    """从 JSON 加载数据源配置"""

    def setUp(self):
        # 重置模块级缓存，避免跨测试污染
        self._patch_cache = mock.patch.object(cm, "_cached_sources", None)
        self._patch_mtime = mock.patch.object(cm, "_cached_mtime", -1.0)
        self._patch_cache.start()
        self._patch_mtime.start()
        self.addCleanup(self._patch_cache.stop)
        self.addCleanup(self._patch_mtime.stop)

    def _write_json(self, data: dict) -> str:
        """写临时 JSON 配置，返回路径"""
        fd, path = tempfile.mkstemp(suffix=".json")
        with open(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        return path

    def test_real_file_empty_after_residue_cleanup(self):
        """真实 JSON 已清空 base_id 空的历史残留（v1.12.6），sources 应为空

        旧版断言加载出 project_status/test_issues——二者 base_id 为空（历史残留，
        config_model.source_usable 运行期过滤 + 日志告警），已随 v1.12.6 清理。
        看板数据源现在完全由动态候选（dashboard_doc_candidates）提供。
        """
        # 用真实配置文件路径
        import os
        real_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "scripts/dashboard/dashboard_sources.json")
        with mock.patch.object(cm, "_JSON_PATH", real_path):
            sources = cm.load_sources()
        keys = {s.key for s in sources}
        self.assertEqual(keys, set())

    def test_field_map_parsed(self):
        """field_map 解析为 FieldSpec（含类型/截断）"""
        path = self._write_json({
            "sources": {
                "s1": {
                    "name": "测试源",
                    "base_id": "abc",
                    "table_mode": "latest_week",
                    "field_map": {
                        "f1": {"label": "项目名称", "type": "list_name"},
                        "f2": {"label": "状态", "type": "dict_name"},
                        "f3": {"label": "描述", "type": "string", "max_len": 80},
                    },
                    "status_groups": {"attention": ["滞后"]},
                }
            },
            "push": {}
        })
        with mock.patch.object(cm, "_JSON_PATH", path):
            sources = cm.load_sources()
        self.assertEqual(len(sources), 1)
        s = sources[0]
        self.assertEqual(s.key, "s1")
        self.assertEqual(s.table_mode, "latest_week")
        self.assertEqual(s.field_map["f1"].label, "项目名称")
        self.assertEqual(s.field_map["f1"].type, "list_name")
        self.assertEqual(s.field_map["f3"].max_len, 80)
        self.assertEqual(s.status_groups["attention"], ["滞后"])

    def test_bad_json_falls_back_to_builtin(self):
        """坏 JSON → 内置默认（空列表），不抛异常"""
        fd, path = tempfile.mkstemp(suffix=".json")
        with open(fd, "w", encoding="utf-8") as f:
            f.write("{not valid json")
        with mock.patch.object(cm, "_JSON_PATH", path):
            sources = cm.load_sources()  # 不应抛异常
        self.assertEqual(sources, [])

    def test_get_source_by_key(self):
        """get_source 按 key 返回"""
        path = self._write_json({
            "sources": {"s1": {"name": "源一"}, "s2": {"name": "源二"}},
            "push": {}
        })
        with mock.patch.object(cm, "_JSON_PATH", path):
            s = cm.get_source("s2")
        self.assertIsNotNone(s)
        self.assertEqual(s.name, "源二")
        self.assertIsNone(cm.get_source("missing"))


class SourceUsableTests(unittest.TestCase):
    """source_usable 过滤（v1.11.5：静态空 base_id 残留导致 404 的根因修复）"""

    def test_dingtalk_doc_with_base_id_usable(self):
        s = cm.SourceConfig(key="k", base_id="b1", enabled=True)
        self.assertTrue(cm.source_usable(s))

    def test_dingtalk_doc_without_base_id_not_usable(self):
        s = cm.SourceConfig(key="k", base_id="", enabled=True)
        self.assertFalse(cm.source_usable(s))

    def test_disabled_not_usable(self):
        s = cm.SourceConfig(key="k", base_id="b1", enabled=False)
        self.assertFalse(cm.source_usable(s))

    def test_none_not_usable(self):
        self.assertFalse(cm.source_usable(None))


class LoadPushConfigTests(unittest.TestCase):
    """推送配置加载"""

    @mock.patch("scripts.config.DASHBOARD_PUSH_HOUR", "10")
    @mock.patch("scripts.config.DASHBOARD_PUSH_MINUTE", "30")
    @mock.patch("scripts.config.DASHBOARD_ALERT_MODE", "always")
    @mock.patch("scripts.config.DASHBOARD_TITLE", "测试看板")
    @mock.patch("scripts.config.DASHBOARD_WEEKDAYS", "1,5")
    def test_load_push_config(self):
        cfg = cm.load_push_config()
        self.assertEqual(cfg.push_hour, 10)
        self.assertEqual(cfg.push_minute, 30)
        self.assertEqual(cfg.alert_mode, "always")
        self.assertEqual(cfg.title, "测试看板")
        self.assertEqual(cfg.weekdays, "1,5")

    @mock.patch("scripts.config.DASHBOARD_PUSH_HOUR", "9")
    @mock.patch("scripts.config.DASHBOARD_PUSH_MINUTE", "0")
    @mock.patch("scripts.config.DASHBOARD_ALERT_MODE", "bad_mode")
    @mock.patch("scripts.config.DASHBOARD_TITLE", "x")
    @mock.patch("scripts.config.DASHBOARD_WEEKDAYS", "")
    def test_alert_mode_normalized(self):
        """非法 alert_mode 归一为 always"""
        cfg = cm.load_push_config()
        self.assertEqual(cfg.alert_mode, "always")


if __name__ == "__main__":
    unittest.main()
