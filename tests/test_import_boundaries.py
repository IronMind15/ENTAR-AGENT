"""导入边界回归测试。

这些测试只读取源码并加载包注册中心，不启动服务、不访问钉钉，也不触碰
生产数据库或上传目录。
"""

import ast
import importlib
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"
ARCHIVED_FILES = {
    SCRIPTS_ROOT / "tools" / "search_standards.py",
    SCRIPTS_ROOT / "tools" / "search_experience_kb.py",
}


def _local_module_names() -> set[str]:
    names = {path.stem for path in SCRIPTS_ROOT.glob("*.py")}
    names.update(path.name for path in SCRIPTS_ROOT.iterdir() if path.is_dir())
    names.discard("__pycache__")
    return names


class ImportBoundaryTests(unittest.TestCase):
    def test_active_source_uses_scripts_namespace(self):
        """活动源码不得把本地模块作为顶层模块导入。"""
        local_names = _local_module_names()
        violations: list[str] = []
        source_files = list(SCRIPTS_ROOT.rglob("*.py"))
        source_files.extend((PROJECT_ROOT / "tests").glob("*.py"))

        for path in source_files:
            if path in ARCHIVED_FILES:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    if node.level or not node.module:
                        continue
                    first = node.module.split(".", 1)[0]
                    if first in local_names:
                        violations.append(f"{path}:{node.lineno}: from {node.module}")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        first = alias.name.split(".", 1)[0]
                        if first in local_names:
                            violations.append(f"{path}:{node.lineno}: import {alias.name}")

        self.assertEqual([], violations, "发现非 scripts.* 的本地导入:\n" + "\n".join(violations))

    def test_core_packages_have_canonical_module_names(self):
        """核心注册中心只能以 scripts.* 名称加载。"""
        paths = importlib.import_module("scripts.paths")
        tools = importlib.import_module("scripts.tools")
        skills = importlib.import_module("scripts.skills")

        self.assertIs(sys.modules["scripts.paths"], paths)
        self.assertIs(sys.modules["scripts.tools"], tools)
        self.assertIs(sys.modules["scripts.skills"], skills)
        for legacy_name in ("paths", "tools", "skills"):
            self.assertNotIn(legacy_name, sys.modules)

    def test_direct_main_entrypoint_has_package_bootstrap(self):
        """直接执行入口必须先补项目根目录，再使用 scripts.* 导入。"""
        source = (SCRIPTS_ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn('if __package__ in {None, ""}', source)
        self.assertIn("from scripts.paths", source)


if __name__ == "__main__":
    unittest.main()
