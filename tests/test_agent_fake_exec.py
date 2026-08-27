"""Agent 幻觉护栏测试（v1.12.6）—— 未执行写操作工具禁止声称已执行写操作

回归背景 1（2026-08-13 实测）：用户「确认修改」后 Agent 未调用任何工具，
直接回复「✅ 已确认修改！订阅已调整」——纯幻觉，工具什么都没执行。
回归背景 2（2026-08-14 实测）：用户「我说选第一个」后 Agent 只调了只读工具
contact_find（查通讯录），随后编造「📋 看板维护转达工单」——旧护栏因
tools_used=True 放行（v1.12.6 B4 修复：只读工具调用不能作为写操作已执行的依据）。
本护栏：本轮未执行任何写操作工具（零工具 或 只调只读工具）+ 回答完成态声明
+ 用户执行/确认指令 → 追加纠偏。
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"

from scripts.skills.agent import (  # noqa: E402
    _needs_fake_exec_correction, _FAKE_EXEC_CORRECTION,
)


class FakeExecCorrectionUnitTests(unittest.TestCase):
    """纯函数判定：无写操作工具 + 完成态声明 + 执行/确认指令 → 纠偏"""

    def test_confirmed_modify_no_tools_corrects(self):
        # 16:57 实测场景：用户「确认修改」，回答「✅ 已确认修改」→ 必须纠偏
        self.assertTrue(_needs_fake_exec_correction(
            "确认修改", "✅ 已确认修改！您的每日看板订阅已调整。",
            write_tool_used=False))

    def test_execute_verb_no_tools_corrects(self):
        self.assertTrue(_needs_fake_exec_correction(
            "帮我删掉测试ai表格", "已删除该数据源。", write_tool_used=False))

    def test_write_tool_used_not_corrected(self):
        # 真执行过写操作工具（带二次确认）→ 完成态声明有依据，不硬拦
        self.assertFalse(_needs_fake_exec_correction(
            "确认修改", "✅ 已确认修改！", write_tool_used=True))

    def test_readonly_tool_only_still_corrected(self):
        # v1.12.6 B4：只调只读工具（contact_find 查通讯录）≠ 执行了写操作，
        # 编造「工单转达」等完成态写声明 → 仍必须纠偏（工单幻觉回归场景）
        self.assertTrue(_needs_fake_exec_correction(
            "我说选第一个",
            "✅ 看板维护转达工单已生成，只转给袁会荧本人。",
            write_tool_used=False))

    def test_readonly_tool_no_done_claim_not_corrected(self):
        # 只调只读工具但回答无完成态写操作声明 → 不误伤（查询类正常回答）
        self.assertFalse(_needs_fake_exec_correction(
            "我说选第一个",
            "查询结果：当前订阅共 4 个数据源。",
            write_tool_used=False))

    def test_normal_reply_not_corrected(self):
        # 正常聊天回答，无完成态写操作声明
        self.assertFalse(_needs_fake_exec_correction(
            "确认修改", "好的，有什么可以帮您？", write_tool_used=False))

    def test_history_statement_not_corrected(self):
        # 无执行/确认指令（复述历史场景），即便提到「已删除」也不拦
        self.assertFalse(_needs_fake_exec_correction(
            "我昨天删的那个文件在哪", "该文件已删除。", write_tool_used=False))

    # ===== v1.13.3：将来时承诺盲区（2026-08-14 袁会荧实测「按之前的那个来」
    # → LLM 零工具编造「将重新开通」；完成态护栏只拦「已…」，将来时漏网） =====
    def test_future_tense_stop_promise_corrected(self):
        # 口语操作「帮我停掉这个」+ 将来时承诺 → 必须纠偏
        self.assertTrue(_needs_fake_exec_correction(
            "帮我停掉这个", "好的，将停用该看板。", write_tool_used=False))

    def test_future_tense_confirm_corrected(self):
        # 「确认修改」+「将为您调整订阅」→ 将来时承诺 → 必须纠偏
        self.assertTrue(_needs_fake_exec_correction(
            "确认修改", "将为您调整订阅。", write_tool_used=False))

    def test_future_tense_pure_query_not_corrected(self):
        # 纯查询「查一下XX」+「我会帮您查看」→ 无写操作动词 → 不误伤
        self.assertFalse(_needs_fake_exec_correction(
            "查一下故障代码", "我会帮您查看。", write_tool_used=False))


class FakeExecCorrectionIntegrationTests(unittest.TestCase):
    """集成：mock LLM 无写操作工具调用 → 幻觉回答被追加纠偏"""

    def _handle(self, query):
        from scripts.skills.agent import RAGAgentSkill
        return RAGAgentSkill.handle(query, user_id="")

    @mock.patch("scripts.skills.agent._call_deepseek",
                return_value={"content": "✅ 已确认修改！您的订阅已调整。",
                              "tool_calls": None})
    def test_confirm_modify_gets_correction(self, mock_llm):
        r = self._handle("确认修改")
        self.assertIn("本轮我未执行任何写操作", r["answer"])
        self.assertIn("正式确认流程", r["answer"])

    @mock.patch("scripts.skills.agent._call_deepseek",
                return_value={"content": "今天天气不错，适合户外活动。",
                              "tool_calls": None})
    def test_normal_chat_no_correction(self, mock_llm):
        r = self._handle("今天天气怎么样")
        self.assertNotIn(_FAKE_EXEC_CORRECTION, r["answer"])
        self.assertIn("天气不错", r["answer"])

    # ===== v1.13.3：将来时承诺端到端（零工具 + 将来时 → 追加纠偏） =====
    @mock.patch("scripts.skills.agent._call_deepseek",
                return_value={"content": "好的，将停用该看板。", "tool_calls": None})
    def test_future_tense_gets_correction(self, mock_llm):
        r = self._handle("帮我停掉这个")
        self.assertIn("本轮我未执行任何写操作", r["answer"])
        self.assertIn("正式确认流程", r["answer"])

    @mock.patch("scripts.skills.agent._call_deepseek",
                return_value={"content": "我会帮您查看。", "tool_calls": None})
    def test_future_tense_query_no_correction(self, mock_llm):
        r = self._handle("查一下故障代码")
        self.assertNotIn(_FAKE_EXEC_CORRECTION, r["answer"])
        self.assertIn("帮您查看", r["answer"])


if __name__ == "__main__":
    unittest.main()
