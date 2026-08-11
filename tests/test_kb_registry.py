"""知识库注册表测试（v1.11.5 多知识库改造）—— CRUD / 种子 / 可见性 / 创建工具"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from kb_registry import KBRegistry  # noqa: E402


class _RegistryBase(unittest.TestCase):
    """临时 DB 的注册表实例（不碰真实 data/user_store.db）"""

    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._db_path = path
        self.reg = KBRegistry(db_path=path)
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        try:
            self.reg.close()
        except Exception:
            pass
        for suffix in ("", "-wal", "-shm"):
            p = self._db_path + suffix
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass


class RegistryTests(_RegistryBase):
    """注册表 CRUD + 种子 + 可见性"""

    def test_seed_defaults(self):
        kbs = {kb["key"]: kb for kb in self.reg.list_knowledge_bases()}
        self.assertIn("error_codes", kbs)
        self.assertIn("standards", kbs)
        self.assertIn("experience_kb", kbs)
        self.assertEqual(kbs["standards"]["name"], "标准知识库")
        self.assertEqual(kbs["standards"]["department"], "public")
        self.assertEqual(kbs["standards"]["collection"], "standards")

    def test_seed_idempotent(self):
        self.reg.seed_defaults()
        self.reg.seed_defaults()
        self.assertEqual(len(self.reg.list_knowledge_bases()), 3)

    def test_create_and_get(self):
        res = self.reg.create_knowledge_base("产品手册", "产品说明书", department="rd")
        self.assertTrue(res["ok"])
        kb = self.reg.get_knowledge_base(res["kb"]["key"])
        self.assertEqual(kb["name"], "产品手册")
        self.assertEqual(kb["department"], "rd")
        self.assertEqual(kb["collection"], "产品手册")  # key = 中文名去空格
        self.assertEqual(kb["enabled"], 1)

    def test_create_duplicate_rejected(self):
        self.reg.create_knowledge_base("产品手册")
        res = self.reg.create_knowledge_base("产品手册")
        self.assertFalse(res["ok"])
        self.assertIn("已存在", res["message"])

    def test_create_empty_name_rejected(self):
        res = self.reg.create_knowledge_base("   ")
        self.assertFalse(res["ok"])
        self.assertIn("不能为空", res["message"])

    def test_create_invalid_department(self):
        res = self.reg.create_knowledge_base("测试库", department="mars")
        self.assertFalse(res["ok"])
        self.assertIn("不合法", res["message"])

    def test_update_disable(self):
        kb = self.reg.create_knowledge_base("临时库")["kb"]
        updated = self.reg.update_knowledge_base(kb["key"], enabled=0)
        self.assertEqual(updated["enabled"], 0)
        # 禁用后 enabled_only 列表不含它
        keys = [k["key"] for k in self.reg.list_knowledge_bases(enabled_only=True)]
        self.assertNotIn(kb["key"], keys)

    def test_delete(self):
        kb = self.reg.create_knowledge_base("待删除库")["kb"]
        self.assertTrue(self.reg.delete_knowledge_base(kb["key"]))
        self.assertIsNone(self.reg.get_knowledge_base(kb["key"]))

    def test_resolve_by_key_and_name(self):
        self.reg.create_knowledge_base("产品手册")
        self.assertEqual(self.reg.resolve_kb("产品手册")["key"], "产品手册")
        # name 双向包含：用完整名查、用简称查
        self.assertIsNotNone(self.reg.resolve_kb("产品手册"))
        self.assertIsNone(self.reg.resolve_kb("不存在的库"))

    def test_get_visible_filters_by_centers(self):
        self.reg.create_knowledge_base("研发库", department="rd")
        # 用户中心 rd → 看到 public + rd
        keys = {kb["key"] for kb in self.reg.get_visible_knowledge_bases(["rd"])}
        self.assertIn("standards", keys)
        self.assertIn("研发库", keys)
        # 用户中心 bz → 看不到 rd 库（部门权限预留）
        keys2 = {kb["key"] for kb in self.reg.get_visible_knowledge_bases(["bz"])}
        self.assertNotIn("研发库", keys2)

    def test_get_visible_none_returns_all(self):
        keys = {kb["key"] for kb in self.reg.get_visible_knowledge_bases(None)}
        self.assertEqual(keys, {"error_codes", "standards", "experience_kb"})


class CreateToolTests(_RegistryBase):
    """create_knowledge_base 工具（mock 注册表单例）"""

    def setUp(self):
        super().setUp()
        self.patch = mock.patch("kb_registry.get_registry", return_value=self.reg)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_tool_registered(self):
        from tools import get_tool_names
        self.assertIn("create_knowledge_base", get_tool_names())

    def test_create_ok(self):
        from tools.create_knowledge_base import execute
        r = json.loads(execute({
            "name": "产品手册", "description": "产品说明书", "department": "rd"}))
        self.assertTrue(r["created"])
        self.assertEqual(r["kb"]["department"], "rd")
        # 提示文案含后续用法（学到XX入库）
        self.assertIn("学到", r["message"])

    def test_empty_name(self):
        from tools.create_knowledge_base import execute
        r = json.loads(execute({"name": "  "}))
        self.assertIn("error", r)

    def test_invalid_department(self):
        from tools.create_knowledge_base import execute
        r = json.loads(execute({"name": "坏库", "department": "mars"}))
        self.assertIn("error", r)
        self.assertIn("不合法", r["error"])

    def test_duplicate(self):
        from tools.create_knowledge_base import execute
        execute({"name": "重复库"})
        r = json.loads(execute({"name": "重复库"}))
        self.assertIn("error", r)
        self.assertIn("已存在", r["error"])


if __name__ == "__main__":
    unittest.main()
