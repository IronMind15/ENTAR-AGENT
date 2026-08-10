"""
同步状态追踪模块

记录每个文件同步到 Chroma 知识库的状态（pending/synced/error），
支持变化检测（通过 SHA-256 比对）、手动同步触发、历史查询。

v2: 新增 upload_user_id / upload_user_name 字段，记录文件上传者。

与 user_store.py 共用同一个 SQLite 文件（user_store.db），
不同表，WAL 模式下读写不互斥。
"""

import logging
import os
import sqlite3
import threading
import uuid
from datetime import datetime
from typing import Optional

logger = logging.getLogger("doc_mgr.sync_tracker")

# ===== 数据库路径（复用 user_store.db） =====
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.join(_SCRIPT_DIR, "..", "..")
DB_DIR = os.path.join(_PROJECT_ROOT, "data")
DB_PATH = os.path.join(DB_DIR, "user_store.db")


class SyncTracker:
    """同步状态追踪器

    线程安全（threading.local + RLock），设计同 user_store.py 模式。
    """

    def __init__(self, db_path: str = DB_PATH):
        self._db_path = db_path
        self._local = threading.local()
        self._lock = threading.RLock()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """获取当前线程的数据库连接"""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            self._local.conn = conn
        return self._local.conn

    def close(self) -> None:
        """关闭当前线程的 SQLite 连接，便于测试、维护任务和优雅退出。"""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def _init_db(self):
        """幂等建表 + 迁移旧表"""
        with self._lock:
            conn = self._get_conn()
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sync_status (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_path        TEXT NOT NULL,
                    file_name        TEXT NOT NULL,
                    file_size        INTEGER DEFAULT 0,
                    file_hash        TEXT DEFAULT '',
                    target_collection TEXT NOT NULL DEFAULT '',
                    upload_user_id   TEXT DEFAULT '',
                    upload_user_name TEXT DEFAULT '',
                    sync_status      TEXT NOT NULL DEFAULT 'pending',
                    error_message    TEXT DEFAULT '',
                    last_synced_at   TEXT,
                    created_at       TEXT DEFAULT (datetime('now','localtime')),
                    updated_at       TEXT DEFAULT (datetime('now','localtime')),
                    UNIQUE(file_path),
                    CHECK(sync_status IN ('pending','synced','error'))
                )
            """)
            # 迁移：给旧表补齐上传者和审核流程字段
            migration_columns = {
                "upload_user_id": "TEXT DEFAULT ''",
                "upload_user_name": "TEXT DEFAULT ''",
                "suggested_department": "TEXT DEFAULT 'public'",
                "review_id": "TEXT DEFAULT ''",
                "reviewer_staff_id": "TEXT DEFAULT ''",
                "review_status": "TEXT DEFAULT ''",
                "reviewed_by": "TEXT DEFAULT ''",
                "reviewed_at": "TEXT",
                "review_task_id": "TEXT DEFAULT ''",
            }
            for col, definition in migration_columns.items():
                try:
                    conn.execute(
                        f"ALTER TABLE sync_status ADD COLUMN {col} {definition}"
                    )
                    logger.info(f"[迁移] sync_status 表新增列: {col}")
                except sqlite3.OperationalError:
                    pass  # 列已存在
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_sync_status
                ON sync_status(sync_status)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_sync_collection
                ON sync_status(target_collection)
            """)
            conn.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_sync_review_id
                ON sync_status(review_id)
                WHERE review_id <> ''
            """)
            conn.commit()

    def upsert_file(self, file_path: str, file_name: str, file_size: int,
                    file_hash: str, target_collection: str = "",
                    upload_user_id: str = "", upload_user_name: str = "",
                    suggested_department: str = "") -> None:
        """插入或更新文件记录

        Args:
            file_path: 文件绝对路径
            file_name: 文件名
            file_size: 文件字节数
            file_hash: 文件内容 SHA-256
            target_collection: 目标知识库
            upload_user_id: 上传者钉钉 user_id
            upload_user_name: 上传者昵称
            suggested_department: 建议所属中心 ID。空串表示未指定：
                首次插入时落为 public，更新时保留原值（避免后续同步/记录
                路径把已指定的部门静默覆盖为 public）。
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            conn = self._get_conn()
            # 注意：INSERT 的 COALESCE 会把空串提前转成 'public'，所以
            # ON CONFLICT 分支不能依赖 excluded.suggested_department 判断
            # 「是否未指定」——必须直接引用参数（?）判断原始值。
            conn.execute("""
                INSERT INTO sync_status
                    (file_path, file_name, file_size, file_hash,
                     target_collection, upload_user_id, upload_user_name,
                     suggested_department,
                     sync_status, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, COALESCE(NULLIF(?, ''), 'public'),
                        'pending', ?)
                ON CONFLICT(file_path) DO UPDATE SET
                    file_name      = excluded.file_name,
                    file_size      = excluded.file_size,
                    file_hash      = excluded.file_hash,
                    target_collection = excluded.target_collection,
                    upload_user_id = CASE
                        WHEN excluded.upload_user_id <> ''
                        THEN excluded.upload_user_id ELSE upload_user_id END,
                    upload_user_name = CASE
                        WHEN excluded.upload_user_name <> ''
                        THEN excluded.upload_user_name ELSE upload_user_name END,
                    suggested_department = CASE
                        WHEN ? <> '' THEN ? ELSE suggested_department END,
                    updated_at     = excluded.updated_at,
                    sync_status    = CASE
                        WHEN sync_status = 'error'
                          OR file_hash <> excluded.file_hash
                          OR target_collection <> excluded.target_collection
                        THEN 'pending'
                        ELSE sync_status
                    END
            """, (file_path, file_name, file_size, file_hash,
                  target_collection, upload_user_id, upload_user_name,
                  suggested_department, now,
                  suggested_department, suggested_department))
            conn.commit()

    def update_department(self, file_path: str, department: str) -> None:
        """更新文件的建议所属中心（审核申请创建 / 后台改归属时写入）

        部门写入必须在文件登记（upsert_file）之后调用，确保审批通过后
        _submit_sync_task 能从记录读取真实部门，而非默认 public。
        允许显式写回 "public"（改归属为全公司公开时需要）。
        """
        if not department:
            return
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            conn = self._get_conn()
            conn.execute("""
                UPDATE sync_status SET
                    suggested_department = ?,
                    updated_at = ?
                WHERE file_path = ?
            """, (department, now, file_path))
            conn.commit()

    def create_review(self, file_path: str, reviewer_staff_id: str) -> str:
        """为已追踪文件创建一次审核申请，返回短申请编号。

        同一文件仍处于 pending 且审核人未变化时复用原申请，避免机器人
        回调重试造成重复推送和重复审批。
        """
        reviewer_staff_id = reviewer_staff_id.strip()
        if not reviewer_staff_id:
            raise ValueError("审核人 staff_id 不能为空")

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT review_id, reviewer_staff_id, review_status "
                "FROM sync_status WHERE file_path = ?",
                (file_path,),
            ).fetchone()
            if not row:
                raise ValueError("文件尚未登记到同步追踪器")
            if (row["review_status"] == "pending"
                    and row["reviewer_staff_id"] == reviewer_staff_id
                    and row["review_id"]):
                return str(row["review_id"])

            review_id = uuid.uuid4().hex[:8].upper()
            conn.execute("""
                UPDATE sync_status SET
                    review_id = ?,
                    reviewer_staff_id = ?,
                    review_status = 'pending',
                    reviewed_by = '',
                    reviewed_at = NULL,
                    review_task_id = '',
                    updated_at = ?
                WHERE file_path = ?
            """, (review_id, reviewer_staff_id, now, file_path))
            conn.commit()
            return review_id

    def get_review(self, review_id: str) -> Optional[dict]:
        """按申请编号查询审核记录。"""
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT * FROM sync_status WHERE review_id = ?",
                (review_id.strip().upper(),),
            ).fetchone()
            return dict(row) if row else None

    def decide_review(self, review_id: str, decision: str,
                      reviewed_by: str) -> bool:
        """原子地批准或拒绝 pending 申请；重复处理返回 False。"""
        if decision not in ("approved", "rejected"):
            raise ValueError("无效审核结果")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute("""
                UPDATE sync_status SET
                    review_status = ?,
                    reviewed_by = ?,
                    reviewed_at = ?,
                    updated_at = ?
                WHERE review_id = ? AND review_status = 'pending'
            """, (
                decision, reviewed_by, now, now,
                review_id.strip().upper(),
            ))
            conn.commit()
            return cur.rowcount == 1

    def set_review_task(self, review_id: str, task_id: str) -> None:
        """记录批准后启动的后台同步任务编号。"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            conn = self._get_conn()
            conn.execute("""
                UPDATE sync_status SET review_task_id = ?, updated_at = ?
                WHERE review_id = ? AND review_status = 'approved'
            """, (task_id, now, review_id.strip().upper()))
            conn.commit()

    def reset_review_pending(self, review_id: str) -> None:
        """后台任务提交失败时撤销决定，允许审核人重试。"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            conn = self._get_conn()
            conn.execute("""
                UPDATE sync_status SET
                    review_status = 'pending',
                    reviewed_by = '',
                    reviewed_at = NULL,
                    review_task_id = '',
                    updated_at = ?
                WHERE review_id = ? AND review_status = 'approved'
                  AND review_task_id = ''
            """, (now, review_id.strip().upper()))
            conn.commit()

    def update_collection(self, file_path: str, collection: str) -> None:
        """更新文件的目标 collection（管理员在后台选择）"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            conn = self._get_conn()
            conn.execute("""
                UPDATE sync_status SET
                    target_collection = ?,
                    updated_at = ?
                WHERE file_path = ?
            """, (collection, now, file_path))
            conn.commit()

    def mark_synced(self, file_path: str) -> None:
        """将文件标记为已同步"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            conn = self._get_conn()
            conn.execute("""
                UPDATE sync_status SET
                    sync_status = 'synced',
                    error_message = '',
                    last_synced_at = ?,
                    updated_at = ?
                WHERE file_path = ?
            """, (now, now, file_path))
            conn.commit()

    def mark_error(self, file_path: str, error_message: str) -> None:
        """将文件标记为失败"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            conn = self._get_conn()
            conn.execute("""
                UPDATE sync_status SET
                    sync_status = 'error',
                    error_message = ?,
                    updated_at = ?
                WHERE file_path = ?
            """, (error_message[:500], now, file_path))
            conn.commit()

    def mark_pending(self, file_path: str) -> None:
        """重置为待处理"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            conn = self._get_conn()
            conn.execute("""
                UPDATE sync_status SET
                    sync_status = 'pending',
                    error_message = '',
                    updated_at = ?
                WHERE file_path = ?
            """, (now, file_path))
            conn.commit()

    def get_status(self, file_path: str) -> Optional[dict]:
        """查询单个文件状态"""
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute(
                "SELECT * FROM sync_status WHERE file_path = ?",
                (file_path,)
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def get_files_by_user(self, upload_user_id: str) -> list[dict]:
        """查询某用户上传过的所有文件及其状态，按 updated_at 倒序。

        v1.10.2 新增：供钉钉「我的文件」查看自己上传文件（姓名/状态/建议）。
        """
        if not upload_user_id:
            return []
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute(
                "SELECT * FROM sync_status WHERE upload_user_id = ? "
                "ORDER BY updated_at DESC",
                (upload_user_id,),
            )
            return [dict(row) for row in cur.fetchall()]

    def delete_file(self, file_path: str) -> bool:
        """删除文件追踪记录（封装 router 里的裸 SQL，供钉钉删除命令复用）。

        Returns:
            True 表示确实删除了一条记录；记录不存在返回 False。
        """
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute(
                "DELETE FROM sync_status WHERE file_path = ?", (file_path,)
            )
            conn.commit()
            return cur.rowcount > 0

    def list_all(self, status_filter: str = "",
                 collection_filter: str = "") -> list[dict]:
        """列出所有文件及其状态，可选按状态/collection 过滤"""
        with self._lock:
            conn = self._get_conn()
            where = []
            params = []
            if status_filter:
                where.append("sync_status = ?")
                params.append(status_filter)
            if collection_filter:
                where.append("target_collection = ?")
                params.append(collection_filter)
            sql = "SELECT * FROM sync_status"
            if where:
                sql += " WHERE " + " AND ".join(where)
            sql += " ORDER BY updated_at DESC"
            cur = conn.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]

    def get_pending_files(self) -> list[dict]:
        """获取所有待处理文件（status='pending' 或 'error'）

        管理员后台用，按文件路径倒序（最新上传的先显示）。
        """
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute("""
                SELECT * FROM sync_status
                WHERE sync_status IN ('pending', 'error')
                ORDER BY updated_at DESC
            """)
            return [dict(row) for row in cur.fetchall()]

    def get_stats(self) -> dict:
        """获取统计：总数/已同步/待处理/失败"""
        with self._lock:
            conn = self._get_conn()
            total = conn.execute(
                "SELECT COUNT(*) FROM sync_status"
            ).fetchone()[0]
            synced = conn.execute(
                "SELECT COUNT(*) FROM sync_status WHERE sync_status='synced'"
            ).fetchone()[0]
            pending = conn.execute(
                "SELECT COUNT(*) FROM sync_status WHERE sync_status='pending'"
            ).fetchone()[0]
            error = conn.execute(
                "SELECT COUNT(*) FROM sync_status WHERE sync_status='error'"
            ).fetchone()[0]
            return {
                "total": total,
                "synced": synced,
                "pending": pending,
                "error": error,
            }

    def get_history(self, limit: int = 50) -> list[dict]:
        """获取最近同步记录（按 updated_at 倒序）"""
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute(
                "SELECT * FROM sync_status ORDER BY updated_at DESC LIMIT ?",
                (limit,)
            )
            return [dict(row) for row in cur.fetchall()]
