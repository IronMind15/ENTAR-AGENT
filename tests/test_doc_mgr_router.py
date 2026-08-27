"""doc_mgr 管理端路由回归测试（v1.13.4 修复 /admin/sync-files 500）

回归：list_sync_files（f582921a 引入）中 `"path": rel_dir` 引用从未定义的变量
`rel_dir` → NameError → 页面 500。修复后补真实目录绝对路径；前端暂不读取
path 字段，测试守护「不再抛异常 + directories 结构完整」。
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from scripts.doc_mgr.router import list_sync_files


class SyncFilesListingTests(unittest.TestCase):
    """/admin/sync-files 同步文件列表——回归 rel_dir NameError"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="sync_files_test_")
        uploads = os.path.join(self._tmp, "uploads")
        os.makedirs(uploads, exist_ok=True)
        with open(os.path.join(uploads, "test.md"), "w", encoding="utf-8") as f:
            f.write("# test")
        # 其余扫描目录指向各自空子目录，避免 os.walk 互相串文件
        for name in ("standards", "fault_codes", "experience"):
            os.makedirs(os.path.join(self._tmp, name), exist_ok=True)
        # 隔离：管理员校验放行、目录常量指向临时目录、同步追踪器空数据
        tracker = mock.MagicMock()
        tracker.list_all.return_value = []
        self._patches = [
            mock.patch("scripts.doc_mgr.router._verify_admin_access", return_value=True),
            mock.patch("scripts.doc_mgr.router.UPLOAD_DIR", uploads),
            mock.patch("scripts.doc_mgr.router.STANDARDS_DIR",
                       os.path.join(self._tmp, "standards")),
            mock.patch("scripts.doc_mgr.router.FAULT_CODES_DIR",
                       os.path.join(self._tmp, "fault_codes")),
            mock.patch("scripts.doc_mgr.router.EXPERIENCE_DIR",
                       os.path.join(self._tmp, "experience")),
            mock.patch("scripts.doc_mgr.sync_tracker.SyncTracker", return_value=tracker),
        ]
        for p in self._patches:
            p.start()
        self.addCleanup(mock.patch.stopall)
        self.addCleanup(shutil.rmtree, self._tmp, True)

    def test_sync_files_no_longer_crashes_on_undefined_rel_dir(self):
        """回归：修复前 NameError→500；修复后返回完整 directories 结构"""
        resp = list_sync_files("pw")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.body.decode("utf-8"))
        self.assertIn("directories", data)
        uploads = data["directories"].get("uploads")
        self.assertIsNotNone(uploads)
        # path 字段存在且为真实目录绝对路径（修复前引用未定义变量）
        self.assertTrue(uploads["path"])
        self.assertEqual(os.path.basename(uploads["path"]), "uploads")
        # 目录下文件正常列出
        self.assertEqual(len(uploads["files"]), 1)
        self.assertEqual(uploads["files"][0]["file_name"], "test.md")

    def test_sync_files_lists_all_four_scan_dirs(self):
        """四个扫描目录都应出现在响应中（uploads/standards/fault_codes/experience）"""
        resp = list_sync_files("pw")
        data = json.loads(resp.body.decode("utf-8"))
        self.assertEqual(
            set(data["directories"].keys()),
            {"uploads", "standards", "fault_codes", "experience"},
        )


if __name__ == "__main__":
    unittest.main()
