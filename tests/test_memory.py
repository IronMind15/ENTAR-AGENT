"""双层会话记忆测试（短期窗口 + 长期记忆）

验证：
1. 短期窗口：add 去重、get_context 裁剪、compressed 排除
2. session_id 时间间隔打标（沿用 / 超时新会话）
3. 长期记忆：save/get、facts 前置、fact 精确去重、上限淘汰（先删 summary）
4. 压缩链路：_parse_llm_json 各形态、compress_user_history（mock LLM）、
   无批次不调 LLM、LLM 失败标记已压缩
5. 调度：总开关关闭不调度、防抖、达阈值触发异步任务
6. 注入格式：format_long_term
7. backfill 旧数据迁移（保留窗口、标记其余已压缩）
"""

import os
import sys
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


class MemoryTestBase(unittest.TestCase):
    """每个用例独立的临时 SQLite 库，并替换 user_store 全局单例"""

    def setUp(self):
        import user_store
        from user_store import SQLiteUserStore
        self._tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self._tmp, "test.db")
        self.store = SQLiteUserStore(db_path=self.db_path)
        # 让 memory.py / memory_compress.py 的 get_store() 指向本用例临时库
        user_store._store = self.store

    def tearDown(self):
        import user_store
        user_store._store = None
        try:
            self.store._close_conn()
        except Exception:
            pass


class TestShortWindow(MemoryTestBase):
    def test_add_dedup(self):
        from skills import memory
        memory.add("u1", "user", "重复内容")
        memory.add("u1", "user", "重复内容")  # 连续同 role 同内容去重
        memory.add("u1", "assistant", "回答")
        ctx = self.store.get_context("u1", 5)
        self.assertEqual(len(ctx), 2)

    def test_short_window_trim(self):
        for i in range(30):
            self.store.add_memory("u1", "user", f"q{i}")
            self.store.add_memory("u1", "assistant", f"a{i}")
        ctx = self.store.get_context("u1", 5)
        self.assertEqual(len(ctx), 10)  # 5 轮 = 10 条
        self.assertEqual(ctx[0]["content"], "q25")  # 正序，只含最近窗口

    def test_compressed_excluded(self):
        self.store.add_memory("u1", "user", "旧消息")
        self.store.add_memory("u1", "assistant", "旧回答")
        self.store.add_memory("u1", "user", "新消息")
        self.store.add_memory("u1", "assistant", "新回答")
        batch = self.store.get_compress_batch("u1", 2)
        self.assertEqual(len(batch), 2)
        self.store.mark_compressed([b["id"] for b in batch])
        ctx = self.store.get_context("u1", 5)
        self.assertEqual([m["content"] for m in ctx], ["新消息", "新回答"])
        self.assertEqual(self.store.count_uncompressed("u1"), 2)


class TestSessionId(MemoryTestBase):
    def test_session_id_assignment(self):
        self.store.add_memory("u1", "user", "a")
        self.store.add_memory("u1", "assistant", "b")
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            "SELECT session_id FROM conversations ORDER BY id"
        ).fetchall()
        conn.close()
        self.assertTrue(rows[0][0])
        self.assertEqual(rows[0][0], rows[1][0])

    def test_resolve_session_id_new(self):
        # 无历史 → 生成新会话 id
        sid = self.store._resolve_session_id("u1")
        self.assertTrue(sid)

    def test_resolve_session_id_continues(self):
        self.store.add_memory("u1", "user", "a")
        sid_old = self.store._resolve_session_id("u1")
        conn = sqlite3.connect(self.db_path)
        sid_db = conn.execute(
            "SELECT session_id FROM conversations ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(sid_old, sid_db)

    def test_session_timeout_generates_new(self):
        import datetime as _dt
        import user_store
        self.store.add_memory("u1", "user", "a")
        self.store.add_memory("u1", "assistant", "b")
        conn = sqlite3.connect(self.db_path)
        # 把最后一条消息时间改成 120 分钟前（模拟上个会话已超时）
        conn.execute(
            "UPDATE conversations SET created_at = "
            "datetime('now','-120 minutes','localtime') "
            "WHERE id = (SELECT MAX(id) FROM conversations)"
        )
        conn.commit()
        conn.close()
        # 用「当前时间 + 5 秒」而非硬编码日期：created_at 用 SQLite 真实当前时间
        # 减 120 分钟生成，硬编码日期在每天较晚时段会导致超时判定不成立（时间依赖 bug）。
        # +5s 同时保证与 add_memory 生成旧 id 的时刻错开，避免秒级碰撞。
        fake_dt = mock.MagicMock()
        fake_dt.now.return_value = _dt.datetime.now() + _dt.timedelta(seconds=5)
        fake_dt.strptime = user_store.datetime.strptime
        with mock.patch.object(user_store, "datetime", fake_dt):
            sid_new = self.store._resolve_session_id("u1")
        conn = sqlite3.connect(self.db_path)
        sid_old = conn.execute(
            "SELECT session_id FROM conversations ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(len(sid_new), 14)  # YYYYMMDDHHMMSS 格式
        self.assertTrue(sid_new.isdigit())
        self.assertNotEqual(sid_new, sid_old)  # 超时 → 新会话 id


class TestLongTerm(MemoryTestBase):
    def test_save_read_facts_first(self):
        self.store.save_long_term("u1", "fact", "负责储能PCS项目")
        self.store.save_long_term("u1", "fact", "使用型号B")
        self.store.save_long_term("u1", "summary", "咨询过d4-1故障")
        items = self.store.get_long_term("u1", 5)
        self.assertEqual([i["mem_type"] for i in items], ["fact", "fact", "summary"])
        self.assertEqual(items[0]["content"], "使用型号B")  # 同类型按时间倒序

    def test_fact_dedup(self):
        self.store.save_long_term("u1", "fact", "同一条事实")
        self.store.save_long_term("u1", "fact", "同一条事实")
        stats = self.store.get_long_term_stats("u1")
        self.assertEqual(stats["facts"], 1)
        self.store.save_long_term("u1", "summary", "摘要A")
        self.store.save_long_term("u1", "summary", "摘要B")
        stats2 = self.store.get_long_term_stats("u1")
        self.assertEqual(stats2["summaries"], 2)

    def test_eviction_summary_first(self):
        import config
        old_limit = config.LONG_TERM_MAX_PER_USER
        config.LONG_TERM_MAX_PER_USER = 6  # 临时调小触发淘汰
        try:
            for i in range(5):
                self.store.save_long_term("u1", "summary", f"摘要{i}")
            for i in range(5):
                self.store.save_long_term("u1", "fact", f"事实{i}")
            stats = self.store.get_long_term_stats("u1")
            self.assertEqual(stats["total"], 6)
            # 淘汰先删 summary：facts 优先保留
            self.assertEqual(stats["facts"], 5)
            self.assertEqual(stats["summaries"], 1)
        finally:
            config.LONG_TERM_MAX_PER_USER = old_limit


class TestParseJson(MemoryTestBase):
    def test_plain_json(self):
        from skills import memory_compress as mc
        r = mc._parse_llm_json('{"facts": ["a"], "summary": "s"}')
        self.assertEqual(r, {"facts": ["a"], "summary": "s"})

    def test_fenced_json(self):
        from skills import memory_compress as mc
        r = mc._parse_llm_json('```json\n{"facts": ["a", "b"], "summary": "s"}\n```')
        self.assertEqual(r["facts"], ["a", "b"])
        self.assertEqual(r["summary"], "s")

    def test_no_json(self):
        from skills import memory_compress as mc
        self.assertEqual(mc._parse_llm_json("无 JSON 内容"), {"facts": [], "summary": ""})
        self.assertEqual(mc._parse_llm_json(None), {"facts": [], "summary": ""})
        self.assertEqual(mc._parse_llm_json(""), {"facts": [], "summary": ""})

    def test_missing_fields(self):
        from skills import memory_compress as mc
        r = mc._parse_llm_json('{"facts": ["a"]}')
        self.assertEqual(r["summary"], "")
        r2 = mc._parse_llm_json('{"summary": "s"}')
        self.assertEqual(r2["facts"], [])


class TestCompressFlow(MemoryTestBase):
    def test_compress_mocked(self):
        from skills import memory_compress as mc
        for i in range(5):
            self.store.add_memory("u2", "user", f"问题{i}")
            self.store.add_memory("u2", "assistant", f"回答{i}")
        with mock.patch(
            "skills.memory_compress._call_llm",
            return_value='{"facts": ["负责项目A", "型号B"], "summary": "问了5轮"}',
        ) as m:
            res = mc.compress_user_history("u2")
        self.assertEqual(res["compressed"], 10)
        self.assertEqual(res["facts"], 2)
        self.assertEqual(self.store.count_uncompressed("u2"), 0)
        items = self.store.get_long_term("u2")
        self.assertEqual(len(items), 3)  # 2 facts + 1 summary
        m.assert_called_once()

    def test_compress_no_batch(self):
        from skills import memory_compress as mc
        with mock.patch("skills.memory_compress._call_llm") as m:
            res = mc.compress_user_history("nobody")
        self.assertEqual(res["compressed"], 0)
        m.assert_not_called()

    def test_compress_llm_failure_marks_compressed(self):
        from skills import memory_compress as mc
        self.store.add_memory("u2", "user", "q")
        self.store.add_memory("u2", "assistant", "a")
        with mock.patch("skills.memory_compress._call_llm", return_value=None):
            res = mc.compress_user_history("u2")
        # LLM 无有效输出仍标记已压缩，避免同一批反复调 LLM 卡住
        self.assertEqual(res["compressed"], 2)
        self.assertEqual(self.store.count_uncompressed("u2"), 0)
        self.assertEqual(self.store.get_long_term_stats("u2")["total"], 0)


class TestSchedule(MemoryTestBase):
    def test_schedule_disabled(self):
        import config
        from skills import memory
        old = config.LONG_TERM_MEMORY_ENABLED
        config.LONG_TERM_MEMORY_ENABLED = False
        try:
            with mock.patch("skills.memory_compress.add_pending") as ap:
                memory._maybe_schedule_compress("u1")
                ap.assert_not_called()
        finally:
            config.LONG_TERM_MEMORY_ENABLED = old

    def test_schedule_pending_dedup(self):
        from skills import memory
        from skills import memory_compress as mc
        with mock.patch.object(mc, "is_pending", return_value=True), \
                mock.patch("doc_mgr.task_manager.get_manager") as gm:
            memory._maybe_schedule_compress("u1")
            gm.assert_not_called()

    def test_schedule_trigger(self):
        import config
        from skills import memory
        old_rounds = config.MAX_SESSION_ROUNDS
        config.MAX_SESSION_ROUNDS = 1  # 阈值 = 2 条消息
        try:
            for i in range(3):
                self.store.add_memory("u1", "user", f"q{i}")
            with mock.patch(
                "skills.memory_compress.add_pending", return_value=True
            ) as ap, mock.patch("doc_mgr.task_manager.get_manager") as gm:
                memory._maybe_schedule_compress("u1")
                ap.assert_called_once_with("u1")
                gm.return_value.run_async.assert_called_once()
        finally:
            config.MAX_SESSION_ROUNDS = old_rounds


class TestFormatLongTerm(MemoryTestBase):
    def test_format(self):
        from skills import memory
        self.store.save_long_term("u1", "fact", "负责项目A")
        self.store.save_long_term("u1", "summary", "咨询过故障")
        out = memory.format_long_term("u1")
        self.assertIn("[事实] 负责项目A", out)
        self.assertIn("[摘要] 咨询过故障", out)
        self.assertLess(out.index("[事实]"), out.index("[摘要]"))  # facts 前置

    def test_format_empty(self):
        from skills import memory
        self.assertEqual(memory.format_long_term("nobody"), "")


class TestBackfill(MemoryTestBase):
    def test_backfill_old_rows(self):
        from user_store import SQLiteUserStore
        db = os.path.join(self._tmp, "old.db")
        conn = sqlite3.connect(db)
        conn.executescript("""
            CREATE TABLE users (
                user_id TEXT PRIMARY KEY, nick TEXT DEFAULT '',
                staff_id TEXT DEFAULT '', corp_id TEXT DEFAULT '',
                center TEXT DEFAULT 'public'
            );
            CREATE TABLE conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL, role TEXT NOT NULL,
                content TEXT NOT NULL, session_id TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now','localtime'))
            );
            CREATE TABLE permissions (
                user_id TEXT PRIMARY KEY, can_upload INTEGER DEFAULT 0,
                can_delete INTEGER DEFAULT 0, can_manage INTEGER DEFAULT 0,
                updated_at TEXT
            );
        """)
        for i in range(20):
            conn.execute(
                "INSERT INTO conversations (user_id, role, content) "
                "VALUES ('old', 'user', ?)", (f"旧消息{i}",)
            )
        conn.commit()
        conn.close()

        # 初始化触发迁移 + backfill
        SQLiteUserStore(db_path=db)

        c = sqlite3.connect(db)
        c.row_factory = sqlite3.Row
        keep = c.execute(
            "SELECT COUNT(*) FROM conversations WHERE compressed=0"
        ).fetchone()[0]
        done = c.execute(
            "SELECT COUNT(*) FROM conversations WHERE compressed=1"
        ).fetchone()[0]
        has_table = c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='long_term_memories'"
        ).fetchone()
        c.close()
        self.assertEqual(keep, 16)  # 窗口保留
        self.assertEqual(done, 4)   # 其余标记已压缩
        self.assertTrue(has_table)


if __name__ == "__main__":
    unittest.main()
