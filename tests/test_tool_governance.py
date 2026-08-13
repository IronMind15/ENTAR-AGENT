"""通用工具权限与二次确认回归测试。"""

import json
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from pending_context import PT_TOOL, is_confirm_text  # noqa: E402
from tools import (confirm_pending_operation, execute_tool, register,
                   set_current_user_id, _tool_registry)  # noqa: E402


class ToolGovernanceTests(unittest.TestCase):
    def test_write_tool_waits_for_confirmation_and_executes_once(self):
        calls = []
        name = "test_confirmed_write"

        @register({"name": name, "description": "测试写操作", "parameters": {}},
                  policy={"confirm": True, "summary": "写入测试数据"})
        def handler(args):
            calls.append(args)
            return json.dumps({"ok": True, "message": "真实写入成功"}, ensure_ascii=False)

        # v1.12.0：测试工具注册后立即注销，避免污染全局注册中心
        # （能力清单完整性断言 10 工具依赖干净注册表）
        self.addCleanup(_tool_registry.pop, name, None)

        set_current_user_id("governance-user")
        proposal = json.loads(execute_tool(name, {"value": 1}))
        self.assertTrue(proposal["confirmation_required"])
        self.assertEqual(calls, [])

        result = json.loads(confirm_pending_operation("governance-user"))
        self.assertTrue(result["ok"])
        self.assertEqual(calls, [{"value": 1}])

    def test_push_action_confirm_word_recognized_and_executes(self):
        """「确认推送」必须被确认词表识别（dash_push 写操作确认死循环真实回归）。

        链路：LLM 调写工具 → execute_tool 建 PT_TOOL pending → 用户回「确认推送」
        → is_confirm_text 必须 True（此前 False 落 Agent，LLM 反复要求再确认、
        推送永不执行）→ confirm_pending_operation 真实执行 handler。
        """
        calls = []
        name = "test_push_action"
        user_id = "push-confirm-user"

        @register({"name": name, "description": "模拟看板推送", "parameters": {}},
                  policy={"confirm": True, "summary": "向订阅接收人推送看板"})
        def handler(args):
            calls.append(args)
            return json.dumps({"ok": True, "message": "已向订阅接收人推送看板"},
                              ensure_ascii=False)

        self.addCleanup(_tool_registry.pop, name, None)

        set_current_user_id(user_id)
        proposal = json.loads(execute_tool(name, {}))
        self.assertTrue(proposal["confirmation_required"])

        # 关键回归：用户回复「确认推送」→ 确认词必须命中，bot 确认路由才会
        # 拦截并执行（不落 Agent 死循环）
        self.assertTrue(is_confirm_text("确认推送", PT_TOOL))
        self.assertTrue(is_confirm_text("好，推送", PT_TOOL))
        # 单独动作词不是确认
        self.assertFalse(is_confirm_text("推送", PT_TOOL))

        result = json.loads(confirm_pending_operation(user_id))
        self.assertTrue(result["ok"])
        self.assertEqual(calls, [{}])   # 真实执行了一次，而非 LLM 口头声称

    def test_read_tool_executes_without_confirmation(self):
        name = "test_read_only"

        @register({"name": name, "description": "测试查询", "parameters": {}})
        def handler(args):
            return json.dumps({"ok": True, "value": 42})

        # v1.12.0：测试工具注册后立即注销，避免污染全局注册中心
        self.addCleanup(_tool_registry.pop, name, None)

        set_current_user_id("read-user")
        result = json.loads(execute_tool(name, {}))
        self.assertEqual(result["value"], 42)


if __name__ == "__main__":
    unittest.main()
