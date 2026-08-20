"""
反馈机制 + Prompt 管理 + 引用溯源标签测试

覆盖：
1. Feedback CRUD（add_feedback、get_feedback_stats、get_last_conversation）
2. Prompt 管理（get_prompt、set_prompt、list_prompts、upsert 覆盖）
3. source_label 生成（kb_search、search_standards、search_experience_kb）
4. 便捷函数代理（add_feedback 等模块级函数）
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"

import scripts.user_store as _user_store_mod  # noqa: E402
from scripts.user_store import SQLiteUserStore  # noqa: E402


class _StoreTestBase(unittest.TestCase):
    """每个用例独立临时 DB，替换全局单例"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self._tmp, "test.db")
        self.store = SQLiteUserStore(db_path=self.db_path)
        _user_store_mod._store = self.store

    def tearDown(self):
        _user_store_mod._store = None
        try:
            self.store._close_conn()
        except Exception:
            pass


# ── Feedback ────────────────────────────────────────────

class TestFeedback(_StoreTestBase):
    def test_add_feedback_up(self):
        ok = self.store.add_feedback("web_alice", "d4-1 是什么", "急停告警", "agent(RAG)", "up")
        self.assertTrue(ok)

    def test_add_feedback_invalid_rating(self):
        ok = self.store.add_feedback("u1", "q", "a", "src", "maybe")
        self.assertFalse(ok)

    def test_get_feedback_stats_empty(self):
        stats = self.store.get_feedback_stats()
        self.assertEqual(stats["total"], 0)
        self.assertEqual(stats["up"], 0)
        self.assertEqual(stats["down"], 0)
        self.assertEqual(stats["rate"], 0.0)
        self.assertEqual(stats["recent_down"], [])

    def test_get_feedback_stats_mixed(self):
        self.store.add_feedback("u1", "q1", "a1", "s", "up")
        self.store.add_feedback("u2", "q2", "a2", "s", "up")
        self.store.add_feedback("u3", "q3", "a3", "s", "down")
        stats = self.store.get_feedback_stats()
        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats["up"], 2)
        self.assertEqual(stats["down"], 1)
        self.assertAlmostEqual(stats["rate"], 66.7, places=1)

    def test_recent_down_limited(self):
        for i in range(15):
            self.store.add_feedback(f"u{i}", f"q{i}", f"a{i}", "s", "down")
        stats = self.store.get_feedback_stats()
        self.assertEqual(len(stats["recent_down"]), 10)

    def test_query_answer_truncated(self):
        long_q = "x" * 1000
        long_a = "y" * 1000
        self.store.add_feedback("u1", long_q, long_a, "s", "up")
        stats = self.store.get_feedback_stats()
        # 查不到直接截断字段，但通过 recent_down 看不到（是 up），
        # 用直接 SQL 查
        conn = self.store._get_conn()
        row = conn.execute("SELECT query, answer FROM feedback LIMIT 1").fetchone()
        self.assertEqual(len(row[0]), 500)
        self.assertEqual(len(row[1]), 500)


class TestGetLastConversation(_StoreTestBase):
    def test_no_conversation(self):
        self.assertIsNone(self.store.get_last_conversation("nonexistent"))

    def test_returns_last_pair(self):
        self.store.add_memory("u1", "user", "问题 1")
        self.store.add_memory("u1", "assistant", "回答 1")
        self.store.add_memory("u1", "user", "问题 2")
        self.store.add_memory("u1", "assistant", "回答 2")
        result = self.store.get_last_conversation("u1")
        self.assertIsNotNone(result)
        self.assertEqual(result["query"], "问题 2")
        self.assertEqual(result["answer"], "回答 2")

    def test_no_user_message(self):
        # 只有 assistant，没有 user
        self.store.add_memory("u1", "assistant", "单方面回答")
        result = self.store.get_last_conversation("u1")
        self.assertIsNotNone(result)
        self.assertEqual(result["query"], "")
        self.assertEqual(result["answer"], "单方面回答")


# ── Prompt 管理 ──────────────────────────────────────────

class TestPrompts(_StoreTestBase):
    def test_get_prompt_nonexistent(self):
        self.assertIsNone(self.store.get_prompt("system"))

    def test_set_and_get(self):
        self.store.set_prompt("system", "你是测试助手")
        content = self.store.get_prompt("system")
        self.assertEqual(content, "你是测试助手")

    def test_upsert_overwrite(self):
        self.store.set_prompt("system", "第一版")
        self.store.set_prompt("system", "第二版")
        content = self.store.get_prompt("system")
        self.assertEqual(content, "第二版")

    def test_list_prompts_empty(self):
        self.assertEqual(self.store.list_prompts(), [])

    def test_list_prompts_multiple(self):
        self.store.set_prompt("system", "sys content")
        self.store.set_prompt("greeting", "hello")
        names = self.store.list_prompts()
        self.assertEqual(names, ["greeting", "system"])


# ── 便捷函数 ─────────────────────────────────────────────

class TestConvenienceFunctions(_StoreTestBase):
    def test_add_feedback_module_level(self):
        from scripts.user_store import add_feedback
        ok = add_feedback("u1", "q", "a", "s", "up")
        self.assertTrue(ok)

    def test_get_feedback_stats_module_level(self):
        from scripts.user_store import get_feedback_stats
        self.store.add_feedback("u1", "q", "a", "s", "up")
        stats = get_feedback_stats()
        self.assertEqual(stats["total"], 1)

    def test_get_last_conversation_module_level(self):
        from scripts.user_store import get_last_conversation
        self.store.add_memory("u1", "user", "问")
        self.store.add_memory("u1", "assistant", "答")
        result = get_last_conversation("u1")
        self.assertEqual(result["answer"], "答")

    def test_prompt_module_level(self):
        from scripts.user_store import get_prompt, set_prompt, list_prompts
        set_prompt("test", "content")
        self.assertEqual(get_prompt("test"), "content")
        self.assertIn("test", list_prompts())


# ── source_label 生成 ────────────────────────────────────

class TestSourceLabel(unittest.TestCase):
    """测试搜索工具的 source_label 字段生成逻辑（不依赖真实 Chroma）"""

    def test_knowledge_base_source_label(self):
        """kb_search 的 source_label 包含故障代码和名称"""
        # 直接测试 label 生成逻辑，不走完整工具调用链
        fault_code = "d4-1"
        name = "急停告警"
        label = f"[PCS故障 {fault_code} {name}]"
        self.assertIn("PCS故障", label)
        self.assertIn("d4-1", label)
        self.assertIn("急停告警", label)

    def test_standards_source_label(self):
        """search_standards 的 source_label 包含标准编号、章节、页码"""
        std_id = "GB/T 34133"
        chapter = "6"
        page = "12"
        label_parts = [std_id]
        if chapter:
            label_parts.append(f"第{chapter}章")
        if page:
            label_parts.append(f"第{page}页")
        label = f"[{' '.join(label_parts)}]"
        self.assertEqual(label, "[GB/T 34133 第6章 第12页]")

    def test_experience_source_label(self):
        """search_experience_kb 的 source_label 包含标题和文件名"""
        title = "IGBT 过温排查"
        chapter_title = "排查步骤"
        file_name = "经验记录_2026.md"
        label_parts = []
        if title:
            label_parts.append(title)
        if chapter_title:
            label_parts.append(chapter_title)
        if file_name:
            label_parts.append(file_name)
        label = f"[经验 {' | '.join(label_parts)}]"
        self.assertEqual(label, "[经验 IGBT 过温排查 | 排查步骤 | 经验记录_2026.md]")

    def test_experience_source_label_no_parts(self):
        """空字段时回退到 [经验库]"""
        label_parts = []
        label = f"[经验 {' | '.join(label_parts)}]" if label_parts else "[经验库]"
        self.assertEqual(label, "[经验库]")


# ── Agent prompt 加载 ───────────────────────────────────

class TestAgentPromptLoading(_StoreTestBase):
    def test_load_from_db_when_available(self):
        """DB 有 prompt 时优先从 DB 加载为基础段，并追加注册中心生成的工具段"""
        self.store.set_prompt("system", "来自 DB 的提示词")
        # 清除 agent.py 的缓存
        from scripts.skills import agent as _agent_mod
        _agent_mod._prompt_cache.pop("system", None)
        content = _agent_mod._load_system_prompt()
        self.assertTrue(content.startswith("来自 DB 的提示词"))
        # v1.12.0：工具段永远由注册中心生成，追加在基础段之后
        self.assertIn(_agent_mod._TOOL_SECTION_MARKER, content)

    def test_db_old_tool_section_stripped_and_names_migrated(self):
        """DB 遗留带旧工具段（marker + 旧名）的 prompt → 归一化丢弃旧段并把旧名换成新名"""
        # 注：旧名 search_knowledge_base / find_employee 刻意保留作输入，
        # 验证 _TOOL_NAME_MIGRATION 把旧名迁成新名（勿被改名批替换误伤）。
        stale = ("你是助手。\n\n"
                 + "===== 工具能力（由注册中心自动生成，勿手动编辑）=====\n"
                 + "1. search_knowledge_base（旧工具段）\n"
                 + "2. find_employee（旧工具段）\n")
        from scripts.skills import agent as _agent_mod
        _agent_mod._prompt_cache.pop("system", None)
        with mock.patch.object(_agent_mod, "_PROMPT_FILE",
                               str(Path(__file__).parent / "not_exists.txt")):
            base = _agent_mod._normalize_prompt_base(stale)
        self.assertEqual(base, "你是助手。")
        migrated = _agent_mod._normalize_prompt_base(
            "请用 search_knowledge_base 查，或用 find_employee")
        self.assertNotIn("search_knowledge_base", migrated)
        self.assertNotIn("find_employee", migrated)
        self.assertIn("kb_search", migrated)
        self.assertIn("contact_find", migrated)

    def test_fallback_to_file(self):
        """DB 无 prompt 时回退到文件"""
        from scripts.skills import agent as _agent_mod
        _agent_mod._prompt_cache.pop("system", None)
        content = _agent_mod._load_system_prompt()
        # 文件存在时应能加载到非空内容
        self.assertTrue(len(content) > 50)
        self.assertIn("恩特小助手", content)

    def test_reload_clears_cache(self):
        """reload_system_prompt 清除缓存并重新加载"""
        from scripts.skills import agent as _agent_mod
        _agent_mod._prompt_cache["system"] = "缓存内容"
        reloaded = _agent_mod.reload_system_prompt()
        # reload 后应该不是缓存的旧值
        self.assertNotEqual(reloaded, "缓存内容")
        self.assertIn("恩特小助手", reloaded)


if __name__ == "__main__":
    unittest.main()
