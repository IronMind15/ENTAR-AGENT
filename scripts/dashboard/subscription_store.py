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
    alert_mode: str = "always" # always（每日必推） | changes_only | off
    recipients: list = field(default_factory=list)     # batchSend staff_id，含 owner
    title: str = "恩特能源每日项目看板"
    template_id: str = "daily"           # 看板模板键（v1.12.0，输出格式）
    per_source: bool = False   # v1.12.5：每源独立总结（True=每个数据源单独一条，False=合并一份报告）
    last_snapshot: Optional[list] = None   # alerts.make_snapshot 输出
    last_pushed_at: str = ""
    enabled: bool = True
    created_at: str = ""
    updated_at: str = ""

    def fingerprint(self) -> tuple:
        """业务唯一键；忽略列表顺序和数据库生成字段。

        v1.12.0 加入 template_id：同来源同时段不同格式不视为重复订阅——
        用户可能故意同时要「每日简报」和「周报总结」两个输出。
        v1.12.5 加入 per_source：同一来源同时段「合并总结」与「每源独立总结」
        是两种输出模式，允许并存（视为不同业务配置）。
        """
        return (
            self.owner_user_id,
            tuple(sorted(str(x) for x in self.data_sources)),
            int(self.push_hour), int(self.push_minute), self.weekdays or "",
            self.alert_mode or "always",
            tuple(sorted(str(x) for x in self.recipients)),
            self.template_id or "daily",
            int(self.per_source),
        )


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
                alert_mode     TEXT DEFAULT 'always',
                recipients     TEXT DEFAULT '[]',
                title          TEXT DEFAULT '恩特能源每日项目看板',
                template_id    TEXT DEFAULT 'daily',
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
        # v1.12.0 迁移：老库补 template_id 列（默认 daily，等价于旧行为）
        self._ensure_column(conn, "dashboard_subscriptions", "template_id",
                            "TEXT DEFAULT 'daily'")
        # v1.12.5 迁移：老库补 per_source 列（默认 0=合并总结，等价于旧行为）
        self._ensure_column(conn, "dashboard_subscriptions", "per_source",
                            "INTEGER DEFAULT 0")
        conn.commit()

    @staticmethod
    def _ensure_column(conn, table: str, column: str, ddl: str):
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

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
            template_id=row["template_id"] or "daily",
            per_source=bool(row["per_source"]),
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
                    template_id, per_source, last_snapshot, last_pushed_at, enabled,
                    created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (sub.owner_user_id, sub.owner_staff_id, sub.owner_union_id,
                 _dumps(sub.data_sources), sub.push_hour, sub.push_minute,
                 sub.weekdays, sub.alert_mode, _dumps(sub.recipients), sub.title,
                 sub.template_id or "daily", int(sub.per_source),
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

    def find_exact_duplicate(self, candidate: Subscription) -> Optional[Subscription]:
        """查找与候选订阅业务配置完全相同的记录。

        v1.12.6（C6）：来源集合按实时解析的有效 key 比较（effective_source_keys）
        ——订阅不再绑定固定快照，同 owner 当前候选相同即视为同一订阅（新发布/
        删除的文档自动反映，不会因快照不同误建第二条订阅）。模板/每源独立总结/
        时间/接收人/提醒模式仍参与区分，允许故意并存的不同输出。
        """
        from dashboard.service import effective_source_keys
        wanted = set(effective_source_keys(candidate))
        for existing in self.list_for_owner(candidate.owner_user_id):
            if (int(existing.push_hour) == int(candidate.push_hour)
                    and int(existing.push_minute) == int(candidate.push_minute)
                    and (existing.weekdays or "") == (candidate.weekdays or "")
                    and (existing.alert_mode or "always")
                    == (candidate.alert_mode or "always")
                    and tuple(sorted(str(x) for x in existing.recipients))
                    == tuple(sorted(str(x) for x in candidate.recipients))
                    and (existing.template_id or "daily")
                    == (candidate.template_id or "daily")
                    and int(existing.per_source) == int(candidate.per_source)
                    and set(effective_source_keys(existing)) == wanted):
                return existing
        return None

    def find_similar(self, candidate: Subscription, threshold: float = 0.5) -> list[tuple[Subscription, float]]:
        """同一时间且来源明显重叠的订阅，用于向用户提示潜在重复。"""
        wanted = set(str(x) for x in candidate.data_sources)
        matches = []
        for existing in self.list_for_owner(candidate.owner_user_id):
            if (existing.push_hour, existing.push_minute, existing.weekdays or "") != (
                    candidate.push_hour, candidate.push_minute, candidate.weekdays or ""):
                continue
            current = set(str(x) for x in existing.data_sources)
            union = wanted | current
            score = len(wanted & current) / len(union) if union else 1.0
            if score >= threshold:
                matches.append((existing, score))
        return matches

    def update(self, sub: Subscription):
        with self._lock:
            self._get_conn().execute(
                """UPDATE dashboard_subscriptions SET
                     owner_staff_id=?, owner_union_id=?, data_sources=?,
                     push_hour=?, push_minute=?, weekdays=?, alert_mode=?,
                     recipients=?, title=?, template_id=?, per_source=?,
                     updated_at=?
                   WHERE id=?""",
                (sub.owner_staff_id, sub.owner_union_id, _dumps(sub.data_sources),
                 sub.push_hour, sub.push_minute, sub.weekdays, sub.alert_mode,
                 _dumps(sub.recipients), sub.title, sub.template_id or "daily",
                 int(sub.per_source), _now(), sub.id),
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

    def delete(self, sub_id: int) -> bool:
        with self._lock:
            cur = self._get_conn().execute(
                "DELETE FROM dashboard_subscriptions WHERE id=?", (sub_id,))
            self._get_conn().commit()
            return cur.rowcount == 1


# 全局单例（同 get_doc_client 惯例）
_store: Optional[SubscriptionStore] = None


def get_subscription_store() -> SubscriptionStore:
    global _store
    if _store is None:
        _store = SubscriptionStore()
    return _store
