"""Web 用户层移除 tripwire（v1.13.0）

/ask（Web 问答）、/feedback（Web 反馈）路由已物理删除，/ 改为跳转 /admin，
普通用户只用钉钉——Web 端 /ask 的 user 参数（用户名伪造攻击面）随之消失。

直接读 main.py 源码断言，防止未来从 git history 恢复旧路由而重新暴露
Web 用户层（审查报告 Critical C1：Web 用户名伪造）。
"""

import os
import unittest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class WebUserLayerRemovedTests(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(PROJECT_ROOT, "scripts", "main.py"),
                  encoding="utf-8") as f:
            self.src = f.read()

    def test_ask_route_removed(self):
        self.assertNotIn("@app.post(\"/ask\")", self.src)

    def test_feedback_route_removed(self):
        self.assertNotIn("@app.post(\"/feedback\")", self.src)

    def test_root_redirects_to_admin(self):
        self.assertIn('RedirectResponse(url="/admin"', self.src)

    def test_web_page_not_imported(self):
        self.assertNotIn("from web_page import HOME_HTML", self.src)


if __name__ == "__main__":
    unittest.main()
