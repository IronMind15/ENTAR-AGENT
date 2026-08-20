"""看板数据解析层测试（v1.11.0）—— 用千问实测基准断言解析逻辑（纯逻辑，无需真实 API）"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"

from scripts.dashboard.config_model import FieldSpec, SourceConfig  # noqa: E402
from scripts.dashboard.parser import (  # noqa: E402
    extract_cell_value, find_latest_week_table, format_number, parse_source_records,
)


def _fixture_sources():
    """A1（v1.12.6）后静态配置已清空，测试自行构造 SourceConfig 双源 fixture。"""
    return {
        "project_status": SourceConfig(
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
        "test_issues": SourceConfig(
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
    }


def _source_by_key(key: str):
    src = _fixture_sources().get(key)
    if src is None:
        raise AssertionError(f"fixture 缺少数据源 {key}")
    return src


class ExtractCellValueTests(unittest.TestCase):
    """单元格值统一解析（方法论 §3.3）"""

    def test_none_and_empty(self):
        self.assertEqual(extract_cell_value(None), "")
        self.assertEqual(extract_cell_value(""), "")
        self.assertEqual(extract_cell_value([]), "")

    def test_plain_string(self):
        self.assertEqual(extract_cell_value("abc"), "abc")

    def test_single_choice_dict(self):
        """单选/状态字段 → name"""
        self.assertEqual(extract_cell_value({"name": "滞后"}), "滞后")
        # 无 name 时回退 text
        self.assertEqual(extract_cell_value({"text": "待验证"}), "待验证")

    def test_multi_choice_list(self):
        """多选/多关联 → name 用 / 连接"""
        cell = [{"name": "项目A"}, {"name": "项目B"}]
        self.assertEqual(extract_cell_value(cell), "项目A / 项目B")

    def test_list_without_name(self):
        cell = [{"text": "x"}, {"text": "y"}]
        self.assertEqual(extract_cell_value(cell), "x / y")

    def test_scalar(self):
        self.assertEqual(extract_cell_value(123), "123")
        self.assertEqual(extract_cell_value(True), "True")


class FindLatestWeekTableTests(unittest.TestCase):
    """周次分表自动检测（方法论 §3.4）"""

    def test_picks_max_week(self):
        tables = [
            {"tableId": "a", "tableName": "32周"},
            {"tableId": "b", "tableName": "33周"},
            {"tableId": "c", "tableName": "31周"},
        ]
        latest = find_latest_week_table(tables)
        self.assertIsNotNone(latest)
        self.assertEqual(latest["tableId"], "b")
        self.assertEqual(latest["tableName"], "33周")

    def test_no_week_number_returns_none(self):
        tables = [{"tableId": "a", "tableName": "汇总"}]
        self.assertIsNone(find_latest_week_table(tables))

    def test_compat_with_list_sheets_name_key(self):
        """list_sheets 返回的 name 键也能识别周次"""
        tables = [
            {"sheetId": "a", "name": "32周"},
            {"sheetId": "b", "name": "33周"},
        ]
        latest = find_latest_week_table(tables)
        self.assertEqual(latest["sheetId"], "b")

    def test_empty_input(self):
        self.assertIsNone(find_latest_week_table([]))
        self.assertIsNone(find_latest_week_table(None))


class FormatNumberTests(unittest.TestCase):
    """数值格式化：小数→百分比；#DIV/0!→无数据"""

    def test_ratio_to_percent(self):
        self.assertEqual(format_number(0.5), "50%")
        self.assertEqual(format_number("0.25"), "25%")

    def test_div0(self):
        self.assertEqual(format_number("#DIV/0!"), "无数据")

    def test_int_drops_decimal(self):
        self.assertEqual(format_number(12.0), "12")
        self.assertEqual(format_number("12.0"), "12")

    def test_empty(self):
        self.assertEqual(format_number(None), "")
        self.assertEqual(format_number(""), "")

    def test_non_numeric_string(self):
        self.assertEqual(format_number("未开始"), "未开始")


class ParseProjectStatusTests(unittest.TestCase):
    """project_status：33周 7条 = 4滞后 + 3正常（千问实测基准）"""

    def _records(self):
        """构造 33 周记录：7 条 = 4 滞后 + 3 正常"""
        statuses = ["滞后"] * 4 + ["正常"] * 3
        records = []
        for i, st in enumerate(statuses, 1):
            records.append({
                "fields": {
                    "01ZM8y7": [{"name": f"项目{i}"}],   # list_name 项目名称
                    "7qnPz0F": {"name": "样机测试"},       # dict_name 阶段
                    "YQnOvE5": {"name": st},               # dict_name 状态
                    "FjrTLFt": "PCS-500",                  # string 产品型号
                    "aepzDFy": "本周完成xx联调",           # string 本周进展
                    "uWD6X8E": "暂无风险",                 # string 风险卡点
                }
            })
        return records

    def test_total_and_groups(self):
        src = _source_by_key("project_status")
        result = parse_source_records(src, self._records(), table_name="33周")
        self.assertEqual(result["total"], 7)
        self.assertEqual(result["table_name"], "33周")
        self.assertEqual(result["status_counts"], {"滞后": 4, "正常": 3})
        self.assertEqual(len(result["attention_items"]), 4)
        self.assertEqual(len(result["normal_items"]), 3)
        self.assertEqual(len(result["other_items"]), 0)

    def test_item_fields_labeled(self):
        src = _source_by_key("project_status")
        result = parse_source_records(src, self._records()[:1], table_name="33周")
        item = result["items"][0]
        self.assertEqual(item["项目名称"], "项目1")
        self.assertEqual(item["状态"], "滞后")
        self.assertEqual(item["阶段"], "样机测试")
        self.assertEqual(item["产品型号"], "PCS-500")


class ParseTestIssuesTests(unittest.TestCase):
    """test_issues：142台 = 故障22 / 待验证3 / 已打包50 / 其他67（千问实测基准）"""

    def _records(self):
        groups = [("故障", 22), ("待验证", 3), ("已打包", 50), ("未开始", 67)]
        records = []
        n = 0
        for st, count in groups:
            for _ in range(count):
                n += 1
                records.append({
                    "fields": {
                        "Hr3tyzt": {"name": st},           # dict_name 状态
                        "GuqYscv": f"SN-{n:03d}",          # string SN
                        "ChZTtyj": f"测试描述 {n}",          # string 描述
                    }
                })
        return records

    def test_142_machine_benchmark(self):
        src = _source_by_key("test_issues")
        result = parse_source_records(src, self._records())
        self.assertEqual(result["total"], 142)
        self.assertEqual(result["status_counts"],
                         {"故障": 22, "待验证": 3, "已打包": 50, "未开始": 67})
        self.assertEqual(len(result["attention_items"]), 25)
        self.assertEqual(len(result["normal_items"]), 50)
        self.assertEqual(len(result["other_items"]), 67)


class EmptyFieldMapFallbackTests(unittest.TestCase):
    """动态源无 field_map 时兜底：label 用 field_id，进 other_items"""

    def test_infers_fields_from_first_record(self):
        from scripts.dashboard.config_model import SourceConfig
        src = SourceConfig(key="dyn", name="动态表", kind="notable",
                           base_id="n1", field_map={})
        records = [{"fields": {"a1": "值A", "b2": {"name": "单选"}}}]
        result = parse_source_records(src, records, table_name="33周")
        self.assertEqual(result["total"], 1)
        self.assertEqual(len(result["items"]), 1)
        item = result["items"][0]
        self.assertEqual(item["a1"], "值A")
        self.assertEqual(item["b2"], "单选")
        # 无「状态」label → 全部进 other_items，不崩、不空
        self.assertEqual(len(result["other_items"]), 1)
        self.assertEqual(result["status_counts"], {})
        self.assertEqual(result["attention_items"], [])
        self.assertEqual(result["normal_items"], [])

    def test_empty_records_no_crash(self):
        from scripts.dashboard.config_model import SourceConfig
        src = SourceConfig(key="dyn", name="动态表", kind="notable",
                           base_id="n1", field_map={})
        result = parse_source_records(src, [])
        self.assertEqual(result["total"], 0)


class ChineseColumnKeyTests(unittest.TestCase):
    """钉钉 AI表格 records 实测用中文列名做字段 key（非 field_id）。

    静态配置 field_map 用 field_id 索引，_extract_field 须按 spec.label 兜底取中文列名。
    """

    def _records(self):
        """同 ParseProjectStatusTests 的记录，但字段键改为中文列名（钉钉实测格式）"""
        return [
            {"fields": {
                "项目名称": [{"name": "项目1"}],   # list_name 项目名称
                "阶段": {"name": "样机测试"},        # dict_name 阶段
                "状态": {"name": "滞后"},            # dict_name 状态
                "产品型号": "PCS-500",
                "本周进展": "本周完成xx联调",
                "风险卡点": "暂无风险",
            }},
            {"fields": {
                "项目名称": [{"name": "项目2"}],
                "阶段": {"name": "量产验证"},
                "状态": {"name": "正常"},
                "产品型号": "PCS-1000",
                "本周进展": "转量产",
                "风险卡点": "无",
            }},
        ]

    def test_chinese_column_keys_resolved_via_label(self):
        """field_map 是 field_id，records 是中文列名 → 按 label 兜底取到"""
        src = _source_by_key("project_status")
        result = parse_source_records(src, self._records(), table_name="33周")
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["status_counts"], {"滞后": 1, "正常": 1})
        item = result["items"][0]
        self.assertEqual(item["项目名称"], "项目1")
        self.assertEqual(item["状态"], "滞后")
        self.assertEqual(item["阶段"], "样机测试")
        self.assertEqual(item["产品型号"], "PCS-500")
        self.assertEqual(len(result["attention_items"]), 1)
        self.assertEqual(len(result["normal_items"]), 1)


if __name__ == "__main__":
    unittest.main()
