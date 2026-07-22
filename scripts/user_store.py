"""
用户信息存储模块 — SQLite 统一存储

提供用户信息、对话记忆、权限管理的 SQLite 存储方案。
替代原 data/chat_memory.json 的 JSON 文件方案。

用法：
    from user_store import get_store
    store = get_store()
    store.add_memory("user_001", "user", "你好")
    ctx = store.get_context("user_001")

设计：
    - 单例模式（get_store()），全局共享一个连接
    - WAL 模式 + 序列化线程安全
    - 接口兼容 memory.py 的 add() / get_context() / format_context()
"""

import json
import logging
import os
import sqlite3
import threading
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

import requests

logger = logging.getLogger("user_store")

# ===== 数据库路径 =====
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.join(_SCRIPT_DIR, "..")
DB_DIR = os.path.join(_PROJECT_ROOT, "data")
DB_PATH = os.path.join(DB_DIR, "user_store.db")


class UserStore(ABC):
    """用户存储统一接口"""

    @abstractmethod
    def add_memory(self, user_id: str, role: str, content: str):
        """添加一条对话记录"""
        ...

    @abstractmethod
    def get_context(self, user_id: str, max_rounds: int = 5) -> list[dict]:
        """获取用户最近对话（返回 role/content 列表）"""
        ...

    @abstractmethod
    def format_context(self, user_id: str, max_rounds: int = 5,
                       max_content: int = 200) -> str:
        """格式化最近对话为文字（供拼进 system prompt）"""
        ...

    @abstractmethod
    def get_or_create_user(self, user_id: str, nick: str = "",
                           staff_id: str = "", corp_id: str = "") -> dict:
        """获取或创建用户记录"""
        ...

    @abstractmethod
    def update_user(self, user_id: str, **kwargs) -> bool:
        """更新用户信息"""
        ...

    @abstractmethod
    def get_user(self, user_id: str) -> Optional[dict]:
        """获取用户信息"""
        ...

    @abstractmethod
    def check_permission(self, user_id: str, action: str) -> bool:
        """检查用户权限
        action: "upload" | "delete" | "manage"
        """
        ...

    @abstractmethod
    def set_permission(self, user_id: str, action: str, value: bool) -> bool:
        """设置用户权限"""
        ...

    @abstractmethod
    def get_stats(self) -> dict:
        """获取使用统计数据"""
        ...

    @abstractmethod
    def get_user_centers(self, user_id: str) -> list[str]:
        """获取用户归属中心列表"""
        ...

    @abstractmethod
    def set_user_centers(self, user_id: str, centers: list[str]) -> bool:
        """设置用户归属中心列表"""
        ...

    @abstractmethod
    def list_users(self) -> list[dict]:
        """列出所有用户"""
        ...

    @abstractmethod
    def get_all_users_memory_count(self) -> dict[str, int]:
        """获取所有用户的对话数量"""
        ...

    @abstractmethod
    def sync_user_from_dingtalk(self, user_id: str, staff_id: str,
                                 access_token: str = "",
                                 nick: str = "") -> Optional[dict]:
        """从钉钉同步用户信息"""
        ...

    @abstractmethod
    def migrate_from_json(self, json_path: str) -> int:
        """从旧的 chat_memory.json 迁移数据"""
        ...


class SQLiteUserStore(UserStore):
    """SQLite 实现"""

    def __init__(self, db_path: str = DB_PATH):
        self._db_path = db_path
        self._local = threading.local()
        self._lock = threading.RLock()  # RLock: 同一线程可重入，避免嵌套死锁
        self._init_db()

    # ── 连接管理 ──────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        """获取当前线程的数据库连接（每个线程独立连接，线程安全）"""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
            conn = sqlite3.connect(
                self._db_path,
                check_same_thread=False,   # 跨线程安全（WAL 模式）
                timeout=5,                 # 等待锁超时 5 秒
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")      # WAL 模式，读写不互斥
            conn.execute("PRAGMA synchronous=NORMAL")     # 性能与安全的平衡
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return self._local.conn

    def _close_conn(self):
        """关闭当前线程的连接（清理用）"""
        if hasattr(self._local, "conn") and self._local.conn:
            try:
                self._local.conn.close()
            except Exception:
                pass
            self._local.conn = None

    # ── 初始化 ──────────────────────────────

    def _init_db(self):
        """建表（幂等，多次调用安全）"""
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id         TEXT PRIMARY KEY,
                staff_id        TEXT DEFAULT '',
                nick            TEXT DEFAULT '',
                corp_id         TEXT DEFAULT '',
                avatar          TEXT DEFAULT '',
                title           TEXT DEFAULT '',
                leader          INTEGER DEFAULT 0,
                role            TEXT DEFAULT 'user',
                center          TEXT DEFAULT 'public',
                centers         TEXT DEFAULT '[]',
                department_ids  TEXT DEFAULT '[]',
                department_names TEXT DEFAULT '[]',
                first_seen      TEXT,
                last_active     TEXT,
                created_at      TEXT DEFAULT (datetime('now','localtime')),
                updated_at      TEXT DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS conversations (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT NOT NULL,
                role        TEXT NOT NULL CHECK(role IN ('user','assistant')),
                content     TEXT NOT NULL,
                session_id  TEXT DEFAULT '',
                created_at  TEXT DEFAULT (datetime('now','localtime')),
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );

            CREATE INDEX IF NOT EXISTS idx_conv_user_time
                ON conversations(user_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_conv_created
                ON conversations(created_at);

            CREATE TABLE IF NOT EXISTS permissions (
                user_id     TEXT PRIMARY KEY,
                can_upload  INTEGER DEFAULT 0,
                can_delete  INTEGER DEFAULT 0,
                can_manage  INTEGER DEFAULT 0,
                updated_at  TEXT DEFAULT (datetime('now','localtime')),
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );
        """)
        conn.commit()
        # 迁移：给旧表加 center 字段
        try:
            conn.execute("ALTER TABLE users ADD COLUMN center TEXT DEFAULT 'public'")
            logger.info("[迁移] users 表新增列: center")
        except sqlite3.OperationalError:
            pass  # 列已存在
        try:
            conn.execute("ALTER TABLE users ADD COLUMN centers TEXT DEFAULT '[]'")
            logger.info("[迁移] users 表新增列: centers")
        except sqlite3.OperationalError:
            pass  # 列已存在
        conn.commit()
        logger.info(f"SQLite 用户存储已初始化: {self._db_path}")

    # ── 对话记忆 ──────────────────────────────

    def add_memory(self, user_id: str, role: str, content: str):
        """添加对话记录（自动创建用户，防重复）"""
        conn = self._get_conn()
        with self._lock:
            try:
                # 确保用户存在
                self.get_or_create_user(user_id)

                # 防重复：最后一条同 role 内容一样则跳过
                row = conn.execute(
                    "SELECT role, content FROM conversations "
                    "WHERE user_id = ? ORDER BY id DESC LIMIT 1",
                    (user_id,),
                ).fetchone()
                if row and row["role"] == role and row["content"] == content:
                    return

                # 写入
                conn.execute(
                    "INSERT INTO conversations (user_id, role, content) "
                    "VALUES (?, ?, ?)",
                    (user_id, role, content),
                )
                conn.commit()
            except Exception as e:
                conn.rollback()
                logger.warning(f"写入对话记录失败: {e}")

    def get_context(self, user_id: str, max_rounds: int = 5) -> list[dict]:
        """获取用户最近 N 轮对话"""
        conn = self._get_conn()
        max_items = max_rounds * 2
        try:
            rows = conn.execute(
                "SELECT role, content FROM conversations "
                "WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                (user_id, max_items),
            ).fetchall()
            # 反转成正序
            result = []
            for row in reversed(rows):
                result.append({"role": row["role"], "content": row["content"]})
            return result
        except Exception as e:
            logger.warning(f"读取对话记录失败: {e}")
            return []

    def format_context(self, user_id: str, max_rounds: int = 5,
                       max_content: int = 200) -> str:
        """格式化最近对话为文字"""
        context = self.get_context(user_id, max_rounds)
        if not context:
            return ""

        lines = ["\n\n## 最近的对话历史"]
        for msg in context:
            speaker = "用户" if msg["role"] == "user" else "助手"
            content = msg["content"][:max_content]
            lines.append(f"{speaker}：{content}")
        return "\n".join(lines)

    # ── 用户信息 ──────────────────────────────

    def get_or_create_user(self, user_id: str, nick: str = "",
                           staff_id: str = "", corp_id: str = "") -> dict:
        """获取或创建用户记录"""
        conn = self._get_conn()
        with self._lock:
            try:
                row = conn.execute(
                    "SELECT * FROM users WHERE user_id = ?", (user_id,)
                ).fetchone()

                if row:
                    # 更新活跃时间，补全信息
                    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    updates = []
                    params = []
                    if nick and not row["nick"]:
                        updates.append("nick = ?")
                        params.append(nick)
                    if staff_id and not row["staff_id"]:
                        updates.append("staff_id = ?")
                        params.append(staff_id)
                    if corp_id and not row["corp_id"]:
                        updates.append("corp_id = ?")
                        params.append(corp_id)
                    if updates:
                        updates.append("last_active = ?")
                        params.append(now)
                        params.append(user_id)
                        conn.execute(
                            f"UPDATE users SET {', '.join(updates)} "
                            f"WHERE user_id = ?",
                            params,
                        )
                    else:
                        conn.execute(
                            "UPDATE users SET last_active = ? WHERE user_id = ?",
                            (now, user_id),
                        )
                    conn.commit()
                    return dict(row)

                # 创建新用户
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                conn.execute(
                    "INSERT INTO users (user_id, staff_id, nick, corp_id, "
                    "first_seen, last_active) VALUES (?, ?, ?, ?, ?, ?)",
                    (user_id, staff_id, nick, corp_id, now, now),
                )
                conn.commit()
                row = conn.execute(
                    "SELECT * FROM users WHERE user_id = ?", (user_id,)
                ).fetchone()
                logger.info(f"新建用户: {user_id} ({nick})")
                return dict(row) if row else {}

            except Exception as e:
                conn.rollback()
                logger.warning(f"获取/创建用户失败: {e}")
                return {"user_id": user_id, "nick": nick}

    def update_user(self, user_id: str, **kwargs) -> bool:
        """更新用户字段（key=value 方式传参）"""
        if not kwargs:
            return False
        conn = self._get_conn()
        with self._lock:
            try:
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                kwargs["updated_at"] = now
                sets = ", ".join(f"{k} = ?" for k in kwargs)
                vals = list(kwargs.values()) + [user_id]
                conn.execute(
                    f"UPDATE users SET {sets} WHERE user_id = ?", vals
                )
                conn.commit()
                return conn.total_changes > 0
            except Exception as e:
                conn.rollback()
                logger.warning(f"更新用户失败: {e}")
                return False

    def get_user(self, user_id: str) -> Optional[dict]:
        """获取用户信息"""
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
            return dict(row) if row else None
        except Exception as e:
            logger.warning(f"查询用户失败: {e}")
            return None

    def get_user_centers(self, user_id: str) -> list[str]:
        """获取用户归属中心列表"""
        user = self.get_user(user_id)
        if not user:
            return []
        raw = user.get("centers", "[]")
        try:
            centers = json.loads(raw) if isinstance(raw, str) else list(raw)
            return [c for c in centers if c] if isinstance(centers, list) else []
        except (json.JSONDecodeError, TypeError):
            return []

    def set_user_centers(self, user_id: str, centers: list[str]) -> bool:
        """设置用户归属中心列表"""
        return self.update_user(user_id, centers=json.dumps(centers, ensure_ascii=False))

    def list_users(self) -> list[dict]:
        """列出所有用户"""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM users ORDER BY last_active DESC"
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.warning(f"列出用户失败: {e}")
            return []

    # ── 权限系统 ──────────────────────────────

    def check_permission(self, user_id: str, action: str) -> bool:
        """检查用户权限

        判定流程：
        1. permissions 表有手动配置 → 以手动配置为准
        2. users 表 leader=1 或 role='admin' → 有权限
        3. 否则 → 无权限

        Args:
            user_id: 用户 ID
            action: "upload" | "delete" | "manage"

        Returns:
            bool
        """
        conn = self._get_conn()
        try:
            # 先查 permissions 表（手动配置优先）
            perm_col = f"can_{action}"
            perm = conn.execute(
                f"SELECT {perm_col} FROM permissions WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if perm is not None:
                return bool(perm[0])

            # 再查 users 表（自动判定）
            user = conn.execute(
                "SELECT leader, role FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if user:
                if user["role"] == "admin":
                    return True
                if action == "upload" and user["leader"]:
                    return True
            return False
        except Exception as e:
            logger.warning(f"权限检查失败: {e}")
            return False

    def set_permission(self, user_id: str, action: str, value: bool) -> bool:
        """设置用户权限（写入 permissions 表）"""
        conn = self._get_conn()
        with self._lock:
            try:
                perm_col = f"can_{action}"
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                conn.execute(
                    f"INSERT INTO permissions (user_id, {perm_col}, updated_at) "
                    f"VALUES (?, ?, ?) "
                    f"ON CONFLICT(user_id) DO UPDATE SET "
                    f"{perm_col} = ?, updated_at = ?",
                    (user_id, int(value), now, int(value), now),
                )
                conn.commit()
                return True
            except Exception as e:
                conn.rollback()
                logger.warning(f"设置权限失败: {e}")
                return False

    # ── 钉钉同步 ──────────────────────────────

    _DINGTALK_OLD_API = "https://oapi.dingtalk.com"

    def _get_dingtalk_token(self) -> str | None:
        """获取旧版钉钉 API access_token（用 appkey + appsecret）"""
        try:
            import importlib
            cfg = importlib.import_module("config")
            appkey = getattr(cfg, "DINGTALK_CLIENT_ID", "")
            appsecret = getattr(cfg, "DINGTALK_CLIENT_SECRET", "")
        except Exception:
            logger.warning("读取钉钉凭证失败")
            return None

        if not appkey or not appsecret:
            logger.warning("钉钉凭证未配置")
            return None

        try:
            url = f"{self._DINGTALK_OLD_API}/gettoken"
            resp = requests.get(
                url,
                params={"appkey": appkey, "appsecret": appsecret},
                timeout=10,
            )
            data = resp.json()
            if data.get("errcode") != 0:
                logger.warning(f"获取钉钉 token 失败: {data.get('errcode')} {data.get('errmsg')}")
                return None
            return data.get("access_token")
        except Exception as e:
            logger.warning(f"获取钉钉 token 异常: {e}")
            return None

    def sync_user_from_dingtalk(self, user_id: str, staff_id: str,
                                 access_token: str = "",
                                 nick: str = "") -> Optional[dict]:
        """从钉钉旧版 API 同步用户信息

        使用 qyapi_get_member 权限，走 oapi.dingtalk.com 旧版接口。

        Args:
            user_id: 我们的 user_id（钉钉 sender_id / unionId）
            staff_id: 钉钉员工 ID (sender_staff_id / userid)
            access_token: 不再使用，保留参数兼容
            nick: 钉钉显示昵称（sender_nick），优先于 API 返回的真名

        Returns:
            更新后的用户 dict，失败返回 None
        """
        if not staff_id:
            logger.warning(f"sync_user_from_dingtalk: staff_id 为空 (user_id={user_id})")
            return None

        # 1. 获取旧版 API token
        token = self._get_dingtalk_token()
        if not token:
            logger.warning("无法获取钉钉 token，跳过同步")
            return None

        try:
            headers = {"Content-Type": "application/json"}
            params = {"access_token": token}

            # 2. 查用户详情
            url = f"{self._DINGTALK_OLD_API}/topapi/v2/user/get"
            logger.info(f"钉钉同步用户: staff_id={staff_id}")
            resp = requests.post(
                url, headers=headers, params=params,
                json={"userid": staff_id}, timeout=10,
            )
            data = resp.json()

            if data.get("errcode") != 0:
                logger.warning(f"钉钉查用户失败: {data.get('errcode')} {data.get('errmsg')}")
                return None

            result = data.get("result", {})
            logger.info(f"钉钉用户数据: dept_ids={result.get('dept_id_list')}, "
                        f"title={result.get('title')}, leader={result.get('leader')}")

            # 提取字段
            dept_ids = result.get("dept_id_list", []) or []
            title = (result.get("title", "") or "").strip()
            leader = 1 if result.get("leader") else 0
            name = (result.get("name", "") or "").strip()
            avatar = (result.get("avatar", "") or "").strip()
            # 显示昵称优先用 sender_nick，其次用 API 返回的真名
            nick = nick or name

            # 3. 查部门名称
            dept_names = []
            for dept_id in dept_ids[:5]:
                try:
                    dept_url = f"{self._DINGTALK_OLD_API}/topapi/v2/department/get"
                    dept_resp = requests.post(
                        dept_url, headers=headers, params=params,
                        json={"dept_id": dept_id}, timeout=10,
                    )
                    if dept_resp.status_code == 200:
                        dept_data = dept_resp.json()
                        if dept_data.get("errcode") == 0:
                            dept_names.append(
                                dept_data.get("result", {}).get("name", str(dept_id))
                            )
                        else:
                            dept_names.append(str(dept_id))
                    else:
                        dept_names.append(str(dept_id))
                except Exception as e:
                    logger.warning(f"查询部门 {dept_id} 失败: {e}")
                    dept_names.append(str(dept_id))

            # 4. 更新数据库
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.update_user(
                user_id,
                staff_id=staff_id,
                nick=nick or "",
                title=title,
                leader=leader,
                avatar=avatar,
                department_ids=json.dumps([str(d) for d in dept_ids]),
                department_names=json.dumps(dept_names),
                updated_at=now,
            )

            updated = self.get_user(user_id)
            logger.info(f"钉钉同步完成: {nick} | {'主管' if leader else '成员'} | "
                        f"部门={dept_names} | 职位={title}")
            return updated

        except requests.exceptions.Timeout:
            logger.warning(f"钉钉 API 超时 (staff_id={staff_id})")
            return None
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"钉钉 API 连接失败: {e}")
            return None
        except Exception as e:
            logger.warning(f"钉钉同步异常: {e}")
            return None

    # ── 统计查询 ──────────────────────────────

    def get_stats(self) -> dict:
        """获取使用统计数据"""
        conn = self._get_conn()
        try:
            total_users = conn.execute(
                "SELECT COUNT(*) FROM users"
            ).fetchone()[0]

            today = datetime.now().strftime("%Y-%m-%d")
            active_today = conn.execute(
                "SELECT COUNT(DISTINCT user_id) FROM conversations "
                "WHERE date(created_at) = ?", (today,)
            ).fetchone()[0]

            total_messages = conn.execute(
                "SELECT COUNT(*) FROM conversations"
            ).fetchone()[0]

            # 每日趋势（近 7 天）
            daily = conn.execute(
                "SELECT date(created_at) as day, COUNT(*) as cnt "
                "FROM conversations "
                "WHERE created_at >= datetime('now', '-7 days', 'localtime') "
                "GROUP BY day ORDER BY day"
            ).fetchall()

            # 用户排行 Top 10
            top_users = conn.execute(
                "SELECT user_id, COUNT(*) as cnt FROM conversations "
                "GROUP BY user_id ORDER BY cnt DESC LIMIT 10"
            ).fetchall()

            return {
                "total_users": total_users,
                "active_today": active_today,
                "total_messages": total_messages,
                "daily_trend": [
                    {"date": r["day"], "count": r["cnt"]} for r in daily
                ],
                "top_users": [
                    {"user_id": r["user_id"], "count": r["cnt"]}
                    for r in top_users
                ],
            }
        except Exception as e:
            logger.warning(f"统计查询失败: {e}")
            return {
                "total_users": 0, "active_today": 0, "total_messages": 0,
                "daily_trend": [], "top_users": [],
            }

    def get_all_users_memory_count(self) -> dict[str, int]:
        """获取每个用户的对话数量"""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT user_id, COUNT(*) as cnt FROM conversations "
                "GROUP BY user_id ORDER BY cnt DESC"
            ).fetchall()
            return {r["user_id"]: r["cnt"] for r in rows}
        except Exception as e:
            logger.warning(f"查询用户对话数失败: {e}")
            return {}

    # ── 数据迁移 ──────────────────────────────

    def migrate_from_json(self, json_path: str) -> int:
        """从旧的 chat_memory.json 迁移数据到 SQLite

        Args:
            json_path: chat_memory.json 的路径

        Returns:
            迁移的消息条数
        """
        if not os.path.exists(json_path):
            logger.warning(f"迁移源文件不存在: {json_path}")
            return 0

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.error(f"读取 JSON 文件失败: {e}")
            return 0

        total = 0
        for user_id, messages in data.items():
            for msg in messages:
                role = msg.get("role", "")
                content = msg.get("content", "")
                if role in ("user", "assistant") and content:
                    self.add_memory(user_id, role, content)
                    total += 1

        logger.info(f"迁移完成：共导入 {total} 条消息，涉及 {len(data)} 个用户")
        return total

    # ── 清理 ──────────────────────────────

    def close(self):
        """关闭当前线程的数据库连接"""
        self._close_conn()


# ===== 全局单例 =====
_store: Optional[SQLiteUserStore] = None


def get_store() -> SQLiteUserStore:
    """获取全局用户存储实例（单例）"""
    global _store
    if _store is None:
        _store = SQLiteUserStore()
    return _store


# ===== 兼容 memory.py 接口 =====
# 供外部通过 from user_store import add, get_context, format_context 使用
# 也用于 memory.py 的代理模式

def add(user_id: str, role: str, content: str):
    """兼容 memory.add() 接口"""
    get_store().add_memory(user_id, role, content)


def get_context(user_id: str, max_rounds: int = 5) -> list[dict]:
    """兼容 memory.get_context() 接口"""
    return get_store().get_context(user_id, max_rounds)


def format_context(user_id: str, max_rounds: int = 5,
                   max_content: int = 200) -> str:
    """兼容 memory.format_context() 接口"""
    return get_store().format_context(user_id, max_rounds, max_content)
