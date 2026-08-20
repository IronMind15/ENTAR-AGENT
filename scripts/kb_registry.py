"""
知识库注册表（v1.11.5）

统一管理知识库元数据：key / name / description / collection / department / enabled。
让「创建知识库」成为独立通用功能——不再绑定具体内容（标准 PDF → standards、
故障 Excel → error_codes 等写死），可动态新建库并让查询/学习通用化。

同时预留 department 字段：为未来「按部门开权限」做数据铺垫。查询侧
get_visible_knowledge_bases 按用户中心过滤可见库（预留逻辑，当前默认库均为
public 不拦截）；学习侧入库时 department 从目标库继承。

数据存 data/user_store.db 的 knowledge_bases 表（与 user_store.py 共用文件，
WAL 模式读写不互斥）。

用法：
    from kb_registry import create_knowledge_base, list_knowledge_bases, resolve_kb
    kb = create_knowledge_base("产品手册", "产品说明书、规格书", department="rd")
    visible = list_knowledge_bases()      # 启用中的全部
    hits = resolve_kb("产品手册")          # 按 key / name 模糊解析
"""

import logging
import os
import sqlite3
import threading
from datetime import datetime
from typing import Optional

from paths import DB_PATH as _DEFAULT_DB_PATH

logger = logging.getLogger("kb_registry")

# ===== 数据库路径（复用 user_store.db，模式同 doc_mgr/sync_tracker.py） =====
DB_PATH = str(_DEFAULT_DB_PATH)
DB_DIR = os.path.dirname(DB_PATH)

# ===== 现有默认知识库（种子数据，INSERT OR IGNORE 幂等写入） =====
_DEFAULT_KBS = [
    {"key": "error_codes", "name": "故障知识库",
     "description": "PCS 产品故障代码定义、故障原因与排查（数据源：PCS参数表遥信 DI）",
     "department": "public"},
    {"key": "standards", "name": "标准知识库",
     "description": "国家标准/行业规范/技术指标 + 公司上传文档（「帮我学习」默认库）",
     "department": "public"},
    {"key": "experience_kb", "name": "经验知识库",
     "description": "工程经验五段式条目：故障现象/排查步骤/根因/解决方案/验证结果",
     "department": "public"},
]

_PUBLIC = "public"


def _clean_key(name: str) -> str:
    """由中文名生成内部 key：去空格（Chroma 支持 Unicode collection 名）"""
    return "".join(name.split())


class KBRegistry:
    """知识库注册表（线程安全：threading.local + RLock，模式同 SyncTracker）"""

    def __init__(self, db_path: str = DB_PATH):
        self._db_path = db_path
        self._local = threading.local()
        self._lock = threading.RLock()
        self._init_db()
        self.seed_defaults()

    def close(self) -> None:
        """关闭当前线程的 SQLite 连接（测试/维护用）"""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    # ===== DB 连接 =====
    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            self._local.conn = conn
        return self._local.conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._get_conn()
            conn.execute("""
                CREATE TABLE IF NOT EXISTS knowledge_bases (
                    key         TEXT PRIMARY KEY,
                    name        TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    collection  TEXT NOT NULL,
                    department  TEXT NOT NULL DEFAULT 'public',
                    enabled     INTEGER NOT NULL DEFAULT 1,
                    created_at  TEXT DEFAULT (datetime('now','localtime'))
                )
            """)
            conn.commit()

    def seed_defaults(self) -> None:
        """幂等写入默认三库（INSERT OR IGNORE）"""
        with self._lock:
            conn = self._get_conn()
            for kb in _DEFAULT_KBS:
                conn.execute(
                    """INSERT OR IGNORE INTO knowledge_bases
                       (key, name, description, collection, department, enabled)
                       VALUES (?, ?, ?, ?, ?, 1)""",
                    (kb["key"], kb["name"], kb["description"],
                     kb["key"], kb["department"]),
                )
            conn.commit()

    # ===== 查询 =====
    def list_knowledge_bases(self, enabled_only: bool = True) -> list[dict]:
        with self._lock:
            conn = self._get_conn()
            sql = "SELECT * FROM knowledge_bases"
            if enabled_only:
                sql += " WHERE enabled = 1"
            sql += " ORDER BY created_at, key"
            rows = conn.execute(sql).fetchall()
            return [dict(r) for r in rows]

    def get_knowledge_base(self, key: str) -> Optional[dict]:
        if not key:
            return None
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT * FROM knowledge_bases WHERE key = ?", (key,)
            ).fetchone()
            return dict(row) if row else None

    def resolve_kb(self, text: str) -> Optional[dict]:
        """按 key 精确匹配，再按 name 模糊匹配（name 是 text 的子串或反之）

        Returns: 命中的知识库 dict；无匹配返回 None
        """
        if not text:
            return None
        t = text.strip()
        # 1) key 精确
        kb = self.get_knowledge_base(t)
        if kb:
            return kb
        # 2) name 精确
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT * FROM knowledge_bases WHERE name = ?", (t,)
            ).fetchone()
            if row:
                return dict(row)
        # 3) name 双向包含
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute(
                "SELECT * FROM knowledge_bases WHERE enabled = 1"
            ).fetchall()
            for r in rows:
                name = r["name"] or ""
                if name and (name in t or t in name):
                    return dict(r)
        return None

    def get_visible_knowledge_bases(self, centers: Optional[list[str]] = None
                                    ) -> list[dict]:
        """按用户中心过滤可见库（部门权限预留逻辑）

        centers 为 None/空 → 返回全部启用库（老用户无 centers 配置时不过滤，避免误伤）；
        否则返回 department ∈ centers ∪ {public} 的库。
        """
        if not centers:
            return self.list_knowledge_bases(enabled_only=True)
        wanted = set(centers) | {_PUBLIC}
        return [kb for kb in self.list_knowledge_bases(enabled_only=True)
                if (kb.get("department") or _PUBLIC) in wanted]

    # ===== 写入 =====
    def create_knowledge_base(self, name: str, description: str = "",
                              department: str = _PUBLIC, key: Optional[str] = None,
                              collection: Optional[str] = None) -> dict:
        """创建知识库。

        校验：name 非空、key 唯一、department 合法（center_config.is_valid_center）。

        Returns:
            {"ok": True, "kb": {...}} 或 {"ok": False, "message": "..."}
        """
        from center_config import is_valid_center

        name = (name or "").strip()
        if not name:
            return {"ok": False, "message": "知识库名称不能为空"}
        dept = (department or _PUBLIC).strip().lower()
        if not is_valid_center(dept):
            return {"ok": False,
                    "message": f"部门「{department}」不合法，可选：public/pmo/rd/mfg/bz/ops"}

        # 生成/校验 key
        kb_key = (key or "").strip() or _clean_key(name)
        if not kb_key:
            return {"ok": False, "message": "知识库标识不能为空"}

        with self._lock:
            conn = self._get_conn()
            # 重名/重复 key 检查（含已禁用的）
            dup = conn.execute(
                "SELECT * FROM knowledge_bases WHERE key = ? OR name = ?",
                (kb_key, name)).fetchone()
            if dup:
                return {"ok": False,
                        "message": f"知识库「{name}」已存在（{dup['key']}），请换一个名字"}
            coll = (collection or "").strip() or kb_key
            conn.execute(
                """INSERT INTO knowledge_bases
                   (key, name, description, collection, department, enabled)
                   VALUES (?, ?, ?, ?, ?, 1)""",
                (kb_key, name, (description or "").strip(), coll, dept),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM knowledge_bases WHERE key = ?", (kb_key,)
            ).fetchone()
        logger.info(f"创建知识库: {kb_key}（{name}，部门={dept}）")
        return {"ok": True, "kb": dict(row) if row else {
            "key": kb_key, "name": name, "description": description,
            "collection": coll, "department": dept, "enabled": 1,
            "created_at": datetime.now().isoformat()}}

    def update_knowledge_base(self, key: str, **fields) -> Optional[dict]:
        """更新知识库字段（name/description/department/enabled），返回更新后的记录"""
        allowed = {"name", "description", "department", "enabled", "collection"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return self.get_knowledge_base(key)
        with self._lock:
            conn = self._get_conn()
            cols = ", ".join(f"{k} = ?" for k in updates)
            conn.execute(f"UPDATE knowledge_bases SET {cols} WHERE key = ?",
                         (*updates.values(), key))
            conn.commit()
            row = conn.execute(
                "SELECT * FROM knowledge_bases WHERE key = ?", (key,)
            ).fetchone()
        return dict(row) if row else None

    def delete_knowledge_base(self, key: str) -> bool:
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute(
                "DELETE FROM knowledge_bases WHERE key = ?", (key,))
            conn.commit()
            return cur.rowcount > 0


# ===== 模块级单例 + 便捷函数 =====
_registry: Optional[KBRegistry] = None
_registry_lock = threading.Lock()


def get_registry() -> KBRegistry:
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = KBRegistry()
    return _registry


def reset_registry() -> None:
    """重置单例（测试用）"""
    global _registry
    with _registry_lock:
        if _registry is not None:
            try:
                _registry.close()
            except Exception:
                pass
        _registry = None


def list_knowledge_bases(enabled_only: bool = True) -> list[dict]:
    return get_registry().list_knowledge_bases(enabled_only=enabled_only)


def get_knowledge_base(key: str) -> Optional[dict]:
    return get_registry().get_knowledge_base(key)


def resolve_kb(text: str) -> Optional[dict]:
    return get_registry().resolve_kb(text)


def get_visible_knowledge_bases(centers: Optional[list[str]] = None) -> list[dict]:
    return get_registry().get_visible_knowledge_bases(centers)


def create_knowledge_base(name: str, description: str = "",
                          department: str = _PUBLIC,
                          key: Optional[str] = None,
                          collection: Optional[str] = None) -> dict:
    return get_registry().create_knowledge_base(
        name, description, department, key, collection)


def update_knowledge_base(key: str, **fields) -> Optional[dict]:
    return get_registry().update_knowledge_base(key, **fields)


def delete_knowledge_base(key: str) -> bool:
    return get_registry().delete_knowledge_base(key)
