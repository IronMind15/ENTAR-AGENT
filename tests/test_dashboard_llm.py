"""看板 LLM 组装接入测试（v1.11.0）—— mock call_deepseek，不打真实 API"""

import sys
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"

from scripts.dashboard import service  # noqa: E402
from scripts.dashboard.assembler import assemble_markdown  # noqa: E402


def _parsed_results():
    """构造一个项目源 fixture（不依赖真实 JSON 配置——v1.12.6 配置源已清空，
    数据源全部来自动态候选，测试自行构造 SourceConfig）"""
    from scripts.dashboard.config_model import SourceConfig, FieldSpec
    from scripts.dashboard.parser import parse_source_records
    src = SourceConfig(
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
    )
    proj = parse_source_records(
        src,
        [{"fields": {
            "01ZM8y7": [{"name": "项目1"}],
            "7qnPz0F": {"name": "样机"},
            "YQnOvE5": {"name": "滞后"},
            "FjrTLFt": "PCS-500",
            "aepzDFy": "进展",
            "uWD6X8E": "风险",
        }}],
        table_name="33周")
    return [proj]


class ServiceAssembleTests(unittest.TestCase):
    def test_no_llm_uses_rules(self):
        text = service.assemble(_parsed_results(), date_str="2026-08-10")
        self.assertIn("## 一、研发项目现况表", text)

    def test_llm_success_used(self):
        def llm_func(prompt):
            return "# LLM看板\n\nLLM 内容"
        text = service.assemble(_parsed_results(), llm_func=llm_func)
        self.assertEqual(text, "# LLM看板\n\nLLM 内容")

    def test_llm_failure_falls_back_to_rules(self):
        def llm_func(prompt):
            raise RuntimeError("超时")
        text = service.assemble(_parsed_results(), llm_func=llm_func)
        self.assertEqual(text, assemble_markdown(_parsed_results()))

    def test_llm_timeout_falls_back_to_rules(self):
        """v1.11.5：LLM 组装超 25s → 规则兜底（实测曾 37.8s 拖慢推送）"""
        import threading
        release = threading.Event()
        def slow_func(prompt):
            release.wait(5)   # 阻塞直到测试放行（模拟慢 LLM）
            return "# 太慢了"
        with mock.patch("scripts.dashboard.assembler.LLM_ASSEMBLE_TIMEOUT", 0.2):
            text = service.assemble(_parsed_results(), llm_func=slow_func)
        self.assertEqual(text, assemble_markdown(_parsed_results()))
        release.set()  # 放行后台线程，避免悬挂


class CallDeepseekWrapperTests(unittest.TestCase):
    """agent.call_deepseek 公开包装：成功取 content / 失败返回空串"""

    @mock.patch("scripts.skills.agent._call_deepseek")
    def test_success_returns_content(self, mock_call):
        mock_call.return_value = {"role": "assistant", "content": "看板正文"}
        from scripts.skills.agent import call_deepseek
        result = call_deepseek("组装看板")
        self.assertEqual(result, "看板正文")
        # 透传单个 user 消息
        args = mock_call.call_args[0][0]
        self.assertEqual(args[0]["role"], "user")

    @mock.patch("scripts.skills.agent._call_deepseek")
    def test_none_response_returns_empty(self, mock_call):
        mock_call.return_value = None
        from scripts.skills.agent import call_deepseek
        self.assertEqual(call_deepseek("x"), "")

    @mock.patch("scripts.skills.agent._call_deepseek")
    def test_exception_returns_empty(self, mock_call):
        mock_call.side_effect = RuntimeError("boom")
        from scripts.skills.agent import call_deepseek
        self.assertEqual(call_deepseek("x"), "")

    def test_empty_content_falls_back_to_rules(self):
        # 端到端：scheduler 阶段将 call_deepseek 作 llm_func，空串 → 规则兜底
        with mock.patch("scripts.skills.agent.call_deepseek", return_value=""):
            from scripts.skills.agent import call_deepseek
            text = service.assemble(_parsed_results(), llm_func=call_deepseek)
        self.assertIn("## 一、研发项目现况表", text)

    @mock.patch("scripts.skills.agent._call_deepseek")
    def test_json_wrapper_disables_thinking(self, mock_call):
        mock_call.return_value = {"role": "assistant", "content": "{\"ok\":true}"}
        from scripts.skills.agent import call_deepseek_json
        self.assertEqual(call_deepseek_json("x"), '{"ok":true}')
        self.assertFalse(mock_call.call_args.kwargs["thinking"])
        self.assertTrue(mock_call.call_args.kwargs["json_output"])
        self.assertGreater(mock_call.call_args.kwargs["timeout_seconds"], 60)


if __name__ == "__main__":
    unittest.main()
