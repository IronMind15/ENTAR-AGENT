"""
文档处理引擎

编排整个处理流程：文件识别 → 文本提取 → 智能切块 → 存储入库。

所有文档类型都走同一个入口 process_file()，
新增文件类型只需在 ext/chunker 的注册表中加一笔。

处理策略：
  - PDF：优先使用 MinerU（VLM 识别 → Markdown），失败时回退到 PyMuPDF
  - Excel：openpyxl 逐行解析
  - Markdown：标题层级切块
"""

import os
import re
import logging
from typing import Optional

from .models import Chunk, Document
from .storage import get_store, VectorStore
from .chunkers import PdfChunker, MarkdownChunker
from .extractors import extract_excel_rows, format_excel_row
from .extractors import extract_pdf_text
from .sync_tracker import SyncTracker
from .task_manager import report_progress as _report_progress

logger = logging.getLogger("doc_mgr.engine")

def _get_mineru_output_dir(file_path: str) -> str:
    """根据源文件路径自动确定 MinerU 输出目录

    规则：源文件在哪个目录，mineru_output 就建在哪个目录下。
      data/standards/xxx.pdf  → data/standards/mineru_output/
      data/uploads/xxx.pdf    → data/uploads/mineru_output/
      data/fault_codes/xxx.xlsx → data/fault_codes/mineru_output/
    """
    abs_path = os.path.abspath(file_path)
    sep = os.sep
    # 找到路径中的 /data/ 段
    idx = abs_path.find(f"{sep}data{sep}")
    if idx >= 0:
        after_data = abs_path[idx + 6:]  # 去掉 /data/
        top_dir = after_data.split(sep)[0]  # standards / uploads / fault_codes
        project_root = abs_path[:idx]
        if top_dir in ("standards", "uploads", "fault_codes"):
            return os.path.join(project_root, "data", top_dir, "mineru_output")
    # 回退
    return os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..",
        "data", "standards", "mineru_output"
    )


def check_chroma_has_file(collection: str, file_name: str) -> bool:
    """查询 Chroma 中是否已有某文件的切块

    用于同步前的预检查：如果文件已经在库里，跳过处理直接标记已同步。
    仅查 metadata 过滤，不需要加载 embedding 模型。
    """
    try:
        store = get_store()
        data = store.get(collection, where={"file_name": file_name})
        if data and data.get("ids") and len(data["ids"]) > 0:
            return True
    except Exception as e:
        logger.warning(f"Chroma 预检查失败（不影响主流程）: {e}")
    return False


def _try_mineru(file_path: str, file_name: str) -> Optional[str]:
    """尝试用 MinerU 处理 PDF，返回生成的 Markdown 路径

    如果 MinerU 不可用、失败或超时，返回 None 让调用方走回退路径。
    """
    try:
        # 动态导入（MinerU 依赖可能未安装）
        import sys as _sys
        _script_dir = os.path.dirname(os.path.abspath(__file__))
        _parent = os.path.normpath(os.path.join(_script_dir, ".."))
        if _parent not in _sys.path:
            _sys.path.insert(0, _parent)

        from mineru_extract import load_token, extract_pdf as mineru_extract

        token = load_token()
        if not token:
            logger.info("  MinerU 未配置 Token，跳过")
            return None

        # 检查是否有缓存的 MinerU 输出
        stem = os.path.splitext(file_name)[0]
        mineru_dir = _get_mineru_output_dir(file_path)
        cache_dir = os.path.join(mineru_dir, f"{stem}-mineru-cache")
        cached_md = os.path.join(cache_dir, "full.md")

        if os.path.isfile(cached_md):
            logger.info(f"  使用缓存的 MinerU 输出: {cached_md}")
            return cached_md

        # 调用 MinerU API
        logger.info(f"  调用 MinerU 处理: {file_name}")
        os.makedirs(cache_dir, exist_ok=True)
        result_path = mineru_extract(file_path, cache_dir)

        # 处理返回结果（可能是 ZIP 或 MD）
        if result_path:
            if result_path.endswith(".zip"):
                # 解压 ZIP 获取 full.md
                import zipfile
                with zipfile.ZipFile(result_path, 'r') as zf:
                    # 查找 full.md
                    md_files = [n for n in zf.namelist() if n.endswith("full.md") or n.endswith(".md")]
                    if md_files:
                        target = os.path.join(cache_dir, md_files[0])
                        # 如果 ZIP 内是平铺的，直接解压到 cache_dir
                        zf.extractall(cache_dir)
                        # 找到最终的 full.md
                        extracted_md = os.path.join(cache_dir, md_files[0])
                        if os.path.isfile(extracted_md):
                            logger.info(f"  MinerU ZIP 解压完成: {extracted_md}")
                            return extracted_md
            elif result_path.endswith(".md"):
                logger.info(f"  MinerU 直接输出 MD: {result_path}")
                return result_path

        logger.warning("  MinerU 未产生有效输出")
        return None

    except TimeoutError as e:
        logger.warning(f"  ⏰ MinerU 远程转换超时，回退本地 PyMuPDF 处理: {e}")
        return None
    except Exception as e:
        logger.warning(f"  ❌ MinerU 远程转换失败，回退本地 PyMuPDF 处理: {e}")
        return None


def process_file(file_path: str, file_name: Optional[str] = None,
                 target_collection: Optional[str] = None) -> Document:
    """处理单个文件：提取 → 切块 → 入库

    Args:
        file_path: 文件绝对路径
        file_name: 文件名（上传时与临时路径不同名时使用）
        target_collection: 目标 collection 名，不指定则由扩展名自动判断

    Returns:
        Document 对象，记录处理结果
    """
    name = file_name or os.path.basename(file_path)
    ext = os.path.splitext(name)[1].lower()
    size = os.path.getsize(file_path)
    store = get_store()

    logger.info(f"开始处理: {name} ({size / 1024:.1f}KB)")

    if ext == ".pdf":
        return _process_pdf(file_path, name, size, store, target_collection)
    elif ext in (".xlsx", ".xls"):
        return _process_excel(file_path, name, size, store, target_collection)
    elif ext == ".md":
        return _process_markdown(file_path, name, size, store, target_collection)
    else:
        logger.warning(f"不支持的文件类型: {ext}")
        return Document(
            file_name=name, file_path=file_path,
            file_size=size, status="error",
            message=f"不支持的文件类型: {ext}",
        )

    # 记录同步状态（所有处理路径共用的出口）
    _record_sync_status(name, file_path, size, doc, store)


def _record_sync_status(file_name: str, file_path: str, file_size: int,
                         doc: Document, store: object = None) -> None:
    """记录文件同步状态到 sync_tracker

    放在 process_file 内部以确保所有调用路径（CLI/Admin/DingTalk/调度器）
    都能自动记录，无需每一处调用点额外调用。
    """
    try:
        tracker = SyncTracker()
        tracker.upsert_file(
            file_path=file_path,
            file_name=file_name,
            file_size=file_size,
            file_hash=str(int(os.path.getmtime(file_path))),
            target_collection=doc.collection or "",
        )
        if doc.status == "done":
            tracker.mark_synced(file_path)
        elif doc.status == "error":
            tracker.mark_error(file_path, doc.message or "处理失败")
        elif doc.status == "skipped":
            tracker.mark_synced(file_path)  # 已存在也算成功
        elif doc.status == "ocr_needed":
            tracker.mark_error(file_path, "需要 OCR 处理")
    except Exception as e:
        logger.warning(f"同步追踪记录失败（不影响主流程）: {e}")


def process_files(file_paths: list[str]) -> list[Document]:
    """批量处理多个文件"""
    return [process_file(fp) for fp in file_paths]


# ==================== PDF 处理 ====================

def _process_pdf(file_path: str, file_name: str, file_size: int,
                 store: VectorStore, target_collection: str = "standards") -> Document:
    """处理 PDF 文件

    策略：
      1. 尝试 MinerU（VLM → Markdown）→ 走 Markdown 入库
      2. MinerU 不可用 → PyMuPDF 提取文字 → PdfChunker 切块
      3. PyMuPDF 也提不出文字 → 标记 ocr_needed
    """
    doc = Document(
        file_name=file_name, file_path=file_path,
        file_size=file_size, collection=target_collection,
    )

    # ---- 方案 A：MinerU 优先 ----
    _report_progress("mineru", 15, "MinerU 远程转换中...")
    mineru_md = _try_mineru(file_path, file_name)
    if mineru_md and os.path.isfile(mineru_md):
        _report_progress("download", 40, "MinerU 转换完成，下载结果...")
        logger.info(f"  MinerU 成功，走 Markdown 入库路径")
        # 用 _process_markdown 处理 MinerU 输出的 MD 文件
        md_doc = _process_markdown(mineru_md, file_name, file_size, store, target_collection)
        # 继承 MinerU 的处理结果
        doc.status = md_doc.status
        doc.chunk_count = md_doc.chunk_count
        doc.std_id = md_doc.std_id
        doc.std_title = md_doc.std_title
        doc.source = "mineru"
        doc.message = md_doc.message or "MinerU → Markdown 入库"
        if doc.status != "done":
            logger.warning(f"  MinerU Markdown 入库异常: {doc.message}")
        else:
            logger.info(f"  MinerU 处理完成: {file_name} → {doc.chunk_count} 块")
        return doc

    # ---- 方案 B：PyMuPDF 本地工具回退 ----
    _report_progress("pymupdf_extract", 25, "MinerU 不可用，使用本地 PyMuPDF 提取文字...")
    logger.info(f"  [回退] 使用本地 PyMuPDF 提取文字: {file_name}")
    full_text, std_id, std_title = extract_pdf_text(file_path)
    doc.std_id = std_id
    doc.std_title = std_title

    if not full_text:
        doc.status = "ocr_needed"
        doc.message = "扫描型 PDF，本地 PyMuPDF 无法提取文字（MinerU 远程转换也未成功）"
        logger.warning(f"  ⚠️ [回退报告] MinerU 远程转换 → 不可用 / 失败")
        logger.warning(f"  ⚠️ [回退报告] PyMuPDF 本地提取 → 无法提取文字（扫描型 PDF）")
        logger.warning(f"  ⚠️ {doc.message}，跳过入库")
        return doc

    doc.status = "processing"

    # PyMuPDF 基础 metadata
    base_meta = {
        "std_id": std_id,
        "std_title": std_title,
        "file_name": file_name,
        "confidence": "text",
    }

    # PyMuPDF 结构分析切块
    _report_progress("chunking", 60, "PyMuPDF 文字提取完成，结构分析切块...")
    chunker = PdfChunker()
    chunks = chunker.chunk(full_text, base_meta, filepath=file_path)

    # 入库
    _report_progress("indexing", 80, f"切块完成（{len(chunks)} 块），入库到知识库...")
    ids, documents, metadatas = _prepare_chunks(chunks, file_name)
    added = store.add("standards", ids, documents, metadatas)

    doc.chunk_count = added
    doc.source = "pymupdf"
    doc.status = "done" if added > 0 else "skipped"
    doc.message = f"PyMuPDF 切块入库 {added} 条"
    logger.info(f"  PDF 处理完成（PyMuPDF）: {file_name} → {added} 块")
    return doc


# ==================== Excel 处理 ====================

def _process_excel(file_path: str, file_name: str, file_size: int,
                   store: VectorStore, target_collection: str = "error_codes") -> Document:
    """处理 Excel 文件（故障代码）"""
    doc = Document(
        file_name=file_name, file_path=file_path,
        file_size=file_size, collection=target_collection,
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
    _report_progress("excel_parse", 40, f"解析 Excel 完成（{len(records)} 行），准备入库...")
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
    doc.source = "excel"
    doc.status = "done" if added > 0 else "skipped"
    doc.message = f"入库 {added} 条故障代码"
    logger.info(f"  Excel 处理完成: {file_name} → {added} 条")
    return doc


# ==================== Markdown 处理（MinerU 输出） ====================

def _process_markdown(file_path: str, file_name: str, file_size: int,
                      store: VectorStore, target_collection: str = "standards") -> Document:
    """处理 Markdown 文件（MinerU 等工具输出）

    流程：读取 Markdown → 提取标准信息 → 切块 → 复制图片 → 入库
    """
    import shutil

    doc = Document(
        file_name=file_name, file_path=file_path,
        file_size=file_size, collection=target_collection,
    )

    # 1. 读取 Markdown 内容
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            full_text = f.read()
    except Exception as e:
        doc.status = "error"
        doc.message = f"读取文件失败: {e}"
        logger.error(f"  {doc.message}")
        return doc

    if not full_text.strip():
        doc.status = "error"
        doc.message = "文件为空"
        logger.warning(f"  {doc.message}")
        return doc

    doc.status = "processing"

    # 2. 从文件名或 Markdown 内容提取标准信息
    std_id, std_title = _extract_std_info(file_name, full_text)
    doc.std_id = std_id
    doc.std_title = std_title

    # 3. 基础 metadata
    base_meta = {
        "std_id": std_id,
        "std_title": std_title,
        "file_name": file_name,
        "source": "mineru",  # 标记来源为 MinerU
    }

    # 4. MarkdownChunker 切块
    _report_progress("chunking", 60, "Markdown 结构分析切块...")
    chunker = MarkdownChunker()
    chunks = chunker.chunk(full_text, base_meta, filepath=file_path)

    # 5. 复制 images 目录（如果存在）
    images_dir = os.path.join(os.path.dirname(file_path), "images")
    if os.path.isdir(images_dir):
        target_images_dir = os.path.join(
            os.path.dirname(os.path.dirname(file_path)),  # data/standards/
            "images", std_id or file_name
        )
        if not os.path.exists(target_images_dir):
            try:
                shutil.copytree(images_dir, target_images_dir)
                logger.info(f"  复制图片目录: {len(os.listdir(images_dir))} 个文件 → {target_images_dir}")
            except Exception as e:
                logger.warning(f"  复制图片失败: {e}")

    # 6. 入库（自带 ID 去重，已存在的 chunk 自动跳过）
    _report_progress("indexing", 80, f"切块完成（{len(chunks)} 块），入库到知识库...")
    ids, documents, metadatas = _prepare_chunks(chunks, file_name)
    added = store.add(target_collection, ids, documents, metadatas)

    doc.chunk_count = added
    doc.source = "markdown"
    if added > 0:
        doc.status = "done"
        doc.message = f"入库 {added} 条新切块"
    else:
        doc.status = "done"
        doc.message = "Chroma 已有该文件，跳过入库（MinerU 结果已保存到本地）"
    logger.info(f"  Markdown 处理完成: {file_name} → {added} 块")
    return doc


def _extract_std_info(file_name: str, content: str) -> tuple[str, str]:
    """从文件名或 Markdown 内容提取标准编号和名称

    Returns:
        (std_id, std_title)
    """
    # 尝试从文件名提取标准编号
    # 常见格式：GB_T_34133-2023.pdf-xxx/  或  EN50178.pdf-xxx/
    name_part = file_name.split(".pdf")[0] if ".pdf" in file_name else file_name
    name_part = name_part.split(".md")[0] if ".md" in name_part else name_part

    # 去掉 UUID 后缀（如果从 MinerU 输出目录来）
    if "-" in name_part:
        # 尝试匹配标准编号模式
        std_match = re.search(r'([A-Z]{2,}[\s/_]?\d{4,}(?:[.-]\d{4})?)', name_part)
        if std_match:
            std_id = std_match.group(1).replace("_", "/").replace(" ", "")
            # 从内容中找标题
            title_match = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
            std_title = title_match.group(1).strip() if title_match else name_part
            return std_id, std_title

    # 从 Markdown 内容提取
    # 找第一个 # 标题作为标准名称
    title_match = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
    if title_match:
        std_title = title_match.group(1).strip()
        # 尝试从标题附近找标准编号
        id_match = re.search(r'([A-Z]{2,}[\s/][\d.]+(?:[-.]?\d{4})?)', content[:1000])
        std_id = id_match.group(1) if id_match else name_part
        return std_id, std_title

    return name_part, name_part


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
