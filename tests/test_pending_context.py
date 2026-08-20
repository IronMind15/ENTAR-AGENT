"""统一 pending 上下文测试（v1.12.1，M3 pending 归一）

覆盖：set/get/clear 往返、跨类型覆盖、TTL 过期、clear_type 类型守卫、
确认词类型作用域（「入库」只在 learn 生效）、并发 set。
"""

import threading
import time
import unittest

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"

from scripts.pending_context import (  # noqa: E402
    PT_CLARIFY,
    PT_KANBAN,
    PT_LEARN,
    PT_TOOL,
    clear,
    clear_type,
    get,
    has,
    is_cancel_text,
    is_confirm_text,
    reset,
    set as pc_set,
    touch,
)


class PendingContextTests(unittest.TestCase):
    def setUp(self):
        reset()

    def test_set_get_clear_roundtrip(self):
        pc_set("u1", PT_TOOL, {"tool": "dash_push", "args": {"x": 1}})
        entry = get("u1")
        self.assertEqual(entry["type"], PT_TOOL)
        self.assertEqual(entry["payload"], {"tool": "dash_push", "args": {"x": 1}})
        self.assertTrue(has("u1"))
        clear("u1")
        self.assertIsNone(get("u1"))
        self.assertFalse(has("u1"))

    def test_get_returns_copy_not_ref(self):
        pc_set("u1", PT_LEARN, {"file_path": "/x/a.pdf"})
        entry = get("u1")
        entry["payload"]["file_path"] = "改坏"
        self.assertEqual(get("u1")["payload"]["file_path"], "/x/a.pdf")

    def test_cross_type_override_logs_and_replaces(self):
        pc_set("u1", PT_LEARN, {"file_path": "/x/a.pdf"})
        pc_set("u1", PT_TOOL, {"tool": "kb_create"})
        entry = get("u1")
        self.assertEqual(entry["type"], PT_TOOL)
        self.assertEqual(entry["payload"]["tool"], "kb_create")
        # 被覆盖后 learn 清仓守卫不生效
        self.assertFalse(clear_type("u1", PT_LEARN))

    def test_ttl_expiry_lazy_clears(self):
        pc_set("u1", PT_KANBAN, {"intent": "create"}, ttl=0.1)
        self.assertIsNotNone(get("u1"))
        time.sleep(0.2)
        self.assertIsNone(get("u1"))
        # 惰性清除后表空
        self.assertIsNone(get("u1"))

    def test_touch_refreshes_expiry(self):
        pc_set("u1", PT_CLARIFY, {"options": []}, ttl=0.3)
        time.sleep(0.15)
        touch("u1")
        time.sleep(0.15)
        self.assertIsNotNone(get("u1"))  # touch 后未过期

    def test_clear_type_guard_preserves_other_type(self):
        pc_set("u1", PT_LEARN, {"file_path": "/x/a.pdf"})
        self.assertFalse(clear_type("u1", PT_TOOL))   # 类型不符不清
        self.assertEqual(get("u1")["type"], PT_LEARN)
        self.assertTrue(clear_type("u1", PT_LEARN))
        self.assertIsNone(get("u1"))

    def test_confirm_words_type_scoped(self):
        # 「入库/入库吧/要」只在 learn 类型生效
        self.assertTrue(is_confirm_text("入库", PT_LEARN))
        self.assertTrue(is_confirm_text("入库吧", PT_LEARN))
        self.assertTrue(is_confirm_text("要", PT_LEARN))
        self.assertFalse(is_confirm_text("入库", PT_TOOL))
        self.assertFalse(is_confirm_text("要", PT_KANBAN))
        # 通用确认词所有类型都生效
        for ptype in (PT_TOOL, PT_LEARN, PT_KANBAN, PT_CLARIFY):
            self.assertTrue(is_confirm_text("确认", ptype), ptype)
            self.assertTrue(is_confirm_text("好的，确认", ptype), ptype)
        # v1.12.4：自然口语确认词（「对的」落 Agent 被 LLM 谎称已开通——真实回归）
        for w in ("对的", "对呀", "对啊", "是的呀", "是呀", "好呀", "好啊", "可以的", "可以呀"):
            for ptype in (PT_TOOL, PT_LEARN, PT_KANBAN, PT_CLARIFY):
                self.assertTrue(is_confirm_text(w, ptype), (w, ptype))
        # v1.12.5：动作后缀「推送/发送」——「确认推送」落 Agent 被 LLM 反复要求
        # 再确认、推送永不执行（真实回归：dash_push 写操作确认死循环）
        for w in ("确认推送", "好，推送", "好的，推送", "执行推送", "确认发送"):
            for ptype in (PT_TOOL, PT_LEARN, PT_KANBAN, PT_CLARIFY):
                self.assertTrue(is_confirm_text(w, ptype), (w, ptype))
        # 单独动作词 / 模糊句不误判
        self.assertFalse(is_confirm_text("推送", PT_TOOL))
        self.assertFalse(is_confirm_text("发送", PT_TOOL))
        self.assertFalse(is_confirm_text("帮我推送", PT_TOOL))
        # 否定/普通句子不误判
        self.assertFalse(is_confirm_text("确认删除所有文件", PT_TOOL))
        self.assertFalse(is_confirm_text("我确认一下", PT_TOOL))
        self.assertFalse(is_confirm_text("对了", PT_KANBAN))   # 「对了」是转折不是确认

    def test_cancel_words(self):
        for w in ("取消", "算了", "不要了", "不执行", "先不弄了"):
            self.assertTrue(is_cancel_text(w), w)
        self.assertFalse(is_cancel_text("取消订阅吧"))  # 不是纯取消词
        self.assertFalse(is_cancel_text("确认"))

    def test_concurrent_set(self):
        """并发 set 不抛错，最终只有一个 pending 存活"""
        errors = []

        def worker(i):
            try:
                pc_set(f"user-{i % 5}", PT_TOOL, {"tool": f"t{i}"})
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        for uid in {f"user-{i % 5}" for i in range(50)}:
            entry = get(uid)
            self.assertEqual(entry["type"], PT_TOOL)


if __name__ == "__main__":
    unittest.main()
