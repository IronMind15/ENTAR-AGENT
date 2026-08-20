"""按人查看聚合测试（v1.13.1 管理层视图）

/people 接口 + _aggregate_files_by_user：把 sync_status 记录按 upload_user_id
聚合为「每人上传的文件与学习进度」，Web 端砍掉用户层后的管理层视图。

覆盖：正常聚合/状态统计、空 id 沉底、昵称合并、按最近更新倒序、
      路由 password 鉴权、返回结构。
"""

import os
import sys
import unittest
from unittest import mock
from types import SimpleNamespace

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS_DIR = os.path.join(PROJECT_ROOT, "scripts")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from scripts.doc_mgr import router as admin_router


def _rec(uid="u1", name="张三", status="synced", updated="2026-08-14 10:00:00", **kw):
    return {
        "upload_user_id": uid,
        "upload_user_name": name,
        "file_name": kw.get("file_name", "doc.pdf"),
        "file_path": kw.get("file_path", "/tmp/doc.pdf"),
        "target_collection": kw.get("target_collection", "standards"),
        "sync_status": status,
        "error_message": kw.get("error_message", ""),
        "last_synced_at": kw.get("last_synced_at", ""),
        "file_size": kw.get("file_size", 1024),
        "updated_at": updated,
    }


class AggregateTests(unittest.TestCase):
    def test_empty_input_returns_empty(self):
        self.assertEqual(admin_router._aggregate_files_by_user([]), [])

    def test_groups_by_user_and_counts_status(self):
        recs = [
            _rec("u1", "张三", "synced"),
            _rec("u1", "张三", "pending"),
            _rec("u1", "张三", "error"),
            _rec("u2", "李四", "synced"),
        ]
        people = admin_router._aggregate_files_by_user(recs)
        self.assertEqual(len(people), 2)
        by = {p["upload_user_id"]: p for p in people}
        self.assertEqual(by["u1"]["synced"], 1)
        self.assertEqual(by["u1"]["pending"], 1)
        self.assertEqual(by["u1"]["error"], 1)
        self.assertEqual(len(by["u1"]["files"]), 3)
        self.assertEqual(by["u2"]["synced"], 1)

    def test_latest_nonempty_name_wins(self):
        """同一人多条记录昵称只有部分非空时，取最近一条非空昵称"""
        recs = [
            _rec("u1", "", "synced", updated="2026-08-13 10:00:00"),
            _rec("u1", "张三", "pending", updated="2026-08-14 10:00:00"),
        ]
        people = admin_router._aggregate_files_by_user(recs)
        self.assertEqual(people[0]["upload_user_name"], "张三")

    def test_empty_user_id_sinks_to_bottom(self):
        """未记录上传者（管理员直传/旧数据）沉底"""
        recs = [
            _rec("", "", "synced", updated="2026-08-14 10:00:00"),
            _rec("u1", "张三", "synced", updated="2026-08-13 10:00:00"),
        ]
        people = admin_router._aggregate_files_by_user(recs)
        self.assertEqual(people[0]["upload_user_id"], "u1")
        self.assertEqual(people[-1]["upload_user_id"], "")

    def test_sorted_by_last_updated_desc(self):
        recs = [
            _rec("u2", "李四", "synced", updated="2026-08-12 10:00:00"),
            _rec("u1", "张三", "synced", updated="2026-08-14 10:00:00"),
        ]
        people = admin_router._aggregate_files_by_user(recs)
        self.assertEqual([p["upload_user_id"] for p in people], ["u1", "u2"])


class PeopleRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._pw = mock.patch.object(admin_router, "_get_admin_password", return_value="secret")
        cls._pw.start()
        app = FastAPI()
        app.include_router(admin_router.router)
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        cls._pw.stop()

    def test_requires_password(self):
        r = self.client.get("/admin/people")
        self.assertEqual(r.status_code, 401)

    def test_returns_people_structure(self):
        with mock.patch("scripts.doc_mgr.sync_tracker.SyncTracker") as M:
            M.return_value.list_all.return_value = [
                _rec("u1", "张三", "synced"),
            ]
            r = self.client.get("/admin/people?password=secret")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["people"][0]["upload_user_name"], "张三")
        self.assertEqual(data["people"][0]["synced"], 1)

    def test_dashboard_status_returns_actual_execution_state(self):
        sub = SimpleNamespace(
            id=7, title="部门日报", owner_user_id="u1", enabled=True,
            push_hour=9, push_minute=5, last_pushed_at="2026-08-14 09:05:10",
            last_run_at="2026-08-14 09:05:10", last_run_status="failed",
            last_run_stage="push", last_run_reason="推送失败：权限不足",
            last_alert_status="failed",
        )
        with mock.patch("scripts.dashboard.subscription_store.get_subscription_store") as get_store:
            get_store.return_value.list_all.return_value = [sub]
            r = self.client.get("/admin/dashboard-status?password=secret")
        self.assertEqual(r.status_code, 200)
        item = r.json()["subscriptions"][0]
        self.assertEqual(item["last_run_status"], "failed")
        self.assertEqual(item["last_alert_status"], "failed")
        self.assertIn("权限不足", item["last_run_reason"])


if __name__ == "__main__":
    unittest.main()
