"""
看板推送留档（v1.12.7）— 每次推送成功写入一条历史，可追溯调用

用户需求：每天留档、可调用历史看板。每个任务（sub_id）独立保留最近
KEEP_RECENT=30 次推送，超出自动裁剪最旧的——留档按任务边界隔离，不混。

- record()：scheduler 推送成功后调用，合并该次全部消息文本（多页/多源）
  成一条留档；写入后裁剪该任务超出 30 条的旧档。
- list_for_sub() / latest()：历史查询意图回放用（看上次的看板）。

连接复用 user_store.db（dashboard_push_history 新表），模式照抄
subscription_store：threading.local + RLock + WAL。
"""

import json
import logging
import os
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

logger = logging.getLogger("dashboard.push_history")

# 复用 user_store.db（与订阅同库）
from user_store import DB_PATH as _DEFAULT_DB_PATH  # noqa: E402

# 每任务保留的最近推送次数（用户拍板：默认最近 30 次，按任务 30 次）
KEEP_RECENT = 30

# 多页/多源消息合并分隔（钉钉渲染安全：纯文本分隔线）
_MSG_SEP = "\n\n──────────\n\n"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _dumps(v) -> str:
    try:
        return json.dumps(v, ensure_ascii=False)
    except Exception:
        return json.dumps([])


def _loads_list(raw) -> list:
    try:
        v = json.loads(raw) if raw else []
        return v if isinstance(v, list) else []
    except Exception:
        return []


def join_messages(messages: list) -> str:
    """多条消息（语义分页/每源一条）合并成一条留档文本，去空行归一。"""
    parts = [m.strip() for m in (messages or []) if m and m.strip()]
    return _MSG_SEP.join(parts)


@dataclass
class PushHistory:
    """单次推送留档（字段对应 dashboard_push_history 表）"""
    id: int = 0
    sub_id: int = 0
    owner_user_id: str = ""
    pushed_at: str = ""
    title: str = ""
    content: str = ""
    source_keys: list = field(default_factory=list)
    message_count: int = 0


class PushHistoryStore:
    """留档存储（threading.local + RLock + WAL，同 subscription_store 模式）"""

    def __init__(self, db_path: str = _DEFAULT_DB_PATH,
                 keep_recent: int = KEEP_RECENT):
        self._db_path = db_path
        self._keep = int(keep_recent) or KEEP_RECENT
        self._local = threading.local()
        self._lock = threading.RLock()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
            conn = sqlite3.connect(self._db_path, check_same_thread=False, timeout=5)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn
        return self._local.conn

    def close(self):
        if hasattr(self._local, "conn") and self._local.conn is not None:
            try:
                self._local.conn.close()
            except Exception:
                pass
            self._local.conn = None

    def _init_db(self):
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS dashboard_push_history (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                sub_id         INTEGER NOT NULL,
                owner_user_id  TEXT DEFAULT '',
                pushed_at      TEXT DEFAULT '',
                title          TEXT DEFAULT '',
                content        TEXT DEFAULT '',
                source_keys    TEXT DEFAULT '[]',
                message_count  INTEGER DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_dash_history_sub
                ON dashboard_push_history(sub_id, id);
        """)
        conn.commit()

    @staticmethod
    def _row_to_h(row) -> PushHistory:
        return PushHistory(
            id=row["id"],
            sub_id=row["sub_id"],
            owner_user_id=row["owner_user_id"] or "",
            pushed_at=row["pushed_at"] or "",
            title=row["title"] or "",
            content=row["content"] or "",
            source_keys=_loads_list(row["source_keys"]),
            message_count=row["message_count"],
        )

    def record(self, sub_id: int, owner_user_id: str, title: str,
               messages: list) -> PushHistory:
        """记录一次推送留档；随后裁剪该任务超出 KEEP_RECENT 的旧档。

        messages 为该次全部发送消息（含语义分页/每源一条/关键源展开），
        合并成一条 content 便于「看上次的看板」整体回放。
        """
        content = join_messages(messages)
        source_keys = []
        now = _now()
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute(
                """INSERT INTO dashboard_push_history
                   (sub_id, owner_user_id, pushed_at, title, content,
                    source_keys, message_count)
                   VALUES (?,?,?,?,?,?,?)""",
                (sub_id, owner_user_id or "", now, title or "",
                 content, _dumps(source_keys), int(len(messages) or 0)),
            )
            hist_id = cur.lastrowid
            conn.execute(
                "DELETE FROM dashboard_push_history WHERE sub_id=? AND id NOT IN ("
                "  SELECT id FROM dashboard_push_history WHERE sub_id=?"
                "  ORDER BY id DESC LIMIT ?)",
                (sub_id, sub_id, self._keep),
            )
            conn.commit()
        return PushHistory(
            id=hist_id, sub_id=sub_id, owner_user_id=owner_user_id or "",
            pushed_at=now, title=title or "", content=content,
            source_keys=source_keys, message_count=int(len(messages) or 0))

    def list_for_sub(self, sub_id: int, limit: int = 10) -> list[PushHistory]:
        """某任务最近 N 次留档（倒序：最新在前）"""
        with self._lock:
            rows = self._get_conn().execute(
                "SELECT * FROM dashboard_push_history WHERE sub_id=? "
                "ORDER BY id DESC LIMIT ?", (sub_id, int(limit))).fetchall()
        return [self._row_to_h(r) for r in rows]

    def latest(self, sub_id: int) -> Optional[PushHistory]:
        """某任务最近一次留档（没有则 None）"""
        with self._lock:
            row = self._get_conn().execute(
                "SELECT * FROM dashboard_push_history WHERE sub_id=? "
                "ORDER BY id DESC LIMIT 1", (sub_id,)).fetchone()
        return self._row_to_h(row) if row else None

    def list_for_owner(self, owner_user_id: str, limit: int = 10) -> list[PushHistory]:
        """某用户全部任务最近 N 次留档（跨任务倒序，历史查询用）"""
        with self._lock:
            rows = self._get_conn().execute(
                "SELECT * FROM dashboard_push_history WHERE owner_user_id=? "
                "ORDER BY id DESC LIMIT ?", (owner_user_id, int(limit))).fetchall()
        return [self._row_to_h(r) for r in rows]

    def count(self, sub_id: int) -> int:
        with self._lock:
            row = self._get_conn().execute(
                "SELECT COUNT(*) AS n FROM dashboard_push_history WHERE sub_id=?",
                (sub_id,)).fetchone()
        return int(row["n"]) if row else 0


# 全局单例（同 get_subscription_store 惯例）
_store: Optional[PushHistoryStore] = None


def get_push_history_store() -> PushHistoryStore:
    global _store
    if _store is None:
        _store = PushHistoryStore()
    return _store
