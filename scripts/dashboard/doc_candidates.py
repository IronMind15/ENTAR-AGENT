"""
钉钉文档「帮我学习」候选登记（v1.11.0）

用户发钉钉在线文档链接 → bot 读取并登记候选（UNIQUE user_id + url）→
用户回「帮我学习」→ doc_learn 取未学候选 → 读取全文 → process_text 入库 → 标记 learned。

存储：data/user_store.db 的 dashboard_doc_candidates 新表（同订阅存储模式）。
"""

import json
import logging
import os
import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

logger = logging.getLogger("dashboard.doc_candidates")

from scripts.user_store import DB_PATH as _DEFAULT_DB_PATH  # noqa: E402


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class DocCandidate:
    """一条钉钉文档「帮我学习」候选 / 动态看板数据源（v1.11.0）

    node_id 即 AI表格 base_id，sheet_id 即 table_id（collector 直用）。
    enabled=1 且 kind∈{notable,workbook} 时可通过「按文档做看板」注册为订阅数据源。
    """
    id: int = 0
    user_id: str = ""
    url: str = ""
    node_id: str = ""
    sheet_id: str = ""
    kind: str = ""
    operator_union: str = ""    # 读取文档用的 unionId（钉钉 sender_id）
    records_count: int = 0
    learned: bool = False
    created_at: str = ""
    # ── 动态看板数据源字段 ──
    table_mode: str = "fixed"          # fixed | latest_week（动态源默认 fixed，用登记时 sheet_id）
    enabled: bool = True               # 可做看板源标志（notable/workbook 登记时置 1）
    name: str = ""                     # 看板板块标题（无则用 node_id 前缀）
    field_map: str = "{}"              # JSON：{field_id: {label,type,max_len}}
    status_groups: str = "{}"          # JSON：{"attention": [...], "normal": [...]}


class DocCandidateStore:
    """候选存储（threading.local + RLock + WAL，同订阅存储模式）"""

    def __init__(self, db_path: str = _DEFAULT_DB_PATH):
        self._db_path = db_path
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
            CREATE TABLE IF NOT EXISTS dashboard_doc_candidates (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id        TEXT NOT NULL,
                url            TEXT NOT NULL,
                node_id        TEXT DEFAULT '',
                sheet_id       TEXT DEFAULT '',
                kind           TEXT DEFAULT '',
                operator_union TEXT DEFAULT '',
                records_count  INTEGER DEFAULT 0,
                learned        INTEGER DEFAULT 0,
                created_at     TEXT DEFAULT '',
                table_mode     TEXT DEFAULT 'fixed',
                enabled        INTEGER DEFAULT 1,
                name           TEXT DEFAULT '',
                field_map      TEXT DEFAULT '{}',
                status_groups  TEXT DEFAULT '{}',
                UNIQUE(user_id, url)
            );
            CREATE INDEX IF NOT EXISTS idx_doc_cand_pending
                ON dashboard_doc_candidates(user_id, learned);
        """)
        self._ensure_columns(conn)
        conn.commit()

    def _ensure_columns(self, conn):
        """老库兼容：v1.11.0 新增列用 ALTER TABLE ADD COLUMN 补齐（幂等）"""
        try:
            cols = {r["name"] for r in conn.execute(
                "PRAGMA table_info(dashboard_doc_candidates)")}
        except Exception:
            return
        defaults = {
            "table_mode": "'fixed'",
            "enabled": "1",
            "name": "''",
            "field_map": "'{}'",
            "status_groups": "'{}'",
        }
        for col, ddl in defaults.items():
            if col not in cols:
                conn.execute(
                    f"ALTER TABLE dashboard_doc_candidates "
                    f"ADD COLUMN {col} TEXT DEFAULT {ddl}")
        conn.commit()

    def _row_to_cand(self, row) -> DocCandidate:
        return DocCandidate(
            id=row["id"], user_id=row["user_id"], url=row["url"],
            node_id=row["node_id"], sheet_id=row["sheet_id"], kind=row["kind"],
            operator_union=row["operator_union"],
            records_count=row["records_count"], learned=bool(row["learned"]),
            created_at=row["created_at"],
            table_mode=row["table_mode"] if "table_mode" in row.keys() else "fixed",
            enabled=bool(row["enabled"]) if "enabled" in row.keys() else True,
            name=row["name"] if "name" in row.keys() else "",
            field_map=row["field_map"] if "field_map" in row.keys() else "{}",
            status_groups=row["status_groups"] if "status_groups" in row.keys() else "{}",
        )

    def add(self, cand: DocCandidate) -> int:
        """登记候选（同 user+url 覆盖为新，保留未学习状态追踪）"""
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                """INSERT INTO dashboard_doc_candidates
                   (user_id, url, node_id, sheet_id, kind, operator_union,
                    records_count, learned, created_at, table_mode, enabled,
                    name, field_map, status_groups)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(user_id, url) DO UPDATE SET
                     node_id=excluded.node_id, sheet_id=excluded.sheet_id,
                     kind=excluded.kind, operator_union=excluded.operator_union,
                     records_count=excluded.records_count, created_at=excluded.created_at,
                     table_mode=excluded.table_mode, name=excluded.name,
                     field_map=excluded.field_map, status_groups=excluded.status_groups
                """,
                (cand.user_id, cand.url, cand.node_id, cand.sheet_id, cand.kind,
                 cand.operator_union, cand.records_count, int(cand.learned), _now(),
                 cand.table_mode, int(cand.enabled), cand.name,
                 cand.field_map, cand.status_groups),
            )
            conn.commit()
            row = conn.execute(
                "SELECT id FROM dashboard_doc_candidates WHERE user_id=? AND url=?",
                (cand.user_id, cand.url)).fetchone()
            return row["id"] if row else 0

    def get_pending(self, user_id: str) -> Optional[DocCandidate]:
        """取该用户最新一条未学习候选"""
        with self._lock:
            row = self._get_conn().execute(
                "SELECT * FROM dashboard_doc_candidates WHERE user_id=? AND learned=0 "
                "ORDER BY id DESC LIMIT 1", (user_id,)).fetchone()
        return self._row_to_cand(row) if row else None

    def list_pending(self, user_id: str) -> list[DocCandidate]:
        with self._lock:
            rows = self._get_conn().execute(
                "SELECT * FROM dashboard_doc_candidates WHERE user_id=? AND learned=0 "
                "ORDER BY id DESC", (user_id,)).fetchall()
        return [self._row_to_cand(r) for r in rows]

    def get(self, cand_id: int) -> Optional[DocCandidate]:
        with self._lock:
            row = self._get_conn().execute(
                "SELECT * FROM dashboard_doc_candidates WHERE id=?",
                (cand_id,)).fetchone()
        return self._row_to_cand(row) if row else None

    def mark_learned(self, cand_id: int):
        with self._lock:
            self._get_conn().execute(
                "UPDATE dashboard_doc_candidates SET learned=1 WHERE id=?",
                (cand_id,))
            self._get_conn().commit()

    def update_name(self, cand_id: int, name: str):
        """回填文件级真实标题；空标题不覆盖已有值。"""
        if not name:
            return
        with self._lock:
            self._get_conn().execute(
                "UPDATE dashboard_doc_candidates SET name=? WHERE id=?",
                (name, cand_id))
            self._get_conn().commit()

    def find_by_node_id(self, user_id: str, node_id: str) -> Optional[DocCandidate]:
        """按文档 node_id 查该用户最新候选（含已学习，供总结工具定位）"""
        with self._lock:
            row = self._get_conn().execute(
                "SELECT * FROM dashboard_doc_candidates WHERE user_id=? AND node_id=? "
                "ORDER BY id DESC LIMIT 1", (user_id, node_id)).fetchone()
        return self._row_to_cand(row) if row else None

    def find_by_url(self, user_id: str, url: str) -> Optional[DocCandidate]:
        """按文档 url 查该用户候选（先精确匹配 url，再按 node_id 兜底，容 URL 变体）"""
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT * FROM dashboard_doc_candidates WHERE user_id=? AND url=? "
                "ORDER BY id DESC LIMIT 1", (user_id, url)).fetchone()
            if row:
                return self._row_to_cand(row)
            m = re.search(r"/i/nodes/([A-Za-z0-9_-]+)", url or "")
            if m:
                row = conn.execute(
                    "SELECT * FROM dashboard_doc_candidates WHERE user_id=? AND node_id=? "
                    "ORDER BY id DESC LIMIT 1", (user_id, m.group(1))).fetchone()
                if row:
                    return self._row_to_cand(row)
        return None

    def find_by_name(self, user_id: str, name: str) -> Optional[DocCandidate]:
        """按文档名查该用户候选（LLM 可能传文档名而非 node_id/url，v1.11.3）"""
        if not name:
            return None
        with self._lock:
            row = self._get_conn().execute(
                "SELECT * FROM dashboard_doc_candidates WHERE user_id=? AND name=? "
                "ORDER BY id DESC LIMIT 1", (user_id, name)).fetchone()
        return self._row_to_cand(row) if row else None

    # ── 动态看板数据源（v1.11.0）──────────────────────────
    def list_dashboard_ready(self, user_id: str) -> list[DocCandidate]:
        """取该用户可做看板数据源的文档（kind∈notable/workbook/doc/folder 且 enabled=1）

        v1.12.6（C8）：folder 加入——文件夹做看板源每次推送解读全部子文档。
        """
        with self._lock:
            rows = self._get_conn().execute(
                "SELECT * FROM dashboard_doc_candidates "
                "WHERE user_id=? AND enabled=1 "
                "AND kind IN ('notable','workbook','doc','folder') "
                "ORDER BY id DESC", (user_id,)).fetchall()
        return [self._row_to_cand(r) for r in rows]

    def set_enabled(self, cand_id: int, enabled: bool):
        with self._lock:
            self._get_conn().execute(
                "UPDATE dashboard_doc_candidates SET enabled=? WHERE id=?",
                (int(enabled), cand_id))
            self._get_conn().commit()

    def list_all_enabled(self, user_id: str = "") -> list[DocCandidate]:
        """全部 enabled 动态源候选

        v1.11.10：query 工具按用户隔离（user_id 非空只取本人动态源，防止
        把同事的文档候选混进自己的看板）；push 跨用户全量（空=全部）。
        """
        with self._lock:
            if user_id:
                rows = self._get_conn().execute(
                    "SELECT * FROM dashboard_doc_candidates "
                    "WHERE enabled=1 AND user_id=? ORDER BY id DESC",
                    (user_id,)).fetchall()
            else:
                rows = self._get_conn().execute(
                    "SELECT * FROM dashboard_doc_candidates WHERE enabled=1 "
                    "ORDER BY id DESC").fetchall()
        return [self._row_to_cand(r) for r in rows]


_candidate_store: Optional[DocCandidateStore] = None


def get_candidate_store() -> DocCandidateStore:
    global _candidate_store
    if _candidate_store is None:
        _candidate_store = DocCandidateStore()
    return _candidate_store
