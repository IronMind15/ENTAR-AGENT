"""「系统设置」配置覆盖测试（v1.13.4）

覆盖：白名单校验 / 留空不覆盖 / 掩码回显 / 原子写回读 / setattr 热更新 /
      get_status 结构 / config 读取优先级（env > admin_config.json > local_config.py）/
      /admin/config 端点鉴权与保存。

测试隔离：overrides_path patch 到临时目录，绝不写生产 data/admin_config.json；
          端点测试 mock _get_admin_password，聚焦业务逻辑本身。
"""

import os
import shutil
import tempfile
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import scripts.admin_config as admin_config


def _mask(value: str) -> str:
    """与实现同口径的掩码断言辅助。"""
    if not value:
        return ""
    if len(value) <= 12:
        return "已设置"
    return f"{value[:6]}……{value[-4:]}"


class AdminConfigUnitTests(unittest.TestCase):
    """存储层：白名单 / 掩码 / 保存 / 状态"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="admin_cfg_unit_")
        self.cfg_file = os.path.join(self.tmp, "admin_config.json")
        self._overrides_path_patch = mock.patch.object(
            admin_config, "overrides_path", return_value=self.cfg_file,
        )
        self._overrides_path_patch.start()

    def tearDown(self):
        self._overrides_path_patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_config_items_completeness(self):
        """白名单 9 项 / 6 组，且每项 key 都已在 config.py 导出（防漏导 tripwire）。"""
        import scripts.config as cfg
        self.assertEqual(len(admin_config.CONFIG_ITEMS), 9)
        self.assertEqual(len(admin_config.GROUPS), 6)
        for key in admin_config.CONFIG_ITEMS:
            self.assertTrue(hasattr(cfg, key), f"config.py 缺少导出 {key}")
        # 每个配置项必须落在已注册的组
        for key, meta in admin_config.CONFIG_ITEMS.items():
            self.assertIn(meta["group"], admin_config.GROUPS, f"{key} 的组未注册")

    def test_overrides_path_isolated_in_test(self):
        """测试环境 overrides 文件落在 _test_runtime 下，不污染生产配置。"""
        self._overrides_path_patch.stop()   # 临时取真实实现，验证 DATA_ROOT 隔离
        try:
            p = admin_config.overrides_path()
        finally:
            self._overrides_path_patch.start()
        self.assertIn("_test_runtime", p)
        self.assertTrue(p.endswith("admin_config.json"))

    def test_mask_value(self):
        self.assertEqual(admin_config.mask_value(""), "")
        self.assertEqual(admin_config.mask_value("abc"), "已设置")
        self.assertEqual(admin_config.mask_value("sk-1234567890abcdef"),
                         "sk-123……cdef")

    def test_save_config_whitelist_and_blank(self):
        """未知 key 静默忽略；留空不覆盖原值。"""
        result = admin_config.save_config({
            "NOT_A_REAL_KEY": "x",
            "LLM_MODEL": "",                      # 留空 → 不覆盖
            "DEEPSEEK_API_KEY": "new-secret-key",
        })
        self.assertEqual(result["updated"], ["DEEPSEEK_API_KEY"])
        data = admin_config.load_overrides()
        self.assertEqual(data.get("DEEPSEEK_API_KEY"), "new-secret-key")
        self.assertNotIn("LLM_MODEL", data)
        self.assertNotIn("NOT_A_REAL_KEY", data)

    def test_save_config_write_and_read_back(self):
        """保存后 load_overrides 能读回（原子写持久化）。"""
        admin_config.save_config({"VISION_MODEL": "qwen-max"})
        data = admin_config.load_overrides()
        self.assertEqual(data.get("VISION_MODEL"), "qwen-max")
        # 再次保存只覆盖指定项，其余保留
        admin_config.save_config({"VISION_MODEL": "qwen-plus"})
        data = admin_config.load_overrides()
        self.assertEqual(data.get("VISION_MODEL"), "qwen-plus")

    def test_save_config_hot_updates_config_module(self):
        """保存后 config 模块属性被 setattr 热更新（ADMIN_PASSWORD 即立即生效机制）。"""
        import scripts.config as cfg
        orig = cfg.LLM_MODEL
        try:
            admin_config.save_config({"LLM_MODEL": "deepseek-r1"})
            self.assertEqual(cfg.LLM_MODEL, "deepseek-r1")
        finally:
            cfg.LLM_MODEL = orig

    def test_save_config_immediate_flag(self):
        """ADMIN_PASSWORD 标注 immediate，其余不标。"""
        import scripts.config as cfg
        orig = cfg.ADMIN_PASSWORD
        try:
            result = admin_config.save_config({"ADMIN_PASSWORD": "newpass123"})
            self.assertEqual(result["immediate"], ["ADMIN_PASSWORD"])
            self.assertEqual(cfg.ADMIN_PASSWORD, "newpass123")
        finally:
            cfg.ADMIN_PASSWORD = orig

    def test_get_status_structure(self):
        """每项返回掩码状态；secret 项不回显明文。"""
        import scripts.config as cfg
        status = admin_config.get_status()
        self.assertEqual(len(status), 9)
        for key, meta in admin_config.CONFIG_ITEMS.items():
            s = status[key]
            self.assertEqual(s["key"], key)
            self.assertEqual(s["group"], meta["group"])
            self.assertEqual(s["label"], meta["label"])
            self.assertEqual(s["secret"], meta["secret"])
            self.assertEqual(s["immediate"], meta.get("immediate", False))
            self.assertIn("set", s)
            self.assertIn("masked", s)
            raw = getattr(cfg, key, "") or ""
            self.assertEqual(s["set"], bool(raw))
            if meta["secret"]:
                # 敏感项 masked 是掩码：与明文不同、且等于同口径掩码
                if raw:
                    self.assertEqual(s["masked"], _mask(raw))
                    self.assertNotEqual(s["masked"], raw)
                else:
                    self.assertEqual(s["masked"], "")
            else:
                self.assertEqual(s["masked"], raw)


class AdminConfigPriorityTests(unittest.TestCase):
    """读取优先级：环境变量 > admin_config.json > local_config.py"""

    def test_env_beats_test_defaults(self):
        import scripts.config as cfg
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "env-key"}):
            self.assertEqual(cfg._get_config("DEEPSEEK_API_KEY"), "env-key")

    def test_overrides_beat_file(self):
        """admin_config.json 覆盖 > local_config.py（临时注入 overrides 验证）。"""
        import scripts.config as cfg
        orig_testing, orig_ov = cfg._TESTING, cfg._OVERRIDES
        try:
            cfg._TESTING = False
            cfg._OVERRIDES = {"LLM_MODEL": "override-model"}
            self.assertEqual(cfg._get_config("LLM_MODEL"), "override-model")
        finally:
            cfg._TESTING = orig_testing
            cfg._OVERRIDES = orig_ov


class AdminConfigApiTests(unittest.TestCase):
    """接口层：GET/POST /admin/config"""

    @classmethod
    def setUpClass(cls):
        import scripts.config as cfg
        import scripts.doc_mgr.router as router
        cls.tmp = tempfile.mkdtemp(prefix="admin_cfg_api_")
        cls.cfg_file = os.path.join(cls.tmp, "admin_config.json")
        # 记录 setattr 可能污染的关键模块属性，tearDownClass 恢复
        cls._cfg_orig = {k: getattr(cfg, k) for k in ("LLM_MODEL", "ADMIN_PASSWORD")}
        cls._patchers = [
            mock.patch.object(admin_config, "overrides_path", return_value=cls.cfg_file),
            mock.patch.object(router, "_get_admin_password", return_value="secret"),
        ]
        for p in cls._patchers:
            p.start()
        app = FastAPI()
        app.include_router(router.router)
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        import scripts.config as cfg
        for k, v in cls._cfg_orig.items():
            setattr(cfg, k, v)
        for p in cls._patchers:
            p.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_get_config_requires_auth(self):
        r = self.client.get("/admin/config")
        self.assertEqual(r.status_code, 401)

    def test_get_config_returns_status(self):
        r = self.client.get("/admin/config", params={"password": "secret"})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("LLM_MODEL", data)
        self.assertIn("DEEPSEEK_API_KEY", data)
        self.assertEqual(len(data), 9)

    def test_post_config_saves(self):
        r = self.client.post("/admin/config", data={
            "LLM_MODEL": "deepseek-r1",
            "password": "secret",
        })
        self.assertEqual(r.status_code, 200)
        j = r.json()
        self.assertTrue(j["ok"])
        self.assertIn("LLM_MODEL", j["updated"])
        data = admin_config.load_overrides()
        self.assertEqual(data.get("LLM_MODEL"), "deepseek-r1")

    def test_post_config_blank_keeps(self):
        """POST 留空不覆盖：updated 不含该 key。"""
        admin_config.save_config({"LLM_MODEL": "keep-model"})
        r = self.client.post("/admin/config", data={
            "LLM_MODEL": "",
            "password": "secret",
        })
        j = r.json()
        self.assertNotIn("LLM_MODEL", j["updated"])


if __name__ == "__main__":
    unittest.main()
