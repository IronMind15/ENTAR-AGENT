"""通用工具权限与二次确认回归测试。"""

import json
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from tools import (confirm_pending_operation, execute_tool, register,
                   set_current_user_id)  # noqa: E402


class ToolGovernanceTests(unittest.TestCase):
    def test_write_tool_waits_for_confirmation_and_executes_once(self):
        calls = []
        name = "test_confirmed_write"

        @register(name, {"name": name, "description": "测试写操作", "parameters": {}},
                  policy={"confirm": True, "summary": "写入测试数据"})
        def handler(args):
            calls.append(args)
            return json.dumps({"ok": True, "message": "真实写入成功"}, ensure_ascii=False)

        set_current_user_id("governance-user")
        proposal = json.loads(execute_tool(name, {"value": 1}))
        self.assertTrue(proposal["confirmation_required"])
        self.assertEqual(calls, [])

        result = json.loads(confirm_pending_operation("governance-user"))
        self.assertTrue(result["ok"])
        self.assertEqual(calls, [{"value": 1}])

    def test_read_tool_executes_without_confirmation(self):
        name = "test_read_only"

        @register(name, {"name": name, "description": "测试查询", "parameters": {}})
        def handler(args):
            return json.dumps({"ok": True, "value": 42})

        set_current_user_id("read-user")
        result = json.loads(execute_tool(name, {}))
        self.assertEqual(result["value"], 42)


if __name__ == "__main__":
    unittest.main()
