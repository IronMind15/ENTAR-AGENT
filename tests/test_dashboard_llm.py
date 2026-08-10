"""看板 LLM 组装接入测试（v1.11.0）—— mock call_deepseek，不打真实 API"""

import sys
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from dashboard import service  # noqa: E402
from dashboard.assembler import assemble_markdown  # noqa: E402


def _parsed_results():
    from dashboard.config_model import load_sources
    from dashboard.parser import parse_source_records
    sources = {s.key: s for s in load_sources()}
    proj = parse_source_records(
        sources["project_status"],
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


class CallDeepseekWrapperTests(unittest.TestCase):
    """agent.call_deepseek 公开包装：成功取 content / 失败返回空串"""

    @mock.patch("skills.agent._call_deepseek")
    def test_success_returns_content(self, mock_call):
        mock_call.return_value = {"role": "assistant", "content": "看板正文"}
        from skills.agent import call_deepseek
        result = call_deepseek("组装看板")
        self.assertEqual(result, "看板正文")
        # 透传单个 user 消息
        args = mock_call.call_args[0][0]
        self.assertEqual(args[0]["role"], "user")

    @mock.patch("skills.agent._call_deepseek")
    def test_none_response_returns_empty(self, mock_call):
        mock_call.return_value = None
        from skills.agent import call_deepseek
        self.assertEqual(call_deepseek("x"), "")

    @mock.patch("skills.agent._call_deepseek")
    def test_exception_returns_empty(self, mock_call):
        mock_call.side_effect = RuntimeError("boom")
        from skills.agent import call_deepseek
        self.assertEqual(call_deepseek("x"), "")

    def test_empty_content_falls_back_to_rules(self):
        # 端到端：scheduler 阶段将 call_deepseek 作 llm_func，空串 → 规则兜底
        with mock.patch("skills.agent.call_deepseek", return_value=""):
            from skills.agent import call_deepseek
            text = service.assemble(_parsed_results(), llm_func=call_deepseek)
        self.assertIn("## 一、研发项目现况表", text)


if __name__ == "__main__":
    unittest.main()
