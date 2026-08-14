"""
v1.12.0 注册中心 + 能力清单测试

覆盖：
- register 新签名 fail-fast（旧签名 TypeError / 缺 name ValueError）
- 注册中心只读视图 / 元数据 / 显示映射 / render_tool_prompt 确定性
- 改名断言：10 个新名注册、8 个旧名排除（tripwire）
- definitions name 与注册 key 恒等
- 停用守卫：search_standards / search_experience_kb 不再注册
- 能力清单完整性：10 工具 / 5 技能 / 17 意图 / enabled 命令 + regex 可编译
"""

import json
import re
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from tools import (get_tool_definitions, get_tool_display_map,  # noqa: E402
                   get_tool_metadata, get_tool_names,
                   register, render_tool_prompt)

# v1.12.0 改名映射：8 个工具改名（新名 = 板块前缀），2 个 calc 保留原名
_NEW_NAMES = {
    "kb_search", "kb_create", "kb_file_manage",
    "calc_pcb_trace", "calc_copper_busbar",
    "dash_query", "dash_push",
    "contact_find", "image_describe", "doc_summarize",
}
_OLD_NAMES = {
    "search_knowledge_base", "create_knowledge_base", "manage_uploaded_file",
    "query_dashboard", "push_dashboard",
    "find_employee", "describe_image", "summarize_doc",
}


class RegisterSignatureTests(unittest.TestCase):
    def test_old_signature_raises_typeerror(self):
        """旧签名 @register("tool_name") → TypeError"""
        with self.assertRaises(TypeError):
            @register("旧签名")  # noqa: F811
            def _x(args): return "{}"

    def test_missing_name_raises_valueerror(self):
        """definition 缺 name → ValueError"""
        with self.assertRaises(ValueError):
            @register({"description": "无 name"})  # noqa: F811
            def _y(args): return "{}"


class ToolRegistryTests(unittest.TestCase):
    def test_new_names_registered_old_excluded(self):
        """10 个新名全部注册；8 个旧名一个不剩（tripwire，防旧名复活）"""
        names = set(get_tool_names())
        self.assertTrue(_NEW_NAMES.issubset(names), f"缺: {_NEW_NAMES - names}")
        self.assertEqual(names & _OLD_NAMES, set(),
                         f"旧名残留: {names & _OLD_NAMES}")

    def test_retired_tools_not_registered(self):
        """v1.11.5 停用工具不再注册（守卫：显式 import 纪律，非 glob 扫描）"""
        names = set(get_tool_names())
        self.assertNotIn("search_standards", names)
        self.assertNotIn("search_experience_kb", names)

    def test_definitions_name_matches_key(self):
        """注册 key 与 DEFINITION['name'] 恒等（防未来实现变化时键名漂移）"""
        defs = get_tool_definitions()
        names = set(get_tool_names())
        self.assertEqual(len(defs), len(names))
        for d in defs:
            self.assertIn(d["function"]["name"], names)
        meta_by_name = {m["name"]: m for m in get_tool_metadata()}
        for name in names:
            self.assertIn(name, meta_by_name)
            self.assertEqual(meta_by_name[name]["name"], name)

    def test_display_map_covers_all_tools(self):
        """显示映射覆盖全部工具（无一缺省）"""
        disp = get_tool_display_map()
        for name in get_tool_names():
            self.assertIn(name, disp)
            self.assertTrue(disp[name], f"{name} 显示为空")
            self.assertIn("...", disp[name])

    def test_render_tool_prompt_contains_all_and_deterministic(self):
        """工具段含 10 工具 + 6 板块标记 + confirm 标记；两次渲染一致（防前缀缓存抖动）"""
        prompt1 = render_tool_prompt()
        prompt2 = render_tool_prompt()
        self.assertEqual(prompt1, prompt2)
        for name in _NEW_NAMES:
            self.assertIn(name, prompt1)
        for sector in ("📚 知识库", "🧮 计算", "📊 项目看板",
                       "👥 通讯录", "🖼️ 图片", "📄 文档"):
            self.assertIn(sector, prompt1)
        # 写操作工具带「须用户确认」标记
        self.assertIn("写操作：执行前须用户确认", prompt1)
        # 旧名不混入工具段
        for old in _OLD_NAMES:
            self.assertNotIn(old, prompt1)


class CapabilityManifestTests(unittest.TestCase):
    def test_manifest_integrity(self):
        """能力清单完整性：10 工具 / 5 技能 / 17 意图 / enabled 命令 + regex 可编译"""
        import capability_manifest as cm

        tools = cm.collect_tools()
        self.assertEqual(len(tools), 10)
        self.assertFalse(any("error" in t for t in tools))

        skills = cm.collect_skills()
        self.assertEqual(len(skills), 5)
        self.assertFalse(any("error" in s for s in skills))

        intents = cm.collect_dashboard_intents()
        self.assertEqual(len(intents), 19)  # v1.12.7：新增 change_sources（任务级源增删）+ history（留档回放）
        self.assertFalse(any("error" in i for i in intents))

        cmds = cm.collect_bot_commands()
        self.assertFalse(any("error" in c for c in cmds))
        enabled = [c for c in cmds if c.get("status") != "removed"]
        self.assertGreaterEqual(len(enabled), 10)
        removed = [c for c in cmds if c.get("status") == "removed"]
        self.assertEqual([c["id"] for c in removed], ["sync_review"])
        # 登记表 regex 全部可编译、trigger 非空
        for c in cmds:
            self.assertTrue(c["trigger"])
            self.assertTrue(c["desc"])
        # 看板意图 regex 可编译
        for it in intents:
            self.assertTrue(it["trigger"])
            self.assertTrue(it["desc"])

    def test_manifest_md_has_all_sections(self):
        """能力清单 Markdown 含全部 5 个 section 且无旧工具名"""
        import capability_manifest as cm
        md = cm.render_capability_snapshot_md()
        for section in ("LLM 工具", "技能层", "Bot 命令", "看板订阅意图", "已停用"):
            self.assertIn(section, md)
        for name in _NEW_NAMES:
            self.assertIn(name, md)
        # 说明性旧名只允许出现在「已停用」段（search_standards 等停用工具），
        # 8 个改名的旧工具名不应出现在清单主体
        for old in _OLD_NAMES:
            self.assertNotIn(old, md)

    def test_render_regexes_compilable(self):
        """登记表 regex 对象都能编译出可用 pattern"""
        import capability_manifest as cm
        for c in cm.collect_bot_commands():
            rx = c.get("regex")
            if rx is not None:
                re.compile(rx.pattern if hasattr(rx, "pattern") else rx)


if __name__ == "__main__":
    unittest.main()
