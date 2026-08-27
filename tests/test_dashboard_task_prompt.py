"""看板任务提示词快照与透明度测试。"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]

from scripts.dashboard.subscription_store import Subscription, SubscriptionStore  # noqa: E402
from scripts.dashboard.task_prompt import (build_prompt, prompt_file_path,  # noqa: E402
                                   set_custom_prompt, sync_prompt_file)
from scripts.dashboard.subscription_commands import (parse_edit_task_prompt,  # noqa: E402
                                             parse_subscription_command)


class DashboardTaskPromptTests(unittest.TestCase):
    def test_prompt_is_stable_and_contains_explicit_ephemeral_boundary(self):
        sub = SimpleNamespace(title="晨报", data_sources=["doc_1"])
        template = SimpleNamespace(
            key="daily", name="每日简报", title="",
            map_instructions="只看变化",
            reduce_instructions="先结论后证据",
            section_spec=[{"kind": "headline", "title": "今日要点"}],
        )
        first = build_prompt(sub, template)
        second = build_prompt(sub, template)
        self.assertEqual(first[0], second[0])
        self.assertEqual(first[2], second[2])
        self.assertIn("本次执行完成后丢弃临时上下文", first[0])
        self.assertIn("只看变化", first[0])

    def test_subscription_migration_and_round_trip_keep_snapshot(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            store = SubscriptionStore(db_path=path)
            sub = Subscription(
                owner_user_id="u1", data_sources=["doc_1"],
                task_prompt="固定任务指令", task_prompt_spec={"key": "daily"},
                task_prompt_version="task-prompt-v1", task_prompt_hash="abc123",
                task_prompt_created_at="2026-08-14 09:00:00")
            sub_id = store.create(sub)
            loaded = store.get(sub_id)
            self.assertEqual("固定任务指令", loaded.task_prompt)
            self.assertEqual({"key": "daily"}, loaded.task_prompt_spec)
            self.assertEqual("abc123", loaded.task_prompt_hash)
            store.close()
        finally:
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.remove(path + suffix)
                except OSError:
                    pass

    def test_task_prompt_is_saved_under_its_owner_folder(self):
        import scripts.dashboard.task_prompt as tp
        with tempfile.TemporaryDirectory() as tmp:
            sub = Subscription(
                id=7, owner_user_id="u_测试/../../x", title="晨报",
                data_sources=["doc_1"], task_prompt="这是一段可核对的固定任务提示词。",
                task_prompt_spec={"key": "daily"}, task_prompt_hash="hash123")
            old_dir = tp._TASK_PROMPT_DIR
            tp._TASK_PROMPT_DIR = tmp
            try:
                path = sync_prompt_file(sub)
                self.assertTrue(path.startswith(tmp))
                self.assertTrue(os.path.exists(path))
                self.assertNotIn("..", os.path.relpath(path, tmp))
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                self.assertEqual(7, data["task_id"])
                self.assertEqual(sub.task_prompt, data["task_prompt"])
            finally:
                tp._TASK_PROMPT_DIR = old_dir

    def test_custom_prompt_is_versioned(self):
        sub = Subscription(id=1, owner_user_id="u1", task_prompt_spec={"key": "daily"})
        set_custom_prompt(sub, "先写唯一最重要的风险，再列出需要谁协调以及为什么。")
        self.assertEqual("task-prompt-user-v1", sub.task_prompt_version)
        self.assertEqual(16, len(sub.task_prompt_hash))
        self.assertIn("最重要的风险", sub.task_prompt)

    def test_task_prompt_file_retries_transient_windows_file_lock(self):
        import scripts.dashboard.task_prompt as tp
        sub = Subscription(id=8, owner_user_id="u1", task_prompt="固定提示词")
        real_replace = tp.os.replace
        with tempfile.TemporaryDirectory() as tmp:
            old_dir = tp._TASK_PROMPT_DIR
            tp._TASK_PROMPT_DIR = tmp
            try:
                attempts = 0

                def flaky_replace(src, dst):
                    nonlocal attempts
                    attempts += 1
                    if attempts == 1:
                        raise PermissionError("locked")
                    return real_replace(src, dst)

                with mock.patch("scripts.dashboard.task_prompt.os.replace",
                                side_effect=flaky_replace), \
                     mock.patch("scripts.dashboard.task_prompt.time.sleep") as m_sleep:
                    path = sync_prompt_file(sub)
                self.assertTrue(os.path.exists(path))
                m_sleep.assert_called_once_with(0.05)
            finally:
                tp._TASK_PROMPT_DIR = old_dir

    def test_edit_prompt_command_requires_explicit_content_and_keeps_task_id(self):
        text = ("编辑编号 7 的看板任务提示词改成："
                "先写唯一最重要的风险，再列出负责人、需要协调的人和截止时间。")
        parsed = parse_edit_task_prompt(text)
        self.assertEqual("edit_task_prompt", parsed["intent"])
        self.assertEqual(7, parsed["task_id"])
        self.assertIn("唯一最重要的风险", parsed["task_prompt"])
        # 管理命令解析也应进入同一条需确认的写入意图，而不是误判为模板编辑。
        self.assertEqual("edit_task_prompt",
                         parse_subscription_command(text)["intent"])

    def test_suffix_style_prompt_edit_uses_the_requirement_before_the_action(self):
        text = ("帮忙分析之后，给出几条精炼的①结论和②需要关注的问题，"
                "③需要协调的事情：按照这个要求重写提示词")
        parsed = parse_edit_task_prompt(text)
        self.assertEqual("edit_task_prompt", parsed["intent"])
        self.assertIn("需要协调的事情", parsed["task_prompt"])
        # 不能被误判成查看提示词，也不能丢失前半段需求。
        from scripts.dashboard.subscription_commands import parse_prompt_intent
        self.assertIsNone(parse_prompt_intent(text))

    def test_request_only_prompt_edit_opens_dashboard_editor_instead_of_agent(self):
        parsed = parse_edit_task_prompt("我要修改提示词")
        self.assertEqual("edit_task_prompt", parsed["intent"])
        self.assertEqual("", parsed["task_prompt"])

    # ===== v1.13.3：任务编号 = 动态位置序号（删除任务后自动重排） =====
    def test_edit_prompt_position_number_forms(self):
        """「第 N 个任务/订阅」→ task_id 取位置序号，兼容旧「任务/编号 N」"""
        cases = [
            ("编辑第 3 个任务提示词改成：先写唯一最重要的风险，再列出需要协调的人。", 3),
            ("编辑第2个看板订阅的提示词改成：先写唯一最重要的风险，再列出需要协调的人。", 2),
            ("编辑第 1 个提示词改成：先写唯一最重要的风险，再列出需要协调的人。", 1),
        ]
        for text, expected in cases:
            parsed = parse_edit_task_prompt(text)
            self.assertEqual("edit_task_prompt", parsed["intent"], text)
            self.assertEqual(expected, parsed["task_id"], text)
            self.assertIn("唯一最重要的风险", parsed["task_prompt"], text)

    def test_edit_prompt_legacy_number_form_keeps_extracting(self):
        """旧「编辑编号 7 的…」写法仍提取 7（解析层兼容，位置语义由技能层落地）"""
        text = ("编辑编号 7 的看板任务提示词改成："
                "先写唯一最重要的风险，再列出负责人、需要协调的人和截止时间。")
        parsed = parse_edit_task_prompt(text)
        self.assertEqual(7, parsed["task_id"])


if __name__ == "__main__":
    unittest.main()
