"""看板模板存储（v1.12.0）— dashboard_templates 表 + CRUD + 系统种子

模板只控制「输出格式」：要点区、结论区（可多个，按 level 分流）、数据完整性提醒、
数据来源的顺序与标题，以及 map/reduce 阶段的补充指令。数据采集、变化检测、证据
校验链路完全不动——换模板不改变采集什么数据，只改变怎么汇报。

scope：
    system — 全员可见，内置 3 个种子（daily 与现输出逐字一致，作为回归锚点，
             禁止修改 section_spec，除非同步改 llm_pipeline._render 的回归测试）
    user   — 创建者私有（owner_user_id），他人不可见、不可改、不可删

数据存 data/user_store.db 的 dashboard_templates 表（同 subscription_store，
WAL 模式读写不互斥）。
"""

import json
import logging
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

logger = logging.getLogger("dashboard.template_store")

# 复用 user_store.db（同一 data 目录）
from user_store import DB_PATH as _DEFAULT_DB_PATH  # noqa: E402


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _dumps(v) -> str:
    try:
        return json.dumps(v, ensure_ascii=False)
    except Exception:
        return json.dumps([])


def _loads_spec(raw):
    if raw in (None, ""):
        return []
    try:
        v = json.loads(raw)
        return v if isinstance(v, list) else []
    except Exception:
        return []


# ===== 默认模板 =====
# daily = 当前硬编码输出的逐字锚点。任何字段改动都会破坏
# tests/test_dashboard_templates.py::test_daily_template_is_regression_anchor。
_DEFAULT_SECTION_SPEC = [
    {"kind": "headline", "title": "今日要点"},
    {"kind": "claims", "title": "重点更新",
     "levels": ["risk", "decision", "update", "info"]},
    {"kind": "errors", "title": "数据完整性提醒"},
    {"kind": "sources", "title": "数据来源"},
]
_DEFAULT_MAP_INSTRUCTION = ("优先筛选 priority=changed 的今日变化；"
                            "存量只保留持续风险、阻塞或待决策事项")
_DEFAULT_REDUCE_INSTRUCTION = "依据筛选记录的完整字段去重、排序，生成最多12条老板摘要"

_SYSTEM_TEMPLATES = [
    {
        "key": "daily",
        "name": "每日简报",
        "description": "今日变化优先 + 重点更新 + 数据来源（默认模板，与当前输出一致）",
        "map_instructions": _DEFAULT_MAP_INSTRUCTION,
        "reduce_instructions": _DEFAULT_REDUCE_INSTRUCTION,
        "section_spec": _DEFAULT_SECTION_SPEC,
    },
    {
        "key": "weekly",
        "name": "周报总结",
        "description": "本周进展与风险待决策分列，适合每周推送",
        "map_instructions": ("优先筛选 priority=changed 的本周变化；"
                             "存量只保留持续风险、阻塞或待决策事项"),
        "reduce_instructions": ("按【本周进展】与【风险与待决策】两个板块组织结论；"
                                "进展收录新进展和推进中事项，风险收录阻塞、风险与"
                                "需要拍板的事项；每条结论必须可回溯原值"),
        "section_spec": [
            {"kind": "headline", "title": "本周要点"},
            {"kind": "claims", "title": "本周进展", "levels": ["update", "info"]},
            {"kind": "claims", "title": "风险与待决策", "levels": ["risk", "decision"]},
            {"kind": "errors", "title": "数据完整性提醒"},
            {"kind": "sources", "title": "数据来源"},
        ],
    },
    {
        "key": "project",
        "name": "项目看板",
        "description": "按项目维度突出里程碑、负责人与阻塞事项",
        "map_instructions": ("优先筛选 priority=changed 的今日变化；"
                             "重点保留带负责人、里程碑、阻塞标记的记录；"
                             "存量只保留持续风险、阻塞或待决策事项"),
        "reduce_instructions": ("优先提炼里程碑进展、负责人变更与阻塞事项；"
                                "每条结论尽量带出负责人与进展阶段（从字段原值提取）；"
                                "全部结论必须可回溯原值"),
        "section_spec": [
            {"kind": "headline", "title": "项目要点"},
            {"kind": "claims", "title": "里程碑与进展", "levels": ["update", "info"]},
            {"kind": "claims", "title": "风险与阻塞", "levels": ["risk", "decision"]},
            {"kind": "errors", "title": "数据完整性提醒"},
            {"kind": "sources", "title": "数据来源"},
        ],
    },
]

_SYSTEM_KEYS = {tpl["key"] for tpl in _SYSTEM_TEMPLATES}


@dataclass
class DashboardTemplate:
    """一条看板模板（字段对应 dashboard_templates 表）"""
    key: str
    name: str
    description: str = ""
    scope: str = "system"          # system | user
    owner_user_id: str = ""
    title: str = ""                # 空=沿用订阅标题
    map_instructions: str = ""
    reduce_instructions: str = ""
    section_spec: list = field(default_factory=list)   # [{kind,title,levels?}]
    enabled: bool = True
    created_at: str = ""
    updated_at: str = ""


class TemplateStore:
    """看板模板存储（threading.local + RLock，同 subscription_store 模式）"""

    def __init__(self, db_path: str = _DEFAULT_DB_PATH):
        self._db_path = db_path
        self._local = threading.local()
        self._lock = threading.RLock()
        self._init_db()
        self.seed_system()

    # ── 连接管理 ──────────────────────────────
    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            import os
            os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
            conn = sqlite3.connect(self._db_path, check_same_thread=False, timeout=5)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn
        return self._local.conn

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
            CREATE TABLE IF NOT EXISTS dashboard_templates (
                key                 TEXT PRIMARY KEY,
                name                TEXT NOT NULL,
                description         TEXT DEFAULT '',
                scope               TEXT NOT NULL DEFAULT 'system',
                owner_user_id       TEXT DEFAULT '',
                title               TEXT DEFAULT '',
                map_instructions    TEXT DEFAULT '',
                reduce_instructions TEXT DEFAULT '',
                section_spec        TEXT DEFAULT '[]',
                enabled             INTEGER DEFAULT 1,
                created_at          TEXT DEFAULT '',
                updated_at          TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_dashboard_templates_owner
                ON dashboard_templates(owner_user_id);
        """)
        conn.commit()

    # ── 种子 ──────────────────────────────
    def seed_system(self):
        """幂等写入 3 个系统模板（INSERT OR IGNORE，不改已存在记录）"""
        with self._lock:
            conn = self._get_conn()
            now = _now()
            for tpl in _SYSTEM_TEMPLATES:
                conn.execute(
                    """INSERT OR IGNORE INTO dashboard_templates
                       (key, name, description, scope, owner_user_id, title,
                        map_instructions, reduce_instructions, section_spec,
                        enabled, created_at, updated_at)
                       VALUES (?,?,?, 'system', '', '', ?,?,?, 1, ?, ?)""",
                    (tpl["key"], tpl["name"], tpl["description"],
                     tpl["map_instructions"], tpl["reduce_instructions"],
                     _dumps(tpl["section_spec"]), now, now))
            conn.commit()

    # ── Row → Template ──────────────────────────────
    def _row_to_template(self, row) -> DashboardTemplate:
        return DashboardTemplate(
            key=row["key"], name=row["name"], description=row["description"],
            scope=row["scope"], owner_user_id=row["owner_user_id"],
            title=row["title"],
            map_instructions=row["map_instructions"],
            reduce_instructions=row["reduce_instructions"],
            section_spec=_loads_spec(row["section_spec"]),
            enabled=bool(row["enabled"]),
            created_at=row["created_at"], updated_at=row["updated_at"],
        )

    # ── 查询 ──────────────────────────────
    def list_visible(self, user_id: str = "") -> list[DashboardTemplate]:
        """当前用户可见模板：全部 system + 本人 user；user_id 为空只看 system"""
        with self._lock:
            conn = self._get_conn()
            if user_id:
                rows = conn.execute(
                    """SELECT * FROM dashboard_templates WHERE enabled=1
                       AND (scope='system' OR owner_user_id=?)
                       ORDER BY scope DESC, key""", (user_id,)).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM dashboard_templates WHERE enabled=1
                       AND scope='system' ORDER BY key""").fetchall()
        return [self._row_to_template(r) for r in rows]

    def get(self, key: str, user_id: str = "") -> Optional[DashboardTemplate]:
        """取模板；他人 user 私有模板返回 None"""
        if not key:
            return None
        with self._lock:
            row = self._get_conn().execute(
                "SELECT * FROM dashboard_templates WHERE key=?", (key,)).fetchone()
        if not row:
            return None
        tpl = self._row_to_template(row)
        if tpl.scope == "user" and user_id and tpl.owner_user_id != user_id:
            return None
        return tpl

    def resolve(self, text: str, user_id: str = "") -> Optional[DashboardTemplate]:
        """按名称/键在可见模板中解析（最长名称包含匹配优先，别名兜底）

        别名：每日/日报→daily；周报/每周→weekly；项目/里程碑→project。
        """
        t = (text or "").strip()
        if not t:
            return None
        templates = self.list_visible(user_id)
        # 1) 键精确（英文小写匹配）
        for tpl in templates:
            if tpl.key and tpl.key.lower() == t.lower():
                return tpl
        # 2) 名称最长包含匹配
        best, best_len = None, 0
        for tpl in templates:
            name = (tpl.name or "").strip()
            if name and name in t and len(name) > best_len:
                best, best_len = tpl, len(name)
        if best:
            return best
        # 3) 别名
        for key, aliases in (("daily", ("每日", "日报", "日", "今天")),
                             ("weekly", ("周报", "每周", "周总结", "周")),
                             ("project", ("项目", "里程碑"))):
            for alias in aliases:
                if alias in t:
                    return self.get(key, user_id)
        return None

    # ── 用户模板写入 ──────────────────────────────
    def create_user_template(self, key: str, name: str, user_id: str,
                             description: str = "", title: str = "",
                             map_instructions: str = "",
                             reduce_instructions: str = "",
                             section_spec: Optional[list] = None) -> dict:
        """创建用户私有模板。

        Returns: {"ok": True, "template": ...} 或 {"ok": False, "message": "..."}
        """
        key = (key or "").strip()
        name = (name or "").strip()
        if not key or not name:
            return {"ok": False, "message": "模板标识与名称不能为空"}
        if key in _SYSTEM_KEYS:
            return {"ok": False, "message": f"模板标识「{key}」与系统模板冲突，请换一个名字"}
        with self._lock:
            conn = self._get_conn()
            dup = conn.execute(
                """SELECT * FROM dashboard_templates
                   WHERE key=? OR (name=? AND owner_user_id=?)""",
                (key, name, user_id)).fetchone()
            if dup:
                return {"ok": False,
                        "message": f"模板「{name}」已存在，请换一个名字"}
            now = _now()
            conn.execute(
                """INSERT INTO dashboard_templates
                   (key, name, description, scope, owner_user_id, title,
                    map_instructions, reduce_instructions, section_spec,
                    enabled, created_at, updated_at)
                   VALUES (?,?,?, 'user', ?, ?, ?,?,?, 1, ?, ?)""",
                (key, name, (description or "").strip(), user_id,
                 (title or "").strip(), (map_instructions or "").strip(),
                 (reduce_instructions or "").strip(),
                 _dumps(section_spec or []), now, now))
            conn.commit()
        logger.info("创建看板模板: %s（%s，%s）", key, name, user_id)
        return {"ok": True, "template": self.get(key, user_id)}

    def update_user_template(self, key: str, user_id: str, **fields
                             ) -> Optional[DashboardTemplate]:
        """更新用户模板字段；非本人/系统模板返回 None"""
        allowed = {"name", "description", "title", "map_instructions",
                   "reduce_instructions", "section_spec", "enabled"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        tpl = self.get(key, user_id)
        if not tpl or tpl.scope != "user":
            return None
        if not updates:
            return tpl
        with self._lock:
            conn = self._get_conn()
            if "section_spec" in updates:
                updates["section_spec"] = _dumps(updates["section_spec"])
            cols = ", ".join(f"{k}=?" for k in updates)
            conn.execute(
                f"UPDATE dashboard_templates SET {cols}, updated_at=? WHERE key=?",
                (*updates.values(), _now(), key))
            conn.commit()
        return self.get(key, user_id)

    def delete_user_template(self, key: str, user_id: str) -> bool:
        """删除用户私有模板（只删本人；系统模板不可删）"""
        tpl = self.get(key, user_id)
        if not tpl or tpl.scope != "user":
            return False
        with self._lock:
            cur = self._get_conn().execute(
                "DELETE FROM dashboard_templates WHERE key=? AND owner_user_id=?",
                (key, user_id))
            self._get_conn().commit()
            return cur.rowcount == 1


# ===== 模块级单例（同 get_subscription_store 惯例） =====
_store: Optional[TemplateStore] = None
_store_lock = threading.Lock()


def get_template_store() -> TemplateStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = TemplateStore()
    return _store


def reset_template_store() -> None:
    """重置单例（测试用）"""
    global _store
    with _store_lock:
        if _store is not None:
            try:
                _store.close()
            except Exception:
                pass
        _store = None
