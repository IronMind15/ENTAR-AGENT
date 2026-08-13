"""
钉钉通讯录客户端 — 员工查询数据源

供 contact_find 工具调用，负责：
  1. 获取并缓存 access token（旧版 oapi gettoken）
  2. 遍历部门树（钉钉不支持一次拿全部员工：子部门列表 → 逐部门用户，cursor 分页）
  3. 进程内短 TTL 全量缓存（规避钉钉 QPS 限流，不做磁盘持久化）
  4. 姓名/职位/工号/部门匹配 + 服务端敏感字段脱敏（mobile/email）

接口选择说明：
  - 使用旧版 oapi（oapi.dingtalk.com /topapi/v2/...），因为新版 api.dingtalk.com
    /v1.0/contact/ 系列接口在部分企业应用上返回 404（InvalidAction.NotFound）。
  - 旧版 topapi 用 GET /gettoken 拿 token，请求时 access_token 走 URL query、body 走 JSON。
  - 项目里 user_store.sync_user_from_dingtalk 一直用这套旧版接口且稳定可用。

权限说明：
  - 应用需开通「通讯录」读取权限；可见范围须包含全员，否则只能查到范围内的人。
  - mobile 属敏感字段，若需返回手机号，还需通讯录敏感字段读取权限。
"""

import json
import logging
import re
import threading
import time
from collections import deque
from typing import Any

import requests

from config import (
    CONTACT_ADMIN_STAFF_IDS,
    CONTACT_CACHE_TTL_SECONDS,
    DINGTALK_CLIENT_ID,
    DINGTALK_CLIENT_SECRET,
)

logger = logging.getLogger("contact")

OAPI_BASE = "https://oapi.dingtalk.com"

# 遍历硬上限，防部门树异常时失控
MAX_DEPTS = 500
MAX_EMPLOYEES = 50000
# 每次 search 对命中结果做详情补齐的上限（避免大量 HTTP 请求）
_MAX_ENRICH = 5


class ContactPermissionError(RuntimeError):
    """应用未开通通讯录读取权限 / 可见范围不含目标部门时抛出，含友好中文提示。"""


def _split_ids(raw: str) -> list[str]:
    """把逗号/分号/空格分隔的 ID 串拆成去重列表（本地副本，避免拉 knowledge_review 重依赖）"""
    if not raw:
        return []
    seen: list[str] = []
    for part in re.split(r"[,，;；\s]+", raw.strip()):
        part = part.strip()
        if part and part not in seen:
            seen.append(part)
    return seen


def is_contact_admin(staff_id: str) -> bool:
    """判断某员工是否通讯录敏感字段（手机号/邮箱）审核人"""
    return bool(staff_id) and staff_id in _split_ids(CONTACT_ADMIN_STAFF_IDS)


def _g(obj: dict, *keys: str) -> str:
    """按候选键顺序取第一个非空值（防御新旧 API 字段命名差异）"""
    for k in keys:
        v = obj.get(k)
        if v not in (None, ""):
            return str(v)
    return ""


class DingTalkContactClient:
    """线程安全的钉钉通讯录客户端（只读，不写任何数据）"""

    def __init__(self, client_id: str = DINGTALK_CLIENT_ID,
                 client_secret: str = DINGTALK_CLIENT_SECRET,
                 oapi_base: str = OAPI_BASE,
                 session=None,
                 cache_ttl: float = CONTACT_CACHE_TTL_SECONDS):
        self.client_id = client_id
        self.client_secret = client_secret
        self.oapi_base = oapi_base.rstrip("/")
        if session is not None:
            self._session = session
        else:
            # 默认直连 Session：钉钉是国内服务，强制直连，不跟随系统/环境代理（避免 Clash 劫持）
            self._session = requests.Session()
            self._session.trust_env = False
        self.cache_ttl = float(cache_ttl) if cache_ttl else 60.0

        # token 缓存
        self._token = ""
        self._token_expires_at = 0.0
        self._token_lock = threading.Lock()
        # 全量通讯录缓存（进程内，纯 TTL 过期）
        self._dir_employees: list[dict] | None = None
        self._dir_ts = 0.0
        self._dir_last_errors: list[str] = []
        self._dir_lock = threading.Lock()

    # ── token（旧版 oapi gettoken）─────────────────────────
    def _get_access_token(self) -> str:
        """获取并缓存 access token（GET /gettoken），提前 60 秒刷新"""
        if self._token and time.time() < self._token_expires_at - 60:
            return self._token
        if not self.client_id or not self.client_secret:
            raise RuntimeError("钉钉 ClientID/ClientSecret 未配置")
        with self._token_lock:
            if self._token and time.time() < self._token_expires_at - 60:
                return self._token
            try:
                response = self._session.get(
                    f"{self.oapi_base}/gettoken",
                    params={"appkey": self.client_id, "appsecret": self.client_secret},
                    timeout=10,
                )
                response.raise_for_status()
            except requests.RequestException as e:
                raise RuntimeError(f"获取钉钉 access token 失败: {e}") from e
            data = response.json()
            if data.get("errcode") != 0:
                raise RuntimeError(
                    f"获取钉钉 access token 失败: {data.get('errmsg', '返回为空')}"
                )
            token = data.get("access_token", "")
            if not token:
                raise RuntimeError("获取钉钉 access token 失败: 返回为空")
            self._token = token
            self._token_expires_at = time.time() + 7200
            return token

    # ── 基础 POST（旧版 topapi）────────────────────────────
    def _post(self, path: str, body: dict | None = None) -> dict:
        """POST 旧版 topapi 接口：access_token 走 URL query，body 走 JSON

        统一处理：429/5xx 退避重试、403/权限 errcode 转 ContactPermissionError、
        其他错误转 RuntimeError（含友好 errmsg）。
        """
        last_err: Exception | None = None
        for attempt in range(1, 4):  # 最多 3 次
            token = self._get_access_token()
            try:
                response = self._session.post(
                    f"{self.oapi_base}{path}",
                    params={"access_token": token},
                    json=body or {},
                    timeout=15,
                )
            except requests.RequestException as e:
                last_err = e
                time.sleep(0.5 * attempt)
                continue
            if response.status_code == 403:
                raise ContactPermissionError(
                    "应用未开通钉钉通讯录读取权限，请管理员在钉钉开放平台为应用授权通讯录权限，"
                    "并确认应用可见范围包含要查询的部门。"
                )
            if response.status_code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(0.5 * attempt)  # 0.5s / 1s 退避
                continue
            if response.status_code >= 400:
                # 其他 4xx（404 等）不重试，直接报错，避免接口不存在被静默当成"无数据"
                raise RuntimeError(f"钉钉通讯录接口 HTTP {response.status_code}: {path}")
            try:
                data = response.json()
            except ValueError as e:
                raise RuntimeError(
                    f"钉钉通讯录接口返回非 JSON（HTTP {response.status_code}）"
                ) from e
            errcode = data.get("errcode", 0)
            if errcode not in (0, "0", None, ""):
                errmsg = data.get("errmsg") or ""
                if errcode in (88, 50009) or any(
                    k in str(errmsg) for k in ("权限", "permission", "Permission", "Forbidden")
                ):
                    raise ContactPermissionError(
                        "应用未开通钉钉通讯录读取权限，请管理员授权通讯录权限并设置全员可见范围。"
                    )
                raise RuntimeError(f"钉钉通讯录接口错误 {errcode}: {errmsg}")
            return data
        raise RuntimeError(f"钉钉通讯录请求多次失败（{path}）：{last_err}")

    # ── 部门 ───────────────────────────────────────────────
    def list_sub_departments(self, dept_id: int) -> list[dict]:
        """获取子部门列表 → [{"id": int, "name": str}]（旧版 topapi/v2/department/listsub）"""
        data = self._post("/topapi/v2/department/listsub", {"dept_id": dept_id})
        result = data.get("result") or []
        return [
            {
                "id": int(d.get("dept_id") or d.get("id")),
                "name": str(d.get("name") or ""),
            }
            for d in result
        ]

    def get_department_name(self, dept_id: int) -> str:
        """获取部门名称（失败返回空串）"""
        try:
            data = self._post("/topapi/v2/department/get", {"dept_id": dept_id})
            result = data.get("result") or {}
            return str(result.get("name") or "")
        except Exception as e:
            logger.debug(f"部门名获取失败(dept={dept_id}): {e}")
            return ""

    # ── 部门用户（cursor 分页）─────────────────────────────
    def list_department_users(self, dept_id: int) -> list[dict]:
        """获取某部门下全部用户（旧版 topapi/v2/user/list，cursor 分页）"""
        users: list[dict] = []
        cursor = 0
        while True:
            data = self._post(
                "/topapi/v2/user/list",
                {"dept_id": dept_id, "cursor": cursor, "size": 100},
            )
            result = data.get("result") or {}
            batch = result.get("list") or []
            users.extend(batch)
            if not result.get("has_more"):
                break
            next_cursor = result.get("next_cursor")
            # 终止条件：游标为空 / 不前进（防死循环）
            if next_cursor is None or next_cursor == cursor:
                break
            cursor = next_cursor
        return users

    def get_user_detail(self, user_id: str) -> dict:
        """获取单个用户详情（旧版 topapi/v2/user/get，补齐 title/mobile 等字段）"""
        data = self._post("/topapi/v2/user/get", {"userid": user_id})
        return data.get("result") or {}

    # ── 全量遍历 ───────────────────────────────────────────
    def _normalize_user(self, u: dict) -> dict:
        """把用户原始 dict 归一化为统一字段（多键回退兼容新旧 API 命名）"""
        return {
            "userId": _g(u, "userid", "userId", "staffId"),
            "name": _g(u, "name", "nick", "nickName"),
            "title": _g(u, "title", "position", "jobTitle"),
            "jobNumber": _g(u, "job_number", "jobNumber", "employeeCode", "employee_code"),
            "mobile": _g(u, "mobile", "phone"),
            "email": _g(u, "email", "mail"),
            "deptIdList": u.get("dept_id_list") or u.get("deptIdList") or [],
            "dept_names": "",
        }

    def _traverse(self) -> tuple[list[dict], list[str]]:
        """BFS 遍历部门树拉全量员工，返回 (员工列表, 部分失败错误列表)

        钉钉不支持一次拿全部员工：子部门列表 → 逐部门用户（分页）。
        某个部门失败不整体报错，记录 errors 继续；仅根请求/完全无数据时向上抛权限/网络错。
        """
        employees: dict[str, dict] = {}
        dept_names: dict[int, str] = {}
        errors: list[str] = []
        queue = deque([1])  # 钉钉根部门 ID = 1
        visited: set[int] = set()
        dept_calls = 0

        # 根部门名
        root_name = self.get_department_name(1)
        dept_names[1] = root_name or "公司"

        while queue and dept_calls < MAX_DEPTS:
            dept_id = queue.popleft()
            if dept_id in visited:
                continue
            visited.add(dept_id)
            dept_calls += 1

            # ① 子部门（listsub 带部门名）
            try:
                for sub in self.list_sub_departments(dept_id):
                    sub_id = sub["id"]
                    if sub.get("name"):
                        dept_names.setdefault(sub_id, sub["name"])
                    if sub_id not in visited:
                        queue.append(sub_id)
            except Exception as e:
                errors.append(f"子部门(dept={dept_id})获取失败: {e}")

            # ② 部门用户（分页）
            try:
                for u in self.list_department_users(dept_id):
                    uid = _g(u, "userid", "userId", "staffId")
                    if not uid:
                        continue
                    if uid not in employees:
                        employees[uid] = self._normalize_user(u)
                    # 归属部门名：dept_id_list 映射优先，缺名补拉；无列表用当前部门名
                    dept_ids = employees[uid]["deptIdList"] or [dept_id]
                    names = []
                    for did in dept_ids:
                        try:
                            did_int = int(did)
                        except (TypeError, ValueError):
                            continue
                        name = dept_names.get(did_int)
                        if not name:
                            name = self.get_department_name(did_int)
                            if name:
                                dept_names[did_int] = name
                        if name:
                            names.append(name)
                    if names:
                        employees[uid]["dept_names"] = "、".join(
                            dict.fromkeys(names)  # 去重保序
                        )
            except ContactPermissionError:
                if dept_id == 1:
                    raise  # 根部门权限不足直接向上抛，给用户明确的权限提示而非"查不到人"
                errors.append(f"部门用户(dept={dept_id})获取失败: 通讯录权限不足")
                continue
            except Exception as e:
                errors.append(f"部门用户(dept={dept_id})获取失败: {e}")
                continue

            if len(employees) >= MAX_EMPLOYEES:
                errors.append("员工数超上限，已截断")
                break

        return list(employees.values()), errors

    def fetch_all_employees(self, force: bool = False) -> tuple[list[dict], list[str]]:
        """获取全量员工列表（命中 TTL 缓存直接返回），返回 (员工列表, 部分失败错误列表)"""
        with self._dir_lock:
            if (not force and self._dir_employees is not None
                    and time.time() - self._dir_ts < self.cache_ttl):
                return list(self._dir_employees), list(self._dir_last_errors)
            employees, errors = self._traverse()
            # 有数据就刷新缓存（含部分失败场景，避免 TTL 内反复重试同一失败部门）
            if employees:
                self._dir_employees = employees
                self._dir_last_errors = errors
                self._dir_ts = time.time()
            elif errors:
                # 完全没拉到数据且出错：抛错而非返回空（否则会被误判为"查不到人"）
                raise RuntimeError(f"钉钉通讯录获取失败：{errors[0]}")
            return employees, errors

    # ── 匹配与脱敏 ─────────────────────────────────────────
    def _enrich_user(self, u: dict) -> dict:
        """对命中结果补齐 title/jobNumber/mobile 等字段（list 接口不含，只补少量）"""
        if u.get("title") and u.get("jobNumber") and u.get("mobile"):
            return u
        uid = u.get("userId")
        if not uid:
            return u
        try:
            detail = self.get_user_detail(uid)
        except Exception as e:
            logger.debug(f"用户详情补齐失败({uid}): {e}")
            return u
        for key, candidates in (
            ("title", ("title", "position")),
            ("jobNumber", ("job_number", "jobNumber", "employeeCode", "employee_code")),
            ("mobile", ("mobile", "phone")),
            ("email", ("email", "mail")),
            ("name", ("name", "nick")),
        ):
            if not u.get(key):
                v = _g(detail, *candidates)
                if v:
                    u[key] = v
        return u

    def search(self, keyword: str = "", dept_name: str = "",
               userid: str = "", include_sensitive: bool = False,
               limit: int = 10) -> dict:
        """按关键词/部门/钉钉ID查询员工，服务端按身份脱敏。

        返回结构（统一 JSON 友好）：
            {found, results, text, message, sensitive_hidden, warning}
        results 每条含 userId/name/title/jobNumber/dept_names（+ 审核人可见 mobile/email）
        """
        employees, errors = self.fetch_all_employees()
        warning = None
        if errors:
            warning = f"部分部门获取失败，结果可能不完整：{'；'.join(errors[:2])}"

        if userid:
            matches = [e for e in employees if e.get("userId") == userid]
        elif keyword:
            kw = keyword.strip().lower()
            # 姓名/职位/工号/部门名任一命中即可（用户问"研发中心有哪些人"时部门名会当 keyword）
            matches = [
                e for e in employees
                if (kw in e.get("name", "").lower()
                    or kw in e.get("title", "").lower()
                    or kw in e.get("jobNumber", "").lower()
                    or kw in e.get("dept_names", "").lower())
            ]
            if dept_name:
                dn = dept_name.strip().lower()
                matches = [e for e in matches if dn in e.get("dept_names", "").lower()]
            # 排序：姓名精确 > 姓名前缀 > 其余
            def _rank(e):
                n = e.get("name", "").lower()
                if n == kw:
                    return 0
                if n.startswith(kw):
                    return 1
                return 2
            matches.sort(key=_rank)
        else:
            matches = employees

        matches = matches[:limit]

        # 命中项做详情补齐（仅有限条，且只在确实缺字段时）
        for i, e in enumerate(matches[: _MAX_ENRICH]):
            if not e.get("title") or not e.get("jobNumber"):
                matches[i] = self._enrich_user(e)

        # 服务端脱敏：非审核人直接剥离 mobile/email（键不进结果，不依赖客户端）
        if not include_sensitive:
            for e in matches:
                e.pop("mobile", None)
                e.pop("email", None)

        # 组装钉钉友好 text（【】格式，非审核人无手机/邮箱行）
        lines = []
        for i, e in enumerate(matches, 1):
            lines.append(f"【{i}】{e.get('name', '')} ｜ {e.get('dept_names', '')}")
            if e.get("title"):
                lines.append(f"· 职位：{e['title']}")
            if e.get("jobNumber"):
                lines.append(f"· 工号：{e['jobNumber']}")
            if include_sensitive:
                if e.get("mobile"):
                    lines.append(f"· 手机：{e['mobile']}")
                if e.get("email"):
                    lines.append(f"· 邮箱：{e['email']}")
            lines.append("")

        found = bool(matches)
        if found:
            message = f"共找到 {len(matches)} 位"
            if keyword and len(matches) >= limit:
                message += "（结果较多，建议补充部门或更精确的姓名）"
        else:
            message = f"未找到匹配「{keyword or userid}」的员工，请确认姓名/部门或换关键词"

        return {
            "found": found,
            "results": matches,
            "text": "\n".join(lines).strip(),
            "message": message,
            "sensitive_hidden": not include_sensitive,
            "warning": warning,
        }


# ── 模块级单例（双检锁）──────────────────────────────────
_client: DingTalkContactClient | None = None
_client_lock = threading.Lock()


def get_contact_client() -> DingTalkContactClient:
    """获取通讯录客户端单例（懒加载 + 双检锁，多线程安全）"""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = DingTalkContactClient()
    return _client
