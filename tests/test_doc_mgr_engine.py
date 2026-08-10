import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from doc_mgr import engine
from doc_mgr.models import Document


class FakeStore:
    def __init__(self, existing=None):
        self.add_calls = []
        self.existing = existing or {"ids": [], "metadatas": []}

    def get(self, collection, ids=None, where=None):
        return self.existing

    def add(self, collection, ids, documents, metadatas):
        self.add_calls.append({
            "collection": collection,
            "ids": list(ids),
            "documents": list(documents),
            "metadatas": list(metadatas),
        })
        return len(ids)

    def replace_document(self, collection, doc_id, file_name,
                         ids, documents, metadatas, legacy_ids=None):
        self.add_calls.append({
            "collection": collection,
            "doc_id": doc_id,
            "file_name": file_name,
            "ids": list(ids),
            "documents": list(documents),
            "metadatas": list(metadatas),
            "legacy_ids": list(legacy_ids or []),
        })
        return len(ids)


class ProcessFileTests(unittest.TestCase):
    def test_default_collection_is_resolved_before_dispatch(self):
        cases = [
            ("sample.pdf", "_process_pdf", "standards"),
            ("sample.xlsx", "_process_excel", "error_codes"),
            ("sample.md", "_process_markdown", "standards"),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            for file_name, processor_name, expected in cases:
                with self.subTest(file_name=file_name):
                    file_path = Path(temp_dir) / file_name
                    file_path.write_bytes(b"test-content")
                    processor = Mock(return_value=Document(
                        file_name=file_name,
                        file_path=str(file_path),
                        collection=expected,
                        status="done",
                    ))
                    with patch.object(engine, "get_store", return_value=FakeStore()), \
                            patch.object(engine, processor_name, processor), \
                            patch.object(engine, "_record_sync_status") as record:
                        doc = engine.process_file(str(file_path))

                    self.assertEqual(expected, doc.collection)
                    self.assertEqual(expected, processor.call_args.args[4])
                    record.assert_called_once()

    def test_explicit_collection_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "sample.md"
            file_path.write_text("# test", encoding="utf-8")
            processor = Mock(return_value=Document(
                file_name="sample.md", collection="private_docs", status="done",
            ))
            with patch.object(engine, "get_store", return_value=FakeStore()), \
                    patch.object(engine, "_process_markdown", processor), \
                    patch.object(engine, "_record_sync_status"):
                engine.process_file(
                    str(file_path), target_collection="private_docs",
                )
            self.assertEqual("private_docs", processor.call_args.args[4])

    def test_processing_exception_still_records_error_status(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "sample.md"
            file_path.write_text("# test", encoding="utf-8")
            with patch.object(engine, "get_store", return_value=FakeStore()), \
                    patch.object(engine, "_process_markdown", side_effect=RuntimeError("boom")), \
                    patch.object(engine, "_record_sync_status") as record:
                doc = engine.process_file(str(file_path))

            self.assertEqual("error", doc.status)
            self.assertIn("boom", doc.message)
            record.assert_called_once()
            self.assertIs(record.call_args.args[4], doc)


class ExcelIdentityTests(unittest.TestCase):
    @staticmethod
    def _records():
        return [{
            "_row_num": 51,
            "_sheet_name": "遥信（DI）",
            "fault_code": "d4-1",
            "name": "急停告警",
            "description": "1-告警",
            "cause": "外部急停信号闭合",
            "notes": "",
        }]

    def test_same_row_in_two_workbooks_has_different_ids(self):
        first = FakeStore()
        second = FakeStore()
        with patch.object(engine, "extract_excel_rows", side_effect=lambda _: self._records()):
            engine._process_excel(
                "first.xlsx", "first.xlsx", 10, first, "custom_errors",
                doc_id="doc-one", content_hash="hash-one",
            )
            engine._process_excel(
                "second.xlsx", "second.xlsx", 10, second, "custom_errors",
                doc_id="doc-two", content_hash="hash-two",
            )

        first_call = first.add_calls[0]
        second_call = second.add_calls[0]
        self.assertEqual("custom_errors", first_call["collection"])
        self.assertTrue(set(first_call["ids"]).isdisjoint(second_call["ids"]))
        self.assertEqual("first.xlsx", first_call["metadatas"][0]["file_name"])
        self.assertEqual("doc-one", first_call["metadatas"][0]["doc_id"])

    def test_matching_legacy_row_id_is_reused_without_duplication(self):
        store = FakeStore(existing={
            "ids": ["row_51"],
            "metadatas": [{"fault_code": "d4-1", "name": "急停告警"}],
        })
        with patch.object(engine, "extract_excel_rows", return_value=self._records()):
            engine._process_excel(
                "first.xlsx", "first.xlsx", 10, store, "error_codes",
                doc_id="doc-one", content_hash="hash-one",
            )
        self.assertEqual(["row_51"], store.add_calls[0]["legacy_ids"])
        self.assertNotEqual(["row_51"], store.add_calls[0]["ids"])


class PdfSplitTests(unittest.TestCase):
    @unittest.skipUnless(
        __import__("importlib").util.find_spec("fitz") is not None,
        "PyMuPDF 未安装",
    )
    def test_401_page_pdf_is_split_into_three_ordered_parts(self):
        import fitz

        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source.pdf"
            output = Path(temp_dir) / "parts"
            output.mkdir()
            doc = fitz.open()
            for _ in range(401):
                doc.new_page()
            doc.save(source)
            doc.close()

            parts = engine._split_pdf(str(source), str(output), max_pages=200)
            self.assertEqual(3, len(parts))
            self.assertEqual([200, 200, 1], [engine._get_pdf_page_count(p) for p in parts])
            self.assertTrue(parts[0].endswith("_p1of3.pdf"))
            self.assertTrue(parts[2].endswith("_p3of3.pdf"))


class ProcessTextTests(unittest.TestCase):
    """v1.11.0 文本直入：engine.process_text 切块入库（钉钉文档「帮我学习」）"""

    def test_process_text_indexes_markdown(self):
        store = FakeStore()
        with patch.object(engine, "get_store", return_value=store):
            doc = engine.process_text("# 在线文档\n\n这是一段内容", file_name="在线文档.md")
        self.assertEqual(doc.status, "done")
        self.assertGreaterEqual(doc.chunk_count, 1)
        self.assertEqual(doc.source, "markdown")
        self.assertTrue(store.add_calls)
        call = store.add_calls[-1]
        self.assertEqual(call["file_name"], "在线文档.md")
        self.assertTrue(call["documents"])
        # 每个切块带 department / doc_id 元数据
        self.assertTrue(all(m.get("department") == "public" for m in call["metadatas"]))

    def test_process_text_empty_returns_error(self):
        with patch.object(engine, "get_store", return_value=FakeStore()):
            doc = engine.process_text("   ", file_name="空.md")
        self.assertEqual(doc.status, "error")

    def test_process_text_doc_id_stable_for_same_content(self):
        store = FakeStore()
        with patch.object(engine, "get_store", return_value=store):
            d1 = engine.process_text("内容A", file_name="x.md")
            d2 = engine.process_text("内容A", file_name="x.md")
        # 相同内容 → 相同 doc_id / content_hash（版本替换语义）
        self.assertEqual(d1.doc_id, d2.doc_id)
        self.assertEqual(d1.content_hash, d2.content_hash)
        self.assertNotEqual(d1.content_hash, "")

    def test_process_text_target_collection_respected(self):
        store = FakeStore()
        with patch.object(engine, "get_store", return_value=store):
            doc = engine.process_text("# 标题", file_name="x.md",
                                      target_collection="private_docs")
        self.assertEqual(doc.collection, "private_docs")
        self.assertEqual(store.add_calls[-1]["collection"], "private_docs")


if __name__ == "__main__":
    unittest.main()
