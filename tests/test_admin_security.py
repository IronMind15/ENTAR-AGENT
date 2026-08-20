"""管理端安全回归测试（v1.4.2 P1 安全收尾）

覆盖：上传接口安全（路径/文件名净化、大小限制、MIME 双重校验、collection 白名单）
     + stats 看板 XSS 转义（防存储型 XSS）

注：Web 问答 GET→POST 的接口测试见运行期验证命令（main.py 依赖 dingtalk_stream，测试环境无 SDK）。
"""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from fastapi import FastAPI
from fastapi.testclient import TestClient


class UploadSecurityTests(unittest.TestCase):
    """上传接口安全校验"""

    @classmethod
    def setUpClass(cls):
        import doc_mgr.router as router
        cls.router = router
        cls.tmp = tempfile.mkdtemp(prefix="admin_sec_")

        # 用临时目录隔离 + patch 恢复，避免污染真实 data/ 或影响其他测试
        cls._patchers = [
            mock.patch.object(router, "FILE_DIRS", {
                "standards": os.path.join(cls.tmp, "standards"),
                "error_codes": os.path.join(cls.tmp, "fault_codes"),
            }),
            mock.patch.object(router, "UPLOAD_DIR", os.path.join(cls.tmp, "uploads")),
            mock.patch.object(router, "_get_admin_password", return_value="secret"),
        ]
        for p in cls._patchers:
            p.start()

        app = FastAPI()
        app.include_router(router.router)
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        for p in cls._patchers:
            p.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _post(self, fname, content, **data):
        # 带管理员密码，绕过权限检查，专注测安全校验逻辑本身
        data.setdefault("password", "secret")
        return self.client.post(
            "/admin/upload",
            files={"file": (fname, content, "application/octet-stream")},
            data=data,
        )

    def test_accepts_valid_pdf(self):
        """真实 PDF 头 → 接受并入库（文件名含 uuid 前缀 + .pdf）"""
        r = self._post("test.pdf", b"%PDF-1.4 fake content", collection="standards")
        self.assertEqual(r.status_code, 200)
        saved = os.listdir(self.router.FILE_DIRS["standards"])
        self.assertGreaterEqual(len(saved), 1)
        self.assertIn(".pdf", saved[0])

    def test_rejects_fake_pdf_mime(self):
        """扩展名 .pdf 但内容非 PDF 头 → 400（MIME 双重校验）"""
        r = self._post("evil.pdf", b"NOT A PDF AT ALL", collection="standards")
        self.assertEqual(r.status_code, 400)
        self.assertIn("内容与扩展名不符", r.json().get("detail", ""))

    def test_rejects_bad_extension(self):
        """非白名单扩展名 → 400"""
        r = self._post("evil.exe", b"MZ\x90\x00", collection="standards")
        self.assertEqual(r.status_code, 400)

    def test_rejects_unknown_collection(self):
        """collection 白名单：任意字符串 → 400（防路径注入）"""
        r = self._post("a.pdf", b"%PDF-1.4", collection="../../etc")
        self.assertEqual(r.status_code, 400)

    def test_size_limit(self):
        """超过大小上限 → 413"""
        orig = self.router.MAX_UPLOAD_BYTES
        self.router.MAX_UPLOAD_BYTES = 16
        try:
            r = self._post("big.pdf", b"%PDF-1.4" + b"x" * 40)
            self.assertEqual(r.status_code, 413)
        finally:
            self.router.MAX_UPLOAD_BYTES = orig

    def test_filename_path_removed(self):
        """恶意路径文件名被净化（去 .. 与 /）"""
        r = self._post("../../evil.pdf", b"%PDF-1.4", collection="standards")
        self.assertEqual(r.status_code, 200)
        saved = os.listdir(self.router.FILE_DIRS["standards"])
        self.assertGreaterEqual(len(saved), 1)
        for name in saved:
            self.assertNotIn("..", name)
            self.assertNotIn("/", name)

    def test_sanitize_filename_unit(self):
        """文件名净化纯函数：去路径、去反斜杠、限长"""
        self.assertEqual(self.router._sanitize_filename("a/b/c.pdf"), "c.pdf")
        self.assertEqual(self.router._sanitize_filename("..\\..\\x.pdf"), "x.pdf")
        self.assertLessEqual(len(self.router._sanitize_filename("x" * 300 + ".pdf")), 120)

    def test_check_signature_unit(self):
        """MIME 签名纯函数：PDF / xlsx(ZIP) / 旧xls(OLE) 判定"""
        self.assertTrue(self.router._check_file_signature("a.pdf", b"%PDF-1.4"))
        self.assertTrue(self.router._check_file_signature("a.xlsx", b"PK\x03\x04..."))
        self.assertTrue(self.router._check_file_signature("a.xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"))
        self.assertFalse(self.router._check_file_signature("a.pdf", b"MZ...."))


class XSSTests(unittest.TestCase):
    """stats 看板对用户可控字段的转义（防存储型 XSS）"""

    def test_stats_html_escapes_user_fields(self):
        from doc_mgr.router import _build_stats_html
        malicious = "<script>alert(1)</script>"
        stats = {
            "total_users": 1, "active_today": 0, "total_messages": 0,
            "daily_trend": [], "top_users": [{"user_id": malicious, "count": 1}],
        }
        users = [{
            "user_id": malicious,
            "nick": '<img src=x onerror=alert(2)>',
            "title": malicious,
            "department_names": "[]",
            "last_active": malicious,
        }]
        html = _build_stats_html(stats, users, {"x": 1}, "")
        # 页面自身含一个合法的深色模式 JS 块，故只允许出现 1 个未转义的 <script> 标签；
        # 用户注入的 <script>/<img> 必须被转义为 &lt;script&gt;/&lt;img
        self.assertEqual(html.count("<script>"), 1)
        self.assertNotIn("<img", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("&lt;img", html)


class ProductionAdminGuardTests(unittest.TestCase):
    """生产环境不能因漏配变量把 /admin 直接暴露。"""

    def test_empty_password_fails_closed_in_production(self):
        from doc_mgr import router
        import config
        with mock.patch.object(router, "_get_admin_password", return_value=""), \
             mock.patch.object(config, "IS_PRODUCTION", True):
            self.assertFalse(router._verify_admin_access(""))

    def test_empty_password_keeps_local_development_compatibility(self):
        from doc_mgr import router
        import config
        with mock.patch.object(router, "_get_admin_password", return_value=""), \
             mock.patch.object(config, "IS_PRODUCTION", False):
            self.assertTrue(router._verify_admin_access(""))


class AdminSessionTests(unittest.TestCase):
    """管理页面不应把密码留在 URL；登录后接口由 HttpOnly Cookie 鉴权。"""

    def setUp(self):
        from doc_mgr import router
        self.router = router
        self.password = mock.patch.object(router, "_get_admin_password", return_value="secret")
        self.password.start()
        app = FastAPI()
        app.include_router(router.router)
        self.client = TestClient(app)

    def tearDown(self):
        self.password.stop()

    def test_post_login_uses_cookie_without_password_query(self):
        response = self.client.post("/admin/login", data={"password": "secret"},
                                    follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        cookie = response.headers.get("set-cookie", "")
        self.assertIn("entar_admin_session=", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertNotIn("secret", cookie)
        # TestClient 保留 Cookie；后续受保护接口不带 password 也能通过会话认证。
        page = self.client.get("/admin")
        self.assertEqual(page.status_code, 200)
        self.assertNotIn("name='password'", page.text)


if __name__ == "__main__":
    unittest.main()
