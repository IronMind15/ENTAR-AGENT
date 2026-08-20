"""sync_mineru 输出目录扫描测试（审查 Critical 3 回归）

覆盖 find_markdown_files 的目录过滤：跳过 -mineru-cache 缓存目录，
不把 MinerU 缓存中间产物当独立文档重复入库。全程不连 Chroma、不联网。
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"

import scripts.sync_mineru as sync_mineru  # noqa: E402


def _make_fake_output(base: Path) -> None:
    """构造一个含 正常输出 / -mineru-cache / .zip 临时目录 的 mineru_output"""
    normal = base / "EN50178.pdf"
    normal.mkdir()
    (normal / "full.md").write_text("# EN50178\n内容", encoding="utf-8")
    (normal / "images").mkdir()

    cache = base / "EN50178.pdf-mineru-cache"
    cache.mkdir()
    (cache / "full.md").write_text("# cache 内容", encoding="utf-8")
    (cache / "cache.json").write_text("{}", encoding="utf-8")

    zip_dir = base / "EN50178.pdf.zip"
    zip_dir.mkdir()
    (zip_dir / "full.md").write_text("解压残留", encoding="utf-8")


class FindMarkdownFilesTests(unittest.TestCase):
    def test_skips_mineru_cache_dirs(self):
        """Critical 3 回归：-mineru-cache 目录含 full.md 也不得作为文档入库"""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _make_fake_output(base)
            with mock.patch.object(sync_mineru, "MINERU_OUTPUT_DIR", base):
                files = sync_mineru.find_markdown_files()
        names = [f["folder_name"] for f in files]
        self.assertEqual(names, ["EN50178.pdf"])
        self.assertFalse(any("mineru-cache" in n for n in names))

    def test_only_full_md_with_images_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _make_fake_output(base)
            with mock.patch.object(sync_mineru, "MINERU_OUTPUT_DIR", base):
                files = sync_mineru.find_markdown_files()
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0]["md_path"].endswith("full.md"))
        self.assertTrue(files[0]["images_dir"].endswith("images"))


if __name__ == "__main__":
    unittest.main()
