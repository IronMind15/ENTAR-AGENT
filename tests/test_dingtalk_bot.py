"""钉钉机器人体验改进测试：秒回判断 / 提示文案 / 错误码"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from skills.dingtalk_bot import (
    _is_fast_operation, PENDING_HINT_TEXT, ERROR_MESSAGE, ERROR_CODE_INTERNAL,
)


class FastOperationTests(unittest.TestCase):
    """秒回判断：快速操作不提示，慢操作（Agent）提示"""

    def test_fault_code_is_fast(self):
        """精确故障代码 → 秒回，不提示"""
        self.assertTrue(_is_fast_operation("d4-1"))
        self.assertTrue(_is_fast_operation("查一下 df-8"))

    def test_pcb_calc_is_fast(self):
        """PCB 计算 → 秒回，不提示"""
        self.assertTrue(_is_fast_operation("3A 1oz 外层走线要多宽"))
        self.assertTrue(_is_fast_operation("50Ω 微带 阻抗多少"))

    def test_review_commands_are_fast(self):
        """审核指令 / 审核口令 → 秒回"""
        self.assertTrue(_is_fast_operation("查看我的审核ID"))
        self.assertTrue(_is_fast_operation("同意同步 ABC12345"))
        self.assertTrue(_is_fast_operation("拒绝同步 ABC12345 内容不对"))

    def test_agent_chat_is_slow(self):
        """普通聊天 / 复杂问题 → 走 Agent，需要提示"""
        self.assertFalse(_is_fast_operation("你好"))
        self.assertFalse(_is_fast_operation("今天天气怎么样"))
        self.assertFalse(_is_fast_operation("我这个逆变器报错了怎么回事"))

    def test_empty_is_not_fast(self):
        self.assertFalse(_is_fast_operation(""))


class HintConstantsTests(unittest.TestCase):
    """提示文案与错误码存在性"""

    def test_hint_texts_defined(self):
        self.assertTrue(PENDING_HINT_TEXT)
        self.assertIn("请稍候", PENDING_HINT_TEXT)

    def test_error_message_format(self):
        msg = ERROR_MESSAGE.format(code=ERROR_CODE_INTERNAL)
        self.assertIn("1000", msg)
        self.assertIn("错误码", msg)


if __name__ == "__main__":
    unittest.main()
