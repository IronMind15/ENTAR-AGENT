"""
contact_find / 钉钉通讯录客户端测试

覆盖：token 缓存、部门树 BFS、多部门去重、分页、匹配、敏感字段脱敏、
contextvar 身份传递、部分失败降级、权限错误、缓存 TTL、无结果、工具注册。

沿用 test_knowledge_review.py 的 _FakeSession 注入模式（本文件用旧版 oapi topapi 路由）。
"""

import json
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from contact_api import DingTalkContactClient, ContactPermissionError  # noqa: E402
from tools.contact_find import execute  # noqa: E402


# ── 测试数据（旧版 oapi 字段名）──────────────────────────
DEPT_NAMES = {1: "恩特能源", 2: "研发中心", 21: "硬件部", 3: "采购部"}
SUB_MAP = {
    1: [{"dept_id": 2, "name": "研发中心"}, {"dept_id": 3, "name": "采购部"}],
    2: [{"dept_id": 21, "name": "硬件部"}],
    21: [],
    3: [],
}

U_WANG = {"userid": "u1", "name": "王工", "title": "硬件工程师", "job_number": "E1024",
          "mobile": "13800000001", "email": "wang@example.com", "dept_id_list": [21]}
U_LI = {"userid": "u2", "name": "李工", "title": "测试工程师", "job_number": "E1025", "dept_id_list": [21]}
U_WANGFANG = {"userid": "u3", "name": "王芳", "title": "软件工程师", "job_number": "E1026", "dept_id_list": [2]}
U_ZHANG = {"userid": "u4", "name": "张伟", "title": "采购经理", "job_number": "E1027",
           "mobile": "13800000004", "email": "zhang@example.com", "dept_id_list": [3]}
U_WANGLEI = {"userid": "u5", "name": "王磊", "title": "项目经理", "job_number": "E1028", "dept_id_list": [2, 3]}

# 王磊同时挂在研发中心(2)和采购部(3)，用于多部门去重合并
DEPT_USERS = {
    1: [],
    2: [U_WANGFANG, U_WANGLEI],
    21: [U_WANG, U_LI],
    3: [U_ZHANG, U_WANGLEI],
}


class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _FakeSession:
    """按 URL 路由返回假钉钉响应（旧版 oapi topapi）"""

    def __init__(self, dept_names=None, sub_map=None, dept_users=None,
                 fail_dept=None, forbidden_dept=None):
        self.calls = []
        self.dept_names = dept_names or {}
        self.sub_map = sub_map or {}
        self.dept_users = dept_users or {}
        self.user_details = {}
        self.fail_dept = fail_dept            # 该部门返回普通错误（部分失败）
        self.forbidden_dept = forbidden_dept  # 该部门返回权限错误

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        if url.endswith("/gettoken"):
            return _FakeResponse({"errcode": 0, "access_token": "token-1"})
        return _FakeResponse({"errcode": 0})

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        body = kwargs.get("json") or {}
        if url.endswith("/gettoken"):
            return _FakeResponse({"errcode": 0, "access_token": "token-1"})
        if "/topapi/v2/department/listsub" in url:
            return _FakeResponse({"errcode": 0, "result": self.sub_map.get(body["dept_id"], [])})
        if "/topapi/v2/user/list" in url:
            dept_id = body["dept_id"]
            if dept_id == self.forbidden_dept:
                return _FakeResponse({"errcode": 88, "errmsg": "无权限"})
            if dept_id == self.fail_dept:
                return _FakeResponse({"errcode": 40010, "errmsg": "参数错误"})
            spec = self.dept_users.get(dept_id, [])
            pages = spec if spec and isinstance(spec[0], list) else [spec]
            cursor = body.get("cursor", 0)
            if cursor >= len(pages):
                return _FakeResponse({"errcode": 0, "result": {"list": [], "has_more": False}})
            batch = pages[cursor]
            has_more = cursor + 1 < len(pages)
            next_cursor = cursor + 1 if has_more else 0
            return _FakeResponse({"errcode": 0, "result": {
                "list": batch, "has_more": has_more, "next_cursor": next_cursor}})
        if "/topapi/v2/department/get" in url:
            return _FakeResponse({"errcode": 0, "result": {"name": self.dept_names.get(body["dept_id"], "")}})
        if "/topapi/v2/user/get" in url:
            return _FakeResponse({"errcode": 0, "result": self.user_details.get(body["userid"], {})})
        return _FakeResponse({"errcode": 0})


class _LoopSession:
    """user/list 游标不前进的会话，验证分页死循环防护"""

    def __init__(self):
        self.list_calls = 0

    def get(self, url, **kwargs):
        if url.endswith("/gettoken"):
            return _FakeResponse({"errcode": 0, "access_token": "token-1"})
        return _FakeResponse({"errcode": 0})

    def post(self, url, **kwargs):
        body = kwargs.get("json") or {}
        if url.endswith("/gettoken"):
            return _FakeResponse({"errcode": 0, "access_token": "token-1"})
        if "/topapi/v2/user/list" in url:
            self.list_calls += 1
            if body.get("cursor", 0) > 0:
                return _FakeResponse({"errcode": 0, "result": {
                    "list": [], "has_more": True, "next_cursor": 1}})
            return _FakeResponse({"errcode": 0, "result": {
                "list": [U_WANG], "has_more": True, "next_cursor": 1}})
        if "/topapi/v2/department/listsub" in url:
            return _FakeResponse({"errcode": 0, "result": []})
        if "/topapi/v2/department/get" in url:
            return _FakeResponse({"errcode": 0, "result": {"name": "公司"}})
        return _FakeResponse({"errcode": 0})


def _client(session=None, ttl=3600):
    return DingTalkContactClient(
        client_id="cid", client_secret="secret",
        oapi_base="https://oapi.dingtalk.test",
        session=session or _FakeSession(DEPT_NAMES, SUB_MAP, DEPT_USERS),
        cache_ttl=ttl,
    )


class ContactClientTests(unittest.TestCase):

    def test_token_cached(self):
        session = _FakeSession(DEPT_NAMES, SUB_MAP, DEPT_USERS)
        client = _client(session)
        client.fetch_all_employees()
        client.fetch_all_employees()  # 缓存命中
        tokens = [c for c in session.calls if c[0] == "GET" and c[1].endswith("/gettoken")]
        self.assertEqual(1, len(tokens))  # token 只获取一次

    def test_traverse_bfs_and_dept_names(self):
        client = _client()
        employees, errors = client.fetch_all_employees(force=True)
        self.assertEqual([], errors)
        names = {e["userId"]: e["name"] for e in employees}
        self.assertEqual({"u1", "u2", "u3", "u4", "u5"}, set(names))
        by_id = {e["userId"]: e for e in employees}
        self.assertEqual("硬件部", by_id["u1"]["dept_names"])
        self.assertEqual("研发中心", by_id["u3"]["dept_names"])

    def test_multi_dept_dedup(self):
        client = _client()
        employees, _ = client.fetch_all_employees(force=True)
        by_id = {e["userId"]: e for e in employees}
        self.assertIn("u5", by_id)  # 王磊跨研发中心+采购部，只出现一次
        self.assertEqual("研发中心、采购部", by_id["u5"]["dept_names"])

    def test_pagination(self):
        session = _FakeSession(
            dept_names={1: "公司", 2: "研发"},
            sub_map={1: [{"dept_id": 2, "name": "研发"}]},
            dept_users={2: [[U_WANGFANG], [U_LI]]},  # 两页
        )
        client = _client(session)
        employees, errors = client.fetch_all_employees(force=True)
        self.assertEqual([], errors)
        self.assertEqual(2, len(employees))

    def test_pagination_no_progress_terminates(self):
        client = DingTalkContactClient(
            client_id="c", client_secret="s",
            oapi_base="https://oapi.dingtalk.test",
            session=_LoopSession(), cache_ttl=3600,
        )
        users = client.list_department_users(2)
        self.assertEqual(1, len(users))  # 不死循环
        self.assertLessEqual(client._session.list_calls, 3)

    def test_search_by_name_and_title(self):
        client = _client()
        out = client.search(keyword="王工")
        self.assertTrue(out["found"])
        self.assertEqual(["u1"], [e["userId"] for e in out["results"]])
        out2 = client.search(keyword="采购")
        self.assertIn("u4", [e["userId"] for e in out2["results"]])

    def test_search_dept_keyword(self):
        # 用户问"研发中心有哪些人"，部门名当 keyword 也能命中
        client = _client()
        out = client.search(keyword="研发中心")
        ids = {e["userId"] for e in out["results"]}
        self.assertEqual({"u3", "u5"}, ids)

    def test_search_dept_filter_and_userid(self):
        client = _client()
        out = client.search(keyword="王", dept_name="研发中心")
        ids = {e["userId"] for e in out["results"]}
        self.assertEqual({"u3", "u5"}, ids)
        out2 = client.search(userid="u1")
        self.assertEqual(["u1"], [e["userId"] for e in out2["results"]])

    def test_sensitive_admin_sees_mobile(self):
        client = _client()
        out = client.search(keyword="张伟", include_sensitive=True)
        self.assertEqual("13800000004", out["results"][0]["mobile"])
        self.assertFalse(out["sensitive_hidden"])

    def test_sensitive_non_admin_hidden(self):
        client = _client()
        out = client.search(keyword="张伟", include_sensitive=False)
        self.assertNotIn("mobile", out["results"][0])
        self.assertNotIn("email", out["results"][0])
        self.assertTrue(out["sensitive_hidden"])
        # 格式化 text 也不含手机号
        self.assertNotIn("手机", out["text"])

    def test_partial_failure_degrades(self):
        session = _FakeSession(DEPT_NAMES, SUB_MAP, DEPT_USERS, fail_dept=3)
        client = _client(session)
        out = client.search(keyword="王")
        self.assertTrue(out["found"])       # 部分部门失败不整体报错
        self.assertIsNotNone(out["warning"])
        self.assertTrue("失败" in out["warning"])

    def test_root_permission_error_raises(self):
        session = _FakeSession(DEPT_NAMES, SUB_MAP, DEPT_USERS, forbidden_dept=1)
        client = _client(session)
        with self.assertRaises(ContactPermissionError):
            client.search(keyword="王")

    def test_cache_hit_ttl_and_force(self):
        session = _FakeSession(DEPT_NAMES, SUB_MAP, DEPT_USERS)
        client = _client(session, ttl=3600)
        client.fetch_all_employees()
        def _traverse_posts():
            return [c for c in session.calls
                    if c[0] == "POST" and not c[1].endswith("/gettoken")]
        after_first = len(_traverse_posts())
        client.fetch_all_employees()  # TTL 内命中缓存
        self.assertEqual(after_first, len(_traverse_posts()))
        client.fetch_all_employees(force=True)  # force 重拉
        self.assertGreater(len(_traverse_posts()), after_first)

    def test_cache_expire_by_ttl(self):
        session = _FakeSession(DEPT_NAMES, SUB_MAP, DEPT_USERS)
        client = _client(session, ttl=0.01)
        client.fetch_all_employees()
        def _traverse_posts():
            return [c for c in session.calls
                    if c[0] == "POST" and not c[1].endswith("/gettoken")]
        first = len(_traverse_posts())
        time.sleep(0.02)
        client.fetch_all_employees()  # 过期重拉（token 未过期，遍历请求翻倍）
        self.assertGreater(len(_traverse_posts()), first)

    def test_no_result(self):
        client = _client()
        out = client.search(keyword="不存在的人")
        self.assertFalse(out["found"])
        self.assertEqual([], out["results"])
        self.assertIn("未找到", out["message"])


class FindEmployeeToolTests(unittest.TestCase):

    def _normal_client(self):
        session = _FakeSession(DEPT_NAMES, SUB_MAP, DEPT_USERS)
        return DingTalkContactClient(
            client_id="cid", client_secret="secret",
            oapi_base="https://oapi.dingtalk.test", session=session, cache_ttl=3600,
        )

    def test_tool_registered(self):
        from tools import get_tool_names, get_tool_definitions
        self.assertIn("contact_find", get_tool_names())
        defs = get_tool_definitions()
        self.assertTrue(any(d["function"]["name"] == "contact_find" for d in defs))

    def test_execute_invalid_args(self):
        out = json.loads(execute({}))
        self.assertIn("error", out)

    def test_execute_contextvar_staff_id_sensitive(self):
        # 审核人（CONTACT_ADMIN_STAFF_IDS 含 A01）→ 返回手机号
        from tools import set_current_staff_id
        client = self._normal_client()
        with mock.patch("contact_api.get_contact_client", return_value=client), \
                mock.patch("contact_api.CONTACT_ADMIN_STAFF_IDS", "A01"):
            set_current_staff_id("A01")
            out = json.loads(execute({"keyword": "张伟"}))
            self.assertTrue(out["found"])
            self.assertEqual("13800000004", out["results"][0]["mobile"])
            # 非审核人 → 无手机号
            set_current_staff_id("")
            out2 = json.loads(execute({"keyword": "张伟"}))
            self.assertNotIn("mobile", out2["results"][0])
            self.assertTrue(out2["sensitive_hidden"])

    def test_execute_permission_error_friendly(self):
        from tools import set_current_staff_id
        session = _FakeSession(DEPT_NAMES, SUB_MAP, DEPT_USERS, forbidden_dept=1)
        client = DingTalkContactClient(
            client_id="cid", client_secret="secret",
            oapi_base="https://oapi.dingtalk.test", session=session, cache_ttl=3600,
        )
        set_current_staff_id("")
        with mock.patch("contact_api.get_contact_client", return_value=client):
            out = json.loads(execute({"keyword": "王"}))
        self.assertIn("error", out)
        self.assertIn("权限", out["error"])  # 明确提示是权限问题而非查不到人


if __name__ == "__main__":
    unittest.main()
