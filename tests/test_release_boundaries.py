"""v1.13.2 的路径、打包和自动发现边界回归。"""

from pathlib import Path
import unittest

from scripts import paths, skills, tools


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class RuntimeBoundaryTests(unittest.TestCase):
    def test_tests_use_runtime_db_outside_production_data(self):
        production_db = PROJECT_ROOT / "data" / "user_store.db"
        self.assertTrue(paths.TESTING)
        self.assertEqual(paths.DB_PATH.name, "user_store.db")
        self.assertNotEqual(paths.DB_PATH.resolve(), production_db.resolve())
        self.assertIn("_test_runtime", paths.DB_PATH.parts)
        self.assertIn("_test_runtime", paths.UPLOADS_DIR.parts)

    def test_deployment_package_excludes_runtime_data(self):
        pack_script = (PROJECT_ROOT / "deploy" / "pack.sh").read_text(encoding="utf-8")
        self.assertNotIn("    data/", pack_script)
        self.assertNotIn("    knowledge_base/", pack_script)
        self.assertIn("scripts/", pack_script)
        self.assertTrue((PROJECT_ROOT / "deploy" / "production.env.example").exists())

    def test_tool_and_skill_registries_autoload_only_active_modules(self):
        self.assertEqual(len(tools.get_tool_names()), 10)
        self.assertNotIn("search_standards", tools.get_tool_names())
        self.assertNotIn("search_experience_kb", tools.get_tool_names())
        self.assertEqual(len(skills.get_skill_list()), 5)


class StableEntrypointTests(unittest.TestCase):
    def test_scripts_is_importable_as_package(self):
        import scripts

        self.assertTrue(Path(scripts.__file__).name == "__init__.py")


if __name__ == "__main__":
    unittest.main()
