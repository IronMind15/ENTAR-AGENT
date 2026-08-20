import json
import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"

from scripts.doc_mgr import engine


class MinerUZipTests(unittest.TestCase):
    def test_safe_zip_extracts_single_full_markdown(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive = Path(temp_dir) / "result.zip"
            target = Path(temp_dir) / "out"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("job/full.md", "# 完整结果")
                zf.writestr("job/images/a.txt", "image")

            result = engine._extract_mineru_result(str(archive), str(target))
            self.assertEqual("# 完整结果", Path(result).read_text(encoding="utf-8"))

    def test_zip_path_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive = Path(temp_dir) / "attack.zip"
            target = Path(temp_dir) / "out"
            escaped = Path(temp_dir) / "escaped.md"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("../escaped.md", "bad")

            with self.assertRaises(ValueError):
                engine._extract_mineru_result(str(archive), str(target))
            self.assertFalse(escaped.exists())

    def test_duplicate_full_markdown_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive = Path(temp_dir) / "duplicate.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("one/full.md", "one")
                zf.writestr("two/full.md", "two")
            with self.assertRaises(ValueError):
                engine._extract_mineru_result(str(archive), str(Path(temp_dir) / "out"))


class MinerUSegmentTests(unittest.TestCase):
    @staticmethod
    def _fake_module(contents, fail_at=None):
        calls = []

        def extract_pdf(chunk_path, output_dir):
            index = len(calls)
            calls.append((chunk_path, output_dir))
            if fail_at is not None and index == fail_at:
                raise RuntimeError("segment failed")
            archive = Path(output_dir) / "result.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("full.md", contents[index])
            return str(archive)

        module = types.SimpleNamespace(
            load_token=lambda: "fake-token",
            extract_pdf=extract_pdf,
        )
        return module, calls

    def test_same_full_md_name_from_each_segment_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source.pdf"
            source.write_bytes(b"fake-pdf")
            parts = []
            for index in range(3):
                part = Path(temp_dir) / f"part-{index + 1}.pdf"
                part.write_bytes(str(index).encode())
                parts.append(str(part))
            module, calls = self._fake_module(["# 第一段", "# 第二段", "# 第三段"])
            output_dir = Path(temp_dir) / "mineru"

            with patch.dict(sys.modules, {"scripts.mineru_extract": module}), \
                    patch.object(engine, "_get_mineru_output_dir", return_value=str(output_dir)), \
                    patch.object(engine, "_split_pdf", return_value=parts), \
                    patch.object(engine, "_get_pdf_page_count", return_value=401):
                result = engine._try_mineru(
                    str(source), "source.pdf", content_hash="hash-new",
                )

            content = Path(result).read_text(encoding="utf-8")
            self.assertEqual(3, len(calls))
            self.assertLess(content.index("第一段"), content.index("第二段"))
            self.assertLess(content.index("第二段"), content.index("第三段"))

    def test_any_failed_segment_rejects_partial_result(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source.pdf"
            source.write_bytes(b"fake-pdf")
            parts = [str(Path(temp_dir) / f"part-{i}.pdf") for i in range(3)]
            for part in parts:
                Path(part).write_bytes(b"part")
            module, _ = self._fake_module(["one", "two", "three"], fail_at=1)

            with patch.dict(sys.modules, {"scripts.mineru_extract": module}), \
                    patch.object(engine, "_get_mineru_output_dir", return_value=str(Path(temp_dir) / "out")), \
                    patch.object(engine, "_split_pdf", return_value=parts), \
                    patch.object(engine, "_get_pdf_page_count", return_value=401):
                result = engine._try_mineru(
                    str(source), "source.pdf", content_hash="hash-new",
                )
            self.assertIsNone(result)

    def test_changed_content_hash_invalidates_same_name_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source.pdf"
            source.write_bytes(b"new-content")
            output_dir = Path(temp_dir) / "mineru"
            cache_dir = output_dir / "source-mineru-cache"
            cache_dir.mkdir(parents=True)
            (cache_dir / "full.md").write_text("old", encoding="utf-8")
            (cache_dir / "cache.json").write_text(
                json.dumps({"content_hash": "old-hash"}), encoding="utf-8",
            )
            module, calls = self._fake_module(["new"])

            with patch.dict(sys.modules, {"scripts.mineru_extract": module}), \
                    patch.object(engine, "_get_mineru_output_dir", return_value=str(output_dir)), \
                    patch.object(engine, "_split_pdf", return_value=[str(source)]):
                result = engine._try_mineru(
                    str(source), "source.pdf", content_hash="new-hash",
                )

            self.assertEqual(1, len(calls))
            self.assertEqual("new", Path(result).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
