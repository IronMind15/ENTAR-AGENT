"""Agent 幻觉护栏测试（v1.14.0）—— 零工具调用禁止声称已执行写操作

回归背景（2026-08-13 实测）：用户「确认修改」后 Agent 未调用任何工具，
直接回复「✅ 已确认修改！订阅已调整」——纯幻觉，工具什么都没执行。
本护栏：本轮零工具调用 + 回答完成态声明 + 用户执行/确认指令 → 追加纠偏。
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from skills.agent import (  # noqa: E402
    _needs_fake_exec_correction, _FAKE_EXEC_CORRECTION,
)


class FakeExecCorrectionUnitTests(unittest.TestCase):
    """纯函数判定：零工具调用 + 完成态声明 + 执行/确认指令 → 纠偏"""

    def test_confirmed_modify_no_tools_corrects(self):
        # 16:57 实测场景：用户「确认修改」，回答「✅ 已确认修改」→ 必须纠偏
        self.assertTrue(_needs_fake_exec_correction(
            "确认修改", "✅ 已确认修改！您的每日看板订阅已调整。", tools_used=False))

    def test_execute_verb_no_tools_corrects(self):
        self.assertTrue(_needs_fake_exec_correction(
            "帮我删掉测试ai表格", "已删除该数据源。", tools_used=False))

    def test_tools_used_not_corrected(self):
        # 调用过工具（哪怕只读）→ 有执行依据，不硬拦
        self.assertFalse(_needs_fake_exec_correction(
            "确认修改", "✅ 已确认修改！", tools_used=True))

    def test_normal_reply_not_corrected(self):
        # 正常聊天回答，无完成态写操作声明
        self.assertFalse(_needs_fake_exec_correction(
            "确认修改", "好的，有什么可以帮您？", tools_used=False))

    def test_history_statement_not_corrected(self):
        # 无执行/确认指令（复述历史场景），即便提到「已删除」也不拦
        self.assertFalse(_needs_fake_exec_correction(
            "我昨天删的那个文件在哪", "该文件已删除。", tools_used=False))


class FakeExecCorrectionIntegrationTests(unittest.TestCase):
    """集成：mock LLM 零工具调用 → 幻觉回答被追加纠偏"""

    def _handle(self, query):
        from skills.agent import RAGAgentSkill
        return RAGAgentSkill.handle(query, user_id="")

    @mock.patch("skills.agent._call_deepseek",
                return_value={"content": "✅ 已确认修改！您的订阅已调整。",
                              "tool_calls": None})
    def test_confirm_modify_gets_correction(self, mock_llm):
        r = self._handle("确认修改")
        self.assertIn("本轮我没有执行任何写操作", r["answer"])
        self.assertIn("正式确认流程", r["answer"])

    @mock.patch("skills.agent._call_deepseek",
                return_value={"content": "今天天气不错，适合户外活动。",
                              "tool_calls": None})
    def test_normal_chat_no_correction(self, mock_llm):
        r = self._handle("今天天气怎么样")
        self.assertNotIn(_FAKE_EXEC_CORRECTION, r["answer"])
        self.assertIn("天气不错", r["answer"])


if __name__ == "__main__":
    unittest.main()
