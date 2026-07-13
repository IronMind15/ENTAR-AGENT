"""
文档处理引擎

编排整个处理流程：文件识别 → 文本提取 → 智能切块 → 存储入库。

所有文档类型都走同一个入口 process_file()，
新增文件类型只需在 ext/chunker 的注册表中加一笔。
"""

import os
import re
import logging
from typing import Optional

from .models import Chunk, Document
from .storage import get_store, VectorStore
from .chunkers import UnstructuredChunker
from .extractors import extract_excel_rows, format_excel_row
from .extractors import extract_pdf_text

logger = logging.getLogger("doc_mgr.engine")


def process_file(file_path: str, file_name: Optional[str] = None) -> Document:
    """处理单个文件：提取 → 切块 → 入库

    Args:
        file_path: 文件绝对路径
        file_name: 文件名（上传时与临时路径不同名时使用）

    Returns:
        Document 对象，记录处理结果
    """
    name = file_name or os.path.basename(file_path)
    ext = os.path.splitext(name)[1].lower()
    size = os.path.getsize(file_path)
    store = get_store()

    logger.info(f"开始处理: {name} ({size / 1024:.1f}KB)")

    if ext == ".pdf":
        return _process_pdf(file_path, name, size, store)
    elif ext in (".xlsx", ".xls"):
        return _process_excel(file_path, name, size, store)
    else:
        logger.warning(f"不支持的文件类型: {ext}")
        return Document(
            file_name=name, file_path=file_path,
            file_size=size, status="error",
            message=f"不支持的文件类型: {ext}",
        )


def process_files(file_paths: list[str]) -> list[Document]:
    """批量处理多个文件"""
    return [process_file(fp) for fp in file_paths]


# ==================== PDF 处理 ====================

def _process_pdf(file_path: str, file_name: str, file_size: int,
                 store: VectorStore) -> Document:
    """处理 PDF 文件"""
    doc = Document(
        file_name=file_name, file_path=file_path,
        file_size=file_size, collection="standards",
    )

    # 1. PyMuPDF 提取文字
    full_text, std_id, std_title = extract_pdf_text(file_path)
    doc.std_id = std_id
    doc.std_title = std_title

    if not full_text:
        doc.status = "ocr_needed"
        doc.message = "扫描型 PDF，文字提取不足，需要 OCR 处理"
        logger.warning(f"  {doc.message}")
        return doc

    doc.status = "processing"

    # 2. 基础 metadata
    base_meta = {
        "std_id": std_id,
        "std_title": std_title,
        "file_name": file_name,
        "confidence": "text",
    }

    # 3. Unstructured 智能切块
    chunker = UnstructuredChunker()
    chunks = chunker.chunk(full_text, base_meta, filepath=file_path)

    # 4. 生成唯一 ID 并入库
    ids, documents, metadatas = _prepare_chunks(chunks, file_name)
    added = store.add("standards", ids, documents, metadatas)

    doc.chunk_count = added
    doc.status = "done" if added > 0 else "skipped"
    doc.message = f"切块入库 {added} 条"
    logger.info(f"  PDF 处理完成: {file_name} → {added} 块")
    return doc


# ==================== Excel 处理 ====================

def _process_excel(file_path: str, file_name: str, file_size: int,
                   store: VectorStore) -> Document:
    """处理 Excel 文件（故障代码）"""
    doc = Document(
        file_name=file_name, file_path=file_path,
        file_size=file_size, collection="error_codes",
    )

    # 1. 提取行数据
    records = extract_excel_rows(file_path)
    if not records:
        doc.status = "error"
        doc.message = "未提取到数据，请检查 Excel 格式"
        logger.warning(f"  {doc.message}")
        return doc

    doc.status = "processing"

    # 2. 每行切一块，生成 Chroma-compatible 格式
    ids = []
    documents = []
    metadatas = []

    for r in records:
        row_num = r.pop("_row_num", 0)
        sheet_name = r.pop("_sheet_name", "")
        doc_text = format_excel_row(r)

        ids.append(f"row_{row_num}")
        documents.append(doc_text)
        r["sheet_name"] = sheet_name
        r["row_num"] = row_num
        metadatas.append(r)

    # 3. 入库
    added = store.add("error_codes", ids, documents, metadatas)

    doc.chunk_count = added
    doc.status = "done" if added > 0 else "skipped"
    doc.message = f"入库 {added} 条故障代码"
    logger.info(f"  Excel 处理完成: {file_name} → {added} 条")
    return doc


# ==================== 辅助函数 ====================

def _prepare_chunks(chunks: list[Chunk], file_name: str
                    ) -> tuple[list[str], list[str], list[dict]]:
    """将 Chunk 对象转为 Chroma 的 add() 需要的格式

    生成唯一 ID（带去重计数器），拼接 doc_id 到 metadata。
    """
    ids = []
    documents = []
    metadatas = []
    id_counter: dict[str, int] = {}

    for ch in chunks:
        # 生成安全 ID
        safe_chapter = re.sub(r'[^a-zA-Z0-9一-鿿\-]', '_', ch.chapter or "")
        base_uid = f"{file_name}_{safe_chapter}_{ch.chunk_index}"
        base_uid = base_uid.replace(' ', '_').replace('/', '_')

        count = id_counter.get(base_uid, 0)
        id_counter[base_uid] = count + 1
        uid = f"{base_uid}_{count}" if count > 0 else base_uid

        meta = dict(ch.metadata)
        meta["chunk_index"] = ch.chunk_index

        ids.append(uid)
        documents.append(ch.text)
        metadatas.append(meta)

    return ids, documents, metadatas
