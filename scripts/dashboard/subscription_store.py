"""
看板订阅持久化（v1.11.0）

SQLite 存订阅，复用 data/user_store.db 的 dashboard_subscriptions 新表。
连接模式照抄 user_store.SqliteStore：threading.local + RLock + WAL。

关键设计：订阅同时存 owner_staff_id（推送 batchSend 用的 userId）与
owner_union_id（定时读取文档的 operatorId）——定时任务无对话上下文，
只能靠订阅表里存的 identity 干活。
"""

import json
import logging
import os
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

logger = logging.getLogger("dashboard.subscription")

# 复用 user_store.db（同一 data 目录）
from user_store import DB_PATH as _DEFAULT_DB_PATH  # noqa: E402


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


def _loads_json(raw):
    if raw is None or raw == "":
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


@dataclass
class Subscription:
    """单条看板订阅（字段对应 dashboard_subscriptions 表）"""
    id: int = 0
    owner_user_id: str = ""          # 订阅人（钉钉 Stream sender_id / unionId）
    owner_staff_id: str = ""         # 订阅人 staff_id（batchSend userId）
    owner_union_id: str = ""         # 订阅人 unionId（读文档 operatorId）
    data_sources: list = field(default_factory=list)   # 数据源 key 列表
    push_hour: int = 9
    push_minute: int = 0
    weekdays: str = ""               # ""=每天；"1,5"=周一、周五（0-6）
    alert_mode: str = "changes_only" # always | changes_only | off
    recipients: list = field(default_factory=list)     # batchSend staff_id，含 owner
    title: str = "恩特能源每日项目看板"
    last_snapshot: Optional[list] = None   # alerts.make_snapshot 输出
    last_pushed_at: str = ""
    enabled: bool = True
    created_at: str = ""
    updated_at: str = ""


class SubscriptionStore:
    """订阅存储（threading.local + RLock + WAL，同 user_store 模式）"""

    def __init__(self, db_path: str = _DEFAULT_DB_PATH):
        self._db_path = db_path
        self._local = threading.local()
        self._lock = threading.RLock()
        self._init_db()

    # ── 连接管理 ──────────────────────────────
    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
            conn = sqlite3.connect(self._db_path, check_same_thread=False, timeout=5)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn
        return self._local.conn

    # ── 初始化 ──────────────────────────────
    def close(self):
        """关闭当前线程连接（测试清理/进程退出用）"""
        if hasattr(self._local, "conn") and self._local.conn is not None:
            try:
                self._local.conn.close()
            except Exception:
                pass
            self._local.conn = None

    def _init_db(self):
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS dashboard_subscriptions (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_user_id  TEXT NOT NULL,
                owner_staff_id TEXT DEFAULT '',
                owner_union_id TEXT DEFAULT '',
                data_sources   TEXT DEFAULT '[]',
                push_hour      INTEGER DEFAULT 9,
                push_minute    INTEGER DEFAULT 0,
                weekdays       TEXT DEFAULT '',
                alert_mode     TEXT DEFAULT 'changes_only',
                recipients     TEXT DEFAULT '[]',
                title          TEXT DEFAULT '恩特能源每日项目看板',
                last_snapshot  TEXT DEFAULT 'null',
                last_pushed_at TEXT DEFAULT '',
                enabled        INTEGER DEFAULT 1,
                created_at     TEXT DEFAULT '',
                updated_at     TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_dashboard_sub_owner
                ON dashboard_subscriptions(owner_user_id);
            CREATE INDEX IF NOT EXISTS idx_dashboard_sub_enabled
                ON dashboard_subscriptions(enabled);
        """)
        conn.commit()

    # ── Row → Subscription ──────────────────────────────
    def _row_to_sub(self, row) -> Subscription:
        return Subscription(
            id=row["id"],
            owner_user_id=row["owner_user_id"],
            owner_staff_id=row["owner_staff_id"],
            owner_union_id=row["owner_union_id"],
            data_sources=_loads_list(row["data_sources"]),
            push_hour=row["push_hour"],
            push_minute=row["push_minute"],
            weekdays=row["weekdays"],
            alert_mode=row["alert_mode"],
            recipients=_loads_list(row["recipients"]),
            title=row["title"],
            last_snapshot=_loads_json(row["last_snapshot"]),
            last_pushed_at=row["last_pushed_at"],
            enabled=bool(row["enabled"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # ── CRUD ──────────────────────────────
    def create(self, sub: Subscription) -> int:
        now = _now()
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute(
                """INSERT INTO dashboard_subscriptions
                   (owner_user_id, owner_staff_id, owner_union_id, data_sources,
                    push_hour, push_minute, weekdays, alert_mode, recipients, title,
                    last_snapshot, last_pushed_at, enabled, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (sub.owner_user_id, sub.owner_staff_id, sub.owner_union_id,
                 _dumps(sub.data_sources), sub.push_hour, sub.push_minute,
                 sub.weekdays, sub.alert_mode, _dumps(sub.recipients), sub.title,
                 _dumps(sub.last_snapshot), sub.last_pushed_at,
                 int(sub.enabled), now, now),
            )
            conn.commit()
            return cur.lastrowid

    def get(self, sub_id: int) -> Optional[Subscription]:
        with self._lock:
            row = self._get_conn().execute(
                "SELECT * FROM dashboard_subscriptions WHERE id=?",
                (sub_id,)).fetchone()
        return self._row_to_sub(row) if row else None

    def list_all(self) -> list[Subscription]:
        with self._lock:
            rows = self._get_conn().execute(
                "SELECT * FROM dashboard_subscriptions ORDER BY id").fetchall()
        return [self._row_to_sub(r) for r in rows]

    def list_enabled(self) -> list[Subscription]:
        with self._lock:
            rows = self._get_conn().execute(
                "SELECT * FROM dashboard_subscriptions WHERE enabled=1 ORDER BY id"
            ).fetchall()
        return [self._row_to_sub(r) for r in rows]

    def list_for_owner(self, owner_user_id: str) -> list[Subscription]:
        with self._lock:
            rows = self._get_conn().execute(
                "SELECT * FROM dashboard_subscriptions WHERE owner_user_id=? ORDER BY id",
                (owner_user_id,)).fetchall()
        return [self._row_to_sub(r) for r in rows]

    def update(self, sub: Subscription):
        with self._lock:
            self._get_conn().execute(
                """UPDATE dashboard_subscriptions SET
                     owner_staff_id=?, owner_union_id=?, data_sources=?,
                     push_hour=?, push_minute=?, weekdays=?, alert_mode=?,
                     recipients=?, title=?, updated_at=?
                   WHERE id=?""",
                (sub.owner_staff_id, sub.owner_union_id, _dumps(sub.data_sources),
                 sub.push_hour, sub.push_minute, sub.weekdays, sub.alert_mode,
                 _dumps(sub.recipients), sub.title, _now(), sub.id),
            )
            self._get_conn().commit()

    def set_snapshot(self, sub_id: int, snapshot: Optional[list],
                     last_pushed_at: Optional[str] = None):
        """更新变化检测快照（推送后调用）；可一并写 last_pushed_at"""
        with self._lock:
            self._get_conn().execute(
                "UPDATE dashboard_subscriptions SET last_snapshot=?, "
                "last_pushed_at=COALESCE(?, last_pushed_at), updated_at=? WHERE id=?",
                (_dumps(snapshot), last_pushed_at, _now(), sub_id),
            )
            self._get_conn().commit()

    def set_enabled(self, sub_id: int, enabled: bool):
        with self._lock:
            self._get_conn().execute(
                "UPDATE dashboard_subscriptions SET enabled=?, updated_at=? WHERE id=?",
                (int(enabled), _now(), sub_id),
            )
            self._get_conn().commit()

    def delete(self, sub_id: int):
        with self._lock:
            self._get_conn().execute(
                "DELETE FROM dashboard_subscriptions WHERE id=?", (sub_id,))
            self._get_conn().commit()


# 全局单例（同 get_doc_client 惯例）
_store: Optional[SubscriptionStore] = None


def get_subscription_store() -> SubscriptionStore:
    global _store
    if _store is None:
        _store = SubscriptionStore()
    return _store
