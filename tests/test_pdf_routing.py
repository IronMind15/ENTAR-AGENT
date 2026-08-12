"""v1.10.3 PDF 检测路由测试

覆盖：
  - classify_pdf_type：文字版/扫描版/混合版/水印伪文字层 分类
  - validate_local_text：正常文本通过、乱码拒绝、字符骤减拒绝
  - 引擎 _process_pdf 路由：auto 下文字版本地优先（MinerU 不被调用）、
    扫描/混合走 MinerU、本地失败/质量差回退 MinerU、mineru 模式全走 MinerU

全部离线执行（不联网、不烧 MinerU 额度），PDF 用 PyMuPDF 现场合成。
"""
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from doc_mgr import engine
from doc_mgr.extractors.pdf_mupdf import classify_pdf_type, validate_local_text
from doc_mgr.models import Document


# 长文本：单页 >50 字符，足够判定为「文字页」
_PAGE_TEXT = (
    "This is a standard document about power converter insulation coordination "
    "for low voltage systems. Clause 5 defines the clearances and creepage distances. "
    "Clause 6 specifies the testing procedures for verification."
)

_GARBLED = "����\x00\x01\x02\x03abc"  # 替换符/控制字符


def _make_pdf(path: str, page_texts: list[str]) -> str:
    """用 PyMuPDF 合成 PDF：每页按 page_texts 插入文本（None/空=空白页）"""
    import fitz
    doc = fitz.open()
    for text in page_texts:
        page = doc.new_page()
        if text:
            page.insert_text((72, 72), text, fontsize=10)
    doc.save(path)
    doc.close()
    return path


def _make_text_pdf(path: str, pages: int = 10) -> str:
    return _make_pdf(path, [_PAGE_TEXT] * pages)


def _make_scanned_pdf(path: str, pages: int = 10) -> str:
    return _make_pdf(path, [""] * pages)


def _make_mixed_pdf(path: str, text_pages: int = 5, blank_pages: int = 5) -> str:
    return _make_pdf(path, [_PAGE_TEXT] * text_pages + [""] * blank_pages)


def _make_watermark_pdf(path: str, pages: int = 10) -> str:
    # 每页只有「第 x 页」级零散文字（<50 字符），模拟水印凑字的伪文字层
    return _make_pdf(path, [f"第 {i} 页 内部资料" for i in range(pages)])


class FakeStore:
    """与 test_doc_mgr_engine 一致的测试存储桩"""

    def __init__(self):
        self.add_calls = []

    def get(self, collection, ids=None, where=None):
        return {"ids": [], "metadatas": []}

    def add(self, collection, ids, documents, metadatas):
        self.add_calls.append({"collection": collection, "ids": list(ids)})
        return len(ids)

    def replace_document(self, collection, doc_id, file_name,
                         ids, documents, metadatas, legacy_ids=None):
        self.add_calls.append({
            "collection": collection, "doc_id": doc_id, "file_name": file_name,
            "ids": list(ids), "legacy_ids": list(legacy_ids or []),
        })
        return len(ids)


def _call_process_pdf(path: str, store=None, **overrides):
    """调用 _process_pdf，传最小参数集"""
    store = store or FakeStore()
    kwargs = dict(
        file_path=path, file_name=os.path.basename(path),
        file_size=os.path.getsize(path), store=store,
        target_collection="standards",
    )
    kwargs.update(overrides)
    return engine._process_pdf(**kwargs)


@unittest.skipIf(importlib.util.find_spec("fitz") is None, "PyMuPDF 未安装")
class ClassifyPdfTypeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_text_pdf_classified_as_text(self):
        path = _make_text_pdf(os.path.join(self.dir, "t.pdf"), pages=10)
        kind, det = classify_pdf_type(path)
        self.assertEqual(kind, "text")
        self.assertEqual(det["pages"], 10)
        self.assertEqual(det["text_pages"], 10)
        self.assertEqual(det["coverage"], 1.0)

    def test_scanned_pdf_classified_as_scanned(self):
        path = _make_scanned_pdf(os.path.join(self.dir, "s.pdf"), pages=10)
        kind, det = classify_pdf_type(path)
        self.assertEqual(kind, "scanned")
        self.assertEqual(det["coverage"], 0.0)

    def test_mixed_pdf_classified_as_mixed(self):
        path = _make_mixed_pdf(os.path.join(self.dir, "m.pdf"), 5, 5)
        kind, det = classify_pdf_type(path)
        self.assertEqual(kind, "mixed")
        self.assertGreater(det["coverage"], 0.05)
        self.assertLess(det["coverage"], 0.95)

    def test_watermark_pdf_classified_as_scanned(self):
        # 每页只有水印级零散文字（<50 字符）→ 判扫描版，防伪文字层
        path = _make_watermark_pdf(os.path.join(self.dir, "w.pdf"), pages=10)
        kind, det = classify_pdf_type(path)
        self.assertEqual(kind, "scanned")
        self.assertEqual(det["text_pages"], 0)


class ValidateLocalTextTests(unittest.TestCase):
    def test_normal_text_passes(self):
        text = "这是一段正常的标准文本内容，包含 text 123 等字符。" * 5
        self.assertTrue(validate_local_text(text, expected_chars=200))

    def test_garbled_text_rejected(self):
        self.assertFalse(validate_local_text(_GARBLED, expected_chars=100))

    def test_char_drop_rejected(self):
        # 检测摘要说有大量字符，实际只提取出极短文本 → 伪文字层
        self.assertFalse(validate_local_text("很短", expected_chars=10000))

    def test_empty_rejected(self):
        self.assertFalse(validate_local_text("", expected_chars=100))


class PdfRoutingEngineTests(unittest.TestCase):
    """引擎 _process_pdf 路由行为"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self.path = _make_text_pdf(os.path.join(self.dir, "routing.pdf"), pages=3)
        self.store = FakeStore()

    def tearDown(self):
        self._tmp.cleanup()

    # ---------- auto 模式 ----------

    def test_auto_text_uses_local_and_skips_mineru(self):
        """auto + 文字版 → 本地优先，MinerU 不被调用（关键断言）"""
        with patch.object(engine, "_get_pdf_routing", return_value="auto"), \
             patch.object(engine, "classify_pdf_type",
                          return_value=("text", {"pages": 3, "text_pages": 3,
                                                 "total_chars": 1000, "coverage": 1.0})), \
             patch.object(engine, "extract_pdf_text",
                          return_value=(_PAGE_TEXT * 3, "GB/T X", "标准")) as ex, \
             patch.object(engine, "_try_mineru",
                          return_value=None) as mineru:
            doc = _call_process_pdf(self.path, self.store)

        self.assertEqual(doc.status, "done")
        self.assertEqual(doc.source, "pymupdf")
        mineru.assert_not_called()  # 文字版不应碰 MinerU
        ex.assert_called_once()

    def test_auto_scanned_calls_mineru(self):
        """auto + 扫描版 → MinerU 优先；MinerU 失败回退本地（空）→ ocr_needed"""
        with patch.object(engine, "_get_pdf_routing", return_value="auto"), \
             patch.object(engine, "classify_pdf_type",
                          return_value=("scanned", {"pages": 3, "text_pages": 0,
                                                    "total_chars": 0, "coverage": 0.0})), \
             patch.object(engine, "_try_mineru", return_value=None) as mineru, \
             patch.object(engine, "extract_pdf_text", return_value=("", "", "")):
            doc = _call_process_pdf(self.path, self.store)

        mineru.assert_called_once()
        self.assertEqual(doc.status, "ocr_needed")

    def test_auto_mixed_calls_mineru(self):
        """auto + 混合版 → 保守走 MinerU"""
        with patch.object(engine, "_get_pdf_routing", return_value="auto"), \
             patch.object(engine, "classify_pdf_type",
                          return_value=("mixed", {"pages": 3, "text_pages": 2,
                                                  "total_chars": 500, "coverage": 0.67})), \
             patch.object(engine, "_try_mineru", return_value=None) as mineru, \
             patch.object(engine, "extract_pdf_text", return_value=("", "", "")):
            doc = _call_process_pdf(self.path, self.store)

        mineru.assert_called_once()
        self.assertEqual(doc.status, "ocr_needed")

    def test_auto_text_local_empty_falls_back_to_mineru(self):
        """auto + 文字版但本地提取为空 → 回退 MinerU"""
        with patch.object(engine, "_get_pdf_routing", return_value="auto"), \
             patch.object(engine, "classify_pdf_type",
                          return_value=("text", {"pages": 3, "text_pages": 3,
                                                 "total_chars": 1000, "coverage": 1.0})), \
             patch.object(engine, "extract_pdf_text", return_value=("", "", "")), \
             patch.object(engine, "_try_mineru", return_value=None) as mineru:
            doc = _call_process_pdf(self.path, self.store)

        mineru.assert_called_once()
        self.assertEqual(doc.status, "ocr_needed")

    def test_auto_text_local_quality_fails_falls_back_to_mineru(self):
        """auto + 文字版但本地提取质量差（字符骤减）→ 回退 MinerU"""
        with patch.object(engine, "_get_pdf_routing", return_value="auto"), \
             patch.object(engine, "classify_pdf_type",
                          return_value=("text", {"pages": 3, "text_pages": 3,
                                                 "total_chars": 1000000, "coverage": 1.0})), \
             patch.object(engine, "extract_pdf_text", return_value=("很短", "GB/T X", "标准")), \
             patch.object(engine, "_try_mineru", return_value=None) as mineru:
            doc = _call_process_pdf(self.path, self.store)

        mineru.assert_called_once()
        self.assertEqual(doc.status, "ocr_needed")

    def test_auto_scanned_mineru_success_uses_mineru(self):
        """auto + 扫描版 + MinerU 成功 → 走 Markdown 入库"""
        md_path = os.path.join(self.dir, "full.md")
        with open(md_path, "w", encoding="utf-8") as f:  # _try_mineru 闭包需 isfile 通过
            f.write("# GB/T X\n测试内容")
        md_doc = Document(file_name="x.pdf", file_path="x.pdf", file_size=0,
                          status="done", chunk_count=42, std_id="GB/T X",
                          std_title="标准", source="mineru")
        with patch.object(engine, "_get_pdf_routing", return_value="auto"), \
             patch.object(engine, "classify_pdf_type",
                          return_value=("scanned", {"pages": 3, "text_pages": 0,
                                                    "total_chars": 0, "coverage": 0.0})), \
             patch.object(engine, "_try_mineru", return_value=md_path), \
             patch.object(engine, "_process_markdown", return_value=md_doc):
            doc = _call_process_pdf(self.path, self.store)

        self.assertEqual(doc.source, "mineru")
        self.assertEqual(doc.status, "done")
        self.assertEqual(doc.chunk_count, 42)

    def test_auto_mixed_mineru_md_ingest_fails_falls_back_to_local(self):
        """审查 Critical 2 回归：MinerU 转换成功但 Markdown 入库失败
        （status=error）必须返回 False 触发本地回退，不得假成功。
        此前 _run_mineru 吞掉入库失败仍 return True，混合版本可用的
        本地 PyMuPDF 回退被跳过，文档静默丢失。"""
        md_path = os.path.join(self.dir, "full.md")
        with open(md_path, "w", encoding="utf-8") as f:  # _try_mineru 闭包需 isfile 通过
            f.write("# GB/T X\n测试内容")
        # MinerU 转换成功，但 Markdown 入库失败（模拟切块/Chroma 写入异常）
        md_doc = Document(file_name="x.pdf", file_path="x.pdf", file_size=0,
                          status="error", chunk_count=0, std_id="", std_title="",
                          source="mineru", message="Chroma 写入失败")
        with patch.object(engine, "_get_pdf_routing", return_value="auto"), \
             patch.object(engine, "classify_pdf_type",
                          return_value=("mixed", {"pages": 3, "text_pages": 2,
                                                  "total_chars": 500, "coverage": 0.67})), \
             patch.object(engine, "_try_mineru", return_value=md_path), \
             patch.object(engine, "_process_markdown", return_value=md_doc), \
             patch.object(engine, "extract_pdf_text",
                          return_value=(_PAGE_TEXT * 3, "GB/T X", "标准")):
            doc = _call_process_pdf(self.path, self.store)

        # 修复前：假成功停在 mineru/error；修复后：本地回退 pymupdf 成功
        self.assertEqual(doc.status, "done")
        self.assertEqual(doc.source, "pymupdf")

    # ---------- mineru 模式（旧行为） ----------

    def test_mineru_mode_always_mineru_first_even_for_text(self):
        """PDF_ROUTING=mineru → 即使文字版也 MinerU 优先；检测不执行"""
        with patch.object(engine, "_get_pdf_routing", return_value="mineru"), \
             patch.object(engine, "classify_pdf_type",
                          side_effect=AssertionError("mineru 模式不应做类型检测")) as clf, \
             patch.object(engine, "_try_mineru", return_value=None) as mineru, \
             patch.object(engine, "extract_pdf_text",
                          return_value=(_PAGE_TEXT * 3, "GB/T X", "标准")):
            doc = _call_process_pdf(self.path, self.store)

        clf.assert_not_called()
        mineru.assert_called_once()  # MinerU 失败后回退本地
        self.assertEqual(doc.source, "pymupdf")
        self.assertEqual(doc.status, "done")

    def test_mineru_mode_scanned_ocr_needed(self):
        """PDF_ROUTING=mineru + 扫描版 → MinerU 失败 + 本地空 → ocr_needed（原行为）"""
        with patch.object(engine, "_get_pdf_routing", return_value="mineru"), \
             patch.object(engine, "_try_mineru", return_value=None), \
             patch.object(engine, "extract_pdf_text", return_value=("", "", "")):
            doc = _call_process_pdf(self.path, self.store)

        self.assertEqual(doc.status, "ocr_needed")


if __name__ == "__main__":
    unittest.main()
