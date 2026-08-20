"""
文档处理引擎

编排整个处理流程：文件识别 → 文本提取 → 智能切块 → 存储入库。

所有文档类型都走同一个入口 process_file()，
新增文件类型只需在 ext/chunker 的注册表中加一笔。

处理策略：
  - PDF：优先使用 MinerU（VLM 识别 → Markdown），失败时回退到 PyMuPDF
  - Excel：openpyxl 逐行解析
  - Word：python-docx 提取 → 转 Markdown → MarkdownChunker 切块
  - PPT：python-pptx 提取 → 转 Markdown → MarkdownChunker 切块
  - CSV：stdlib csv 逐行解析（同 Excel 模式）
  - Markdown：标题层级切块
"""

import hashlib
import json
import logging
import os
import re
import shutil
import stat
import tempfile
import zipfile
from pathlib import PurePosixPath
from typing import Optional

from .models import Chunk, Document
from .storage import get_store, VectorStore
from .chunkers import PdfChunker, MarkdownChunker
from .extractors import extract_excel_rows, format_excel_row
from .extractors import extract_pdf_text, classify_pdf_type, validate_local_text
from .extractors import extract_docx_text, extract_pptx_text
from .extractors import extract_csv_rows, format_csv_row
from .sync_tracker import SyncTracker
from .task_manager import report_progress as _report_progress
from .identity import file_sha256, stable_document_id
from scripts.paths import DATA_ROOT, RUNTIME_DIR, STANDARDS_DIR

logger = logging.getLogger("doc_mgr.engine")


def _get_pdf_routing() -> str:
    """读取 PDF_ROUTING 配置（auto 检测路由 / mineru 全走 MinerU）

    防御式读取：config 模块可能未在 sys.path，读取失败时回退默认 auto。
    """
    try:
        import importlib
        cfg = importlib.import_module("scripts.config")
        return getattr(cfg, "PDF_ROUTING", "auto") or "auto"
    except Exception:
        return "auto"


def _get_mineru_output_dir(file_path: str) -> str:
    """根据源文件路径自动确定 MinerU 输出目录

    规则：源文件在哪个目录，mineru_output 就建在哪个目录下。
      data/standards/xxx.pdf  → data/standards/mineru_output/
      data/uploads/xxx.pdf    → data/uploads/mineru_output/
      data/fault_codes/xxx.xlsx → data/fault_codes/mineru_output/
    """
    abs_path = os.path.abspath(file_path)
    for root in (str(RUNTIME_DIR), str(DATA_ROOT)):
        root = os.path.abspath(root)
        try:
            relative = os.path.relpath(abs_path, root)
        except ValueError:
            continue
        if relative == os.pardir or relative.startswith(os.pardir + os.sep):
            continue
        top_dir = relative.split(os.sep)[0]
        if top_dir in ("standards", "uploads", "fault_codes"):
            return os.path.join(root, top_dir, "mineru_output")
    return os.path.join(str(STANDARDS_DIR), "mineru_output")


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


def _get_pdf_page_count(file_path: str) -> int:
    """获取 PDF 总页数"""
    import fitz
    doc = fitz.open(file_path)
    try:
        return doc.page_count
    finally:
        doc.close()


def _split_pdf(file_path: str, output_dir: str, max_pages: int = 200) -> list[str]:
    """将超过 max_pages 页的 PDF 拆分成多份子 PDF

    循环拆分，直到每一份都 ≤ max_pages：
      - 401 页 → 拆成 1-200、201-400、401（共 3 份）
      - 888 页 → 拆成 5 份（200+200+200+200+88）

    Args:
        file_path: 源 PDF 路径
        output_dir: 子 PDF 输出目录（调用方负责清理，建议传临时目录，
                    避免孤儿 chunk 累积占用磁盘）
        max_pages: 每份最大页数

    Returns:
        拆分后的 PDF 路径列表。如果页数没超限，返回 [file_path]
    """
    import fitz

    os.makedirs(output_dir, exist_ok=True)
    doc = fitz.open(file_path)
    try:
        total = doc.page_count
        if total <= max_pages:
            return [file_path]

        stem = os.path.splitext(os.path.basename(file_path))[0]
        chunk_paths = []
        total_parts = (total + max_pages - 1) // max_pages

        for i in range(0, total, max_pages):
            end = min(i + max_pages, total)
            part_num = i // max_pages + 1
            chunk_name = f"{stem}_p{part_num}of{total_parts}.pdf"
            chunk_path = os.path.join(output_dir, chunk_name)

            new_doc = fitz.open()
            try:
                new_doc.insert_pdf(doc, from_page=i, to_page=end - 1)
                new_doc.save(chunk_path)
            finally:
                new_doc.close()

            chunk_paths.append(chunk_path)
            logger.info(f"    拆分: 第{i+1}-{end}页 → {chunk_name}")

        return chunk_paths
    finally:
        doc.close()


_MAX_ZIP_MEMBERS = 5000
_MAX_ZIP_UNCOMPRESSED = 500 * 1024 * 1024
_MAX_ZIP_RATIO = 200


def _validated_zip_target(cache_dir: str, member_name: str) -> str:
    """校验 ZIP 成员路径，返回受 cache_dir 约束的绝对路径。"""
    normalized = member_name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (not normalized or path.is_absolute()
            or re.match(r"^[A-Za-z]:", normalized)
            or any(part == ".." for part in path.parts)):
        raise ValueError(f"ZIP 包含不安全路径: {member_name}")

    root = os.path.abspath(cache_dir)
    target = os.path.abspath(os.path.join(root, *path.parts))
    if os.path.commonpath([root, target]) != root:
        raise ValueError(f"ZIP 路径越界: {member_name}")
    return target


def _extract_mineru_result(result_path: str, cache_dir: str) -> Optional[str]:
    """从 MinerU 返回结果中提取 Markdown 文件路径

    处理 ZIP（解压找 .md）和直接 MD 两种情况。

    Returns:
        Markdown 文件路径，或 None
    """
    if not result_path:
        return None

    lower_path = result_path.lower()
    if lower_path.endswith(".zip"):
        with zipfile.ZipFile(result_path, "r") as zf:
            infos = zf.infolist()
            if len(infos) > _MAX_ZIP_MEMBERS:
                raise ValueError(f"ZIP 文件数量超限: {len(infos)}")

            total_size = sum(info.file_size for info in infos)
            if total_size > _MAX_ZIP_UNCOMPRESSED:
                raise ValueError(f"ZIP 解压后大小超限: {total_size} bytes")

            md_infos = []
            for info in infos:
                target = _validated_zip_target(cache_dir, info.filename)
                mode = (info.external_attr >> 16) & 0xFFFF
                if stat.S_ISLNK(mode):
                    raise ValueError(f"ZIP 不允许符号链接: {info.filename}")
                if (info.file_size > 0 and info.compress_size > 0
                        and info.file_size / info.compress_size > _MAX_ZIP_RATIO):
                    raise ValueError(f"ZIP 压缩比异常: {info.filename}")
                if info.filename.lower().endswith(".md") and not info.is_dir():
                    md_infos.append((info, target))

            full_md = [item for item in md_infos
                       if PurePosixPath(item[0].filename.replace("\\", "/")).name.lower() == "full.md"]
            candidates = full_md or md_infos
            if len(candidates) != 1:
                raise ValueError(f"MinerU ZIP 中 Markdown 结果不唯一: {len(candidates)}")

            os.makedirs(cache_dir, exist_ok=True)
            for info in infos:
                target = _validated_zip_target(cache_dir, info.filename)
                if info.is_dir():
                    os.makedirs(target, exist_ok=True)
                    continue
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with zf.open(info, "r") as source, open(target, "wb") as dest:
                    shutil.copyfileobj(source, dest, length=1024 * 1024)

            extracted = candidates[0][1]
            if os.path.isfile(extracted):
                logger.info(f"  MinerU ZIP 安全解压完成: {extracted}")
                return extracted
    elif lower_path.endswith(".md"):
        logger.info(f"  MinerU 直接输出 MD: {result_path}")
        return result_path

    return None


def _try_mineru(file_path: str, file_name: str,
                 content_hash: str = "", force: bool = False) -> Optional[str]:
    """尝试用 MinerU 处理 PDF，返回生成的 Markdown 路径

    如果 MinerU 不可用、失败或超时，返回 None 让调用方走回退路径。

    自动处理：
      - 超过 200 页的 PDF 自动拆分成多份分别送 MinerU，结果合并后返回
      - 有缓存时直接使用缓存结果
    """
    run_context = None
    try:
        # 动态导入（MinerU 依赖可能未安装）
        from scripts.mineru_extract import load_token, extract_pdf as mineru_extract

        token = load_token()
        if not token:
            logger.info("  MinerU 未配置 Token，跳过")
            return None

        # 检查是否有缓存的 MinerU 输出
        stem = os.path.splitext(file_name)[0]
        mineru_dir = _get_mineru_output_dir(file_path)
        cache_dir = os.path.join(mineru_dir, f"{stem}-mineru-cache")
        cached_md = os.path.join(cache_dir, "full.md")
        manifest_path = os.path.join(cache_dir, "cache.json")
        current_hash = content_hash or file_sha256(file_path)

        if not force and os.path.isfile(cached_md) and os.path.isfile(manifest_path):
            try:
                with open(manifest_path, "r", encoding="utf-8") as manifest_file:
                    manifest = json.load(manifest_file)
                if manifest.get("content_hash") == current_hash:
                    logger.info(f"  使用内容哈希匹配的 MinerU 缓存: {cached_md}")
                    return cached_md
            except (OSError, ValueError, TypeError) as e:
                logger.warning(f"  MinerU 缓存清单无效，将重新处理: {e}")

        os.makedirs(cache_dir, exist_ok=True)
        run_context = tempfile.TemporaryDirectory(prefix="run-", dir=cache_dir)
        run_dir = run_context.name

        # === 检查页数，200 页以上自动拆分 ===
        # 子 PDF 写入临时 run_dir（随 run_context.cleanup() 删除），
        # 避免在持久缓存目录累积孤儿 chunk 无限占用磁盘
        pdf_paths = _split_pdf(file_path, run_dir, max_pages=200)
        is_split = len(pdf_paths) > 1
        if is_split:
            total_pages = _get_pdf_page_count(file_path)
            logger.info(f"  📄 PDF 共 {total_pages} 页，超过 200 页限制，拆分为 {len(pdf_paths)} 份处理")

        # === 逐份送入 MinerU ===
        md_parts: list[str] = []
        failed_parts: list[str] = []
        for idx, chunk_path in enumerate(pdf_paths):
            chunk_name = os.path.basename(chunk_path)
            prefix = f"  [{idx+1}/{len(pdf_paths)}]" if is_split else ""
            logger.info(f"{prefix} 调用 MinerU 处理: {chunk_name}")

            try:
                part_dir = os.path.join(run_dir, f"part-{idx + 1:04d}")
                os.makedirs(part_dir, exist_ok=True)
                result_path = mineru_extract(chunk_path, part_dir)
                md_file = _extract_mineru_result(result_path, part_dir)
                if md_file and os.path.isfile(md_file):
                    md_parts.append(md_file)
                    logger.info(f"{prefix} ✅ MinerU 处理成功: {chunk_name}")
                else:
                    failed_parts.append(chunk_name)
                    logger.warning(f"{prefix} ⚠️ MinerU 未产生有效输出: {chunk_name}")
            except Exception as e:
                failed_parts.append(chunk_name)
                logger.warning(f"{prefix} ❌ MinerU 处理失败: {chunk_name} - {e}")
                continue

        if failed_parts or len(md_parts) != len(pdf_paths):
            logger.warning(
                f"  MinerU 分段结果不完整，拒绝合并（成功 {len(md_parts)}/{len(pdf_paths)}，"
                f"失败: {', '.join(failed_parts) or '未知'}）"
            )
            return None

        # === 按 PDF 分段顺序合并，先写临时文件再替换正式缓存 ===
        logger.info(f"  合并 {len(md_parts)} 份 MinerU 结果...")
        merged_path = os.path.join(cache_dir, "full.md")
        temp_merged = os.path.join(run_dir, "merged.md")
        with open(temp_merged, "w", encoding="utf-8") as merged:
            for i, md_file in enumerate(md_parts):
                with open(md_file, "r", encoding="utf-8") as part_file:
                    content = part_file.read()
                if not content.strip():
                    raise ValueError(f"MinerU 分段结果为空: {md_file}")
                if i > 0:
                    merged.write("\n\n<!-- MinerU 分块合并标记 -->\n\n")
                merged.write(content)
        os.replace(temp_merged, merged_path)
        with open(manifest_path, "w", encoding="utf-8") as manifest_file:
            json.dump({
                "content_hash": current_hash,
                "part_count": len(md_parts),
            }, manifest_file, ensure_ascii=False, indent=2)
        logger.info(f"  ✅ 合并完成: {merged_path}")
        return merged_path

    except TimeoutError as e:
        logger.warning(f"  ⏰ MinerU 远程转换超时，回退本地 PyMuPDF 处理: {e}")
        return None
    except Exception as e:
        logger.warning(f"  ❌ MinerU 远程转换失败，回退本地 PyMuPDF 处理: {e}")
        return None
    finally:
        if run_context is not None:
            run_context.cleanup()


def process_file(file_path: str, file_name: Optional[str] = None,
                 target_collection: Optional[str] = None,
                 force: bool = False,
                 department: str = "public") -> Document:
    """处理单个文件：提取 → 切块 → 入库

    Args:
        file_path: 文件绝对路径
        file_name: 文件名（上传时与临时路径不同名时使用）
        target_collection: 目标 collection 名，不指定则由扩展名自动判断
        force: 是否强制重新处理
        department: 所属中心 ID（默认 public = 全公司公开）

    Returns:
        Document 对象，记录处理结果
    """
    name = file_name or os.path.basename(file_path)
    ext = os.path.splitext(name)[1].lower()
    supported_exts = (".pdf", ".xlsx", ".xls", ".md", ".docx", ".pptx", ".csv", ".txt")
    if ext not in supported_exts:
        ext = os.path.splitext(file_path)[1].lower()
    size = os.path.getsize(file_path)
    content_hash = file_sha256(file_path)
    doc_id = stable_document_id(file_path)
    default_collections = {
        ".pdf": "standards",
        ".xlsx": "error_codes",
        ".xls": "error_codes",
        ".md": "standards",
        ".txt": "standards",
        ".docx": "standards",
        ".pptx": "standards",
        ".csv": "error_codes",
    }
    collection = target_collection or default_collections.get(ext, "")

    logger.info(f"开始处理: {name} ({size / 1024:.1f}KB)")

    if ext not in supported_exts:
        logger.warning(f"不支持的文件类型: {ext}")
        doc = Document(
            file_name=name, file_path=file_path, file_size=size,
            status="error", message=f"不支持的文件类型: {ext}",
            doc_id=doc_id, content_hash=content_hash,
            version_id=content_hash,
        )
    else:
        try:
            store = get_store()
            common = {
                "doc_id": doc_id,
                "content_hash": content_hash,
                "department": department,
            }
            if ext == ".pdf":
                doc = _process_pdf(
                    file_path, name, size, store, collection,
                    force=force, **common,
                )
            elif ext in (".xlsx", ".xls"):
                doc = _process_excel(
                    file_path, name, size, store, collection, **common,
                )
            elif ext == ".docx":
                doc = _process_word(
                    file_path, name, size, store, collection, **common,
                )
            elif ext == ".pptx":
                doc = _process_pptx(
                    file_path, name, size, store, collection, **common,
                )
            elif ext == ".csv":
                doc = _process_csv(
                    file_path, name, size, store, collection, **common,
                )
            else:
                doc = _process_markdown(
                    file_path, name, size, store, collection, **common,
                )
        except Exception as e:
            logger.exception(f"文档处理失败: {name}: {e}")
            doc = Document(
                file_name=name, file_path=file_path, file_size=size,
                collection=collection, status="error", message=str(e),
                doc_id=doc_id, content_hash=content_hash,
                version_id=content_hash,
            )

    # 记录同步状态（所有处理路径共用的出口）
    _record_sync_status(name, file_path, size, content_hash, doc)
    return doc


def _record_sync_status(file_name: str, file_path: str, file_size: int,
                         content_hash: str, doc: Document) -> None:
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
            file_hash=content_hash,
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
                 store: VectorStore, target_collection: str = "standards",
                 doc_id: str = "", content_hash: str = "",
                 force: bool = False,
                 department: str = "public") -> Document:
    """处理 PDF 文件

    策略（PDF_ROUTING=auto，v1.10.3 起）：
      1. 检测文字层覆盖率：
         - 纯文字版（text）→ 本地 PyMuPDF 免费高保真提取（省 MinerU 每日 1000 页额度）
         - 扫描版（scanned）/ 混合版（mixed）→ MinerU VLM 识别（保质量）
      2. MinerU 不可用 → PyMuPDF 提取文字 → PdfChunker 切块
      3. PyMuPDF 也提不出文字 → 标记 ocr_needed

    PDF_ROUTING=mineru 时恢复旧行为：一律 MinerU 优先、本地回退。
    """
    doc = Document(
        file_name=file_name, file_path=file_path,
        file_size=file_size, collection=target_collection,
        doc_id=doc_id, content_hash=content_hash,
        version_id=content_hash,
    )

    # ---- v1.10.3 路由：检测 PDF 类型，决定本地/MinerU 先后顺序 ----
    routing = _get_pdf_routing()
    det_summary = None
    local_first = False
    if routing == "auto":
        pdf_type, det_summary = classify_pdf_type(file_path)
        local_first = (pdf_type == "text")
        cov = det_summary.get("coverage", 0.0)
        logger.info(
            f"  PDF 路由: 类型={pdf_type}（覆盖率 {cov:.1%}）→ "
            + ("本地 PyMuPDF 优先（省 MinerU 额度）" if local_first else "MinerU 优先（保质量）")
        )

    def _run_mineru() -> bool:
        """方案 A：MinerU 远程转换 → Markdown 入库。成功返回 True。

        注意：嵌套闭包故意命名为 _run_mineru（而非 _try_mineru），
        避免与模块级 _try_mineru 同名造成 LEGB 遮蔽——同名时闭包体内
        `_try_mineru(...)` 会解析到闭包自身，导致 TypeError。
        """
        _report_progress("mineru", 15, "MinerU 远程转换中...")
        mineru_md = _try_mineru(
            file_path, file_name, content_hash=content_hash, force=force,
        )
        if not (mineru_md and os.path.isfile(mineru_md)):
            return False
        _report_progress("download", 40, "MinerU 转换完成，下载结果...")
        logger.info(f"  MinerU 成功，走 Markdown 入库路径")
        # 用 _process_markdown 处理 MinerU 输出的 MD 文件
        md_doc = _process_markdown(
            mineru_md, file_name, file_size, store, target_collection,
            doc_id=doc_id, content_hash=content_hash,
            department=department,
        )
        # 继承 MinerU 的处理结果
        doc.status = md_doc.status
        doc.chunk_count = md_doc.chunk_count
        doc.std_id = md_doc.std_id
        doc.std_title = md_doc.std_title
        doc.source = "mineru"
        doc.message = md_doc.message or "MinerU → Markdown 入库"
        if doc.status != "done":
            # 审查 Critical 2：MinerU 转换成功但 Markdown 入库失败（切块/Chroma 异常）
            # 必须返回 False 触发上层本地 PyMuPDF 回退，不得假成功静默丢文档
            logger.warning(f"  MinerU Markdown 入库异常: {doc.message}，回退本地 PyMuPDF")
            return False
        logger.info(f"  MinerU 处理完成: {file_name} → {doc.chunk_count} 块")
        return True

    def _try_local(expected_chars: int = 0) -> bool:
        """方案 B：PyMuPDF 本地提取 → PdfChunker 切块入库。成功返回 True。"""
        _report_progress("pymupdf_extract", 25, "使用本地 PyMuPDF 提取文字...")
        logger.info(f"  [本地] 使用 PyMuPDF 提取文字: {file_name}")
        full_text, std_id, std_title = extract_pdf_text(file_path)
        doc.std_id = std_id
        doc.std_title = std_title

        if not full_text:
            return False

        # 质量校验（仅 auto 路由 + 本地优先路径）：防「伪文字层」/乱码导致的质量下降
        if local_first and expected_chars > 0:
            if not validate_local_text(full_text, expected_chars):
                logger.warning(f"  ⚠️ 本地提取质量校验未通过，回退 MinerU: {file_name}")
                return False

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
        ids, documents, metadatas = _prepare_chunks(
            chunks, file_name, doc_id=doc_id, content_hash=content_hash,
            department=department,
        )
        added = store.replace_document(
            target_collection, doc_id, file_name,
            ids, documents, metadatas,
        )

        doc.chunk_count = added
        doc.source = "pymupdf"
        doc.status = "done"
        doc.message = f"PyMuPDF 新版本换库 {added} 条"
        logger.info(f"  PDF 处理完成（PyMuPDF）: {file_name} → {added} 块")
        return True

    if local_first:
        # ---- 路径②：纯文字版 → 本地优先（免费省额度），失败/质量差回退 MinerU ----
        if _try_local(det_summary.get("total_chars", 0) if det_summary else 0):
            return doc
        logger.info(f"  本地路径未成功，回退 MinerU 远程转换: {file_name}")
        if _run_mineru():
            return doc
    else:
        # ---- 路径①：扫描/混合版 或 mineru 模式 → MinerU 优先（旧行为），失败回退本地 ----
        if _run_mineru():
            return doc
        if _try_local():
            return doc

    # ---- 两条路径都失败：标记 ocr_needed ----
    doc.status = "ocr_needed"
    doc.message = "扫描型 PDF，本地 PyMuPDF 无法提取文字（MinerU 远程转换也未成功）"
    logger.warning(f"  ⚠️ [回退报告] MinerU 远程转换 → 不可用 / 失败")
    logger.warning(f"  ⚠️ [回退报告] PyMuPDF 本地提取 → 无法提取文字（扫描型 PDF）")
    logger.warning(f"  ⚠️ {doc.message}，跳过入库")
    return doc


# ==================== Excel 处理 ====================

def _process_excel(file_path: str, file_name: str, file_size: int,
                   store: VectorStore, target_collection: str = "error_codes",
                   doc_id: str = "", content_hash: str = "",
                   department: str = "public") -> Document:
    """处理 Excel 文件（故障代码）"""
    doc = Document(
        file_name=file_name, file_path=file_path,
        file_size=file_size, collection=target_collection,
        doc_id=doc_id, content_hash=content_hash,
        version_id=content_hash,
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

    # 兼容旧知识库中的 row_行号 ID：同一故障记录继续沿用旧 ID，
    # 避免第一阶段切换 ID 规则时立即产生一份重复数据。
    legacy_ids = [f"row_{r.get('_row_num', 0)}" for r in records]
    legacy_meta: dict[str, dict] = {}
    try:
        existing = store.get(target_collection, ids=legacy_ids)
        for index, existing_id in enumerate(existing.get("ids", [])):
            metas = existing.get("metadatas", [])
            legacy_meta[existing_id] = metas[index] if index < len(metas) else {}
    except Exception as e:
        logger.warning(f"  检查旧版 Excel ID 失败，将使用新版文档级 ID: {e}")

    matched_legacy_ids: list[str] = []
    for r in records:
        row_num = r.pop("_row_num", 0)
        sheet_name = r.pop("_sheet_name", "")
        doc_text = format_excel_row(r)

        safe_sheet = re.sub(r"[^a-zA-Z0-9一-鿿-]", "_", sheet_name)
        legacy_id = f"row_{row_num}"
        old_meta = legacy_meta.get(legacy_id, {})
        same_legacy_record = bool(old_meta) and (
            (r.get("fault_code") and old_meta.get("fault_code") == r.get("fault_code"))
            or (r.get("name") and old_meta.get("name") == r.get("name"))
        )
        if same_legacy_record:
            matched_legacy_ids.append(legacy_id)
        ids.append(f"excel_{doc_id or file_name}_{safe_sheet}_{row_num}")
        documents.append(doc_text)
        r["sheet_name"] = sheet_name
        r["row_num"] = row_num
        r["file_name"] = file_name
        r["doc_id"] = doc_id
        r["content_hash"] = content_hash
        r["version_id"] = content_hash
        r["department"] = department
        metadatas.append(r)

    # 3. 入库
    added = store.replace_document(
        target_collection, doc_id, file_name,
        ids, documents, metadatas,
        legacy_ids=matched_legacy_ids,
    )

    doc.chunk_count = added
    doc.source = "excel"
    doc.status = "done"
    doc.message = f"新版本换库 {added} 条故障代码"
    logger.info(f"  Excel 处理完成: {file_name} → {added} 条")
    return doc


# ==================== Word 处理 ====================

def _process_word(file_path: str, file_name: str, file_size: int,
                  store: VectorStore, target_collection: str = "standards",
                  doc_id: str = "", content_hash: str = "",
                  department: str = "public") -> Document:
    """处理 Word (.docx) 文件

    流程：python-docx 提取 → 转 Markdown → MarkdownChunker 切块 → 入库
    """
    doc = Document(
        file_name=file_name, file_path=file_path,
        file_size=file_size, collection=target_collection,
        doc_id=doc_id, content_hash=content_hash,
        version_id=content_hash,
    )

    # 1. 提取文本（输出 Markdown 格式）
    _report_progress("word_parse", 30, "提取 Word 文档内容...")
    full_text = extract_docx_text(file_path)
    if not full_text.strip():
        doc.status = "error"
        doc.message = "未提取到文本内容，请检查 Word 文件"
        logger.warning(f"  {doc.message}")
        return doc

    doc.status = "processing"

    # 2. 提取标准编号信息（如果有）
    std_id, std_title = _extract_std_info(file_name, full_text)
    doc.std_id = std_id
    doc.std_title = std_title

    # 3. MarkdownChunker 切块
    base_meta = {
        "std_id": std_id,
        "std_title": std_title,
        "file_name": file_name,
        "source": "word",
        "doc_id": doc_id,
        "content_hash": content_hash,
        "version_id": content_hash,
    }
    _report_progress("chunking", 60, "Word 文档结构分析切块...")
    chunker = MarkdownChunker()
    chunks = chunker.chunk(full_text, base_meta, filepath=file_path)

    # 4. 入库
    _report_progress("indexing", 80, f"切块完成（{len(chunks)} 块），入库到知识库...")
    ids, documents, metadatas = _prepare_chunks(
        chunks, file_name, doc_id=doc_id, content_hash=content_hash,
        department=department,
    )
    added = store.replace_document(
        target_collection, doc_id, file_name,
        ids, documents, metadatas,
    )

    doc.chunk_count = added
    doc.source = "word"
    doc.status = "done"
    doc.message = f"新版本换库 {added} 条切块"
    logger.info(f"  Word 处理完成: {file_name} → {added} 块")
    return doc


# ==================== PPT 处理 ====================

def _process_pptx(file_path: str, file_name: str, file_size: int,
                  store: VectorStore, target_collection: str = "standards",
                  doc_id: str = "", content_hash: str = "",
                  department: str = "public") -> Document:
    """处理 PPT (.pptx) 文件

    流程：python-pptx 提取 → 转 Markdown → MarkdownChunker 切块 → 入库
    """
    doc = Document(
        file_name=file_name, file_path=file_path,
        file_size=file_size, collection=target_collection,
        doc_id=doc_id, content_hash=content_hash,
        version_id=content_hash,
    )

    # 1. 提取文本（输出 Markdown 格式）
    _report_progress("pptx_parse", 30, "提取 PPT 文档内容...")
    full_text = extract_pptx_text(file_path)
    if not full_text.strip():
        doc.status = "error"
        doc.message = "未提取到文本内容，请检查 PPT 文件"
        logger.warning(f"  {doc.message}")
        return doc

    doc.status = "processing"

    # 2. MarkdownChunker 切块
    base_meta = {
        "file_name": file_name,
        "source": "pptx",
        "doc_id": doc_id,
        "content_hash": content_hash,
        "version_id": content_hash,
    }
    _report_progress("chunking", 60, "PPT 文档结构分析切块...")
    chunker = MarkdownChunker()
    chunks = chunker.chunk(full_text, base_meta, filepath=file_path)

    # 3. 入库
    _report_progress("indexing", 80, f"切块完成（{len(chunks)} 块），入库到知识库...")
    ids, documents, metadatas = _prepare_chunks(
        chunks, file_name, doc_id=doc_id, content_hash=content_hash,
        department=department,
    )
    added = store.replace_document(
        target_collection, doc_id, file_name,
        ids, documents, metadatas,
    )

    doc.chunk_count = added
    doc.source = "pptx"
    doc.status = "done"
    doc.message = f"新版本换库 {added} 条切块"
    logger.info(f"  PPT 处理完成: {file_name} → {added} 块")
    return doc


# ==================== CSV 处理 ====================

def _process_csv(file_path: str, file_name: str, file_size: int,
                 store: VectorStore, target_collection: str = "error_codes",
                 doc_id: str = "", content_hash: str = "",
                 department: str = "public") -> Document:
    """处理 CSV (.csv) 文件

    流程：csv 逐行解析 → 每行格式化 → 直接入库（同 Excel 模式，不切块）
    """
    doc = Document(
        file_name=file_name, file_path=file_path,
        file_size=file_size, collection=target_collection,
        doc_id=doc_id, content_hash=content_hash,
        version_id=content_hash,
    )

    # 1. 提取行数据
    _report_progress("csv_parse", 30, "解析 CSV 文件...")
    records = extract_csv_rows(file_path)
    if not records:
        doc.status = "error"
        doc.message = "未提取到数据，请检查 CSV 格式"
        logger.warning(f"  {doc.message}")
        return doc

    doc.status = "processing"

    # 2. 获取表头（用于格式化）
    headers = [k for k in records[0].keys() if not k.startswith("_")]

    # 3. 每行一条记录，生成 Chroma-compatible 格式
    _report_progress("csv_parse", 50, f"解析 CSV 完成（{len(records)} 行），准备入库...")
    ids = []
    documents = []
    metadatas = []

    for r in records:
        row_num = r.pop("_row_num", 0)
        r.pop("_encoding", None)
        doc_text = format_csv_row(r, headers)

        safe_name = re.sub(r"[^a-zA-Z0-9一-鿿-]", "_", file_name)
        ids.append(f"csv_{doc_id or safe_name}_{row_num}")
        documents.append(doc_text)
        r["row_num"] = row_num
        r["file_name"] = file_name
        r["doc_id"] = doc_id
        r["content_hash"] = content_hash
        r["version_id"] = content_hash
        r["department"] = department
        metadatas.append(r)

    # 4. 入库
    added = store.replace_document(
        target_collection, doc_id, file_name,
        ids, documents, metadatas,
    )

    doc.chunk_count = added
    doc.source = "csv"
    doc.status = "done"
    doc.message = f"新版本换库 {added} 条 CSV 数据"
    logger.info(f"  CSV 处理完成: {file_name} → {added} 条")
    return doc


# ==================== Markdown 处理（MinerU 输出） ====================

def _process_markdown(file_path: str, file_name: str, file_size: int,
                      store: VectorStore, target_collection: str = "standards",
                      doc_id: str = "", content_hash: str = "",
                      department: str = "public") -> Document:
    """处理 Markdown 文件（MinerU 等工具输出）

    流程：读取 Markdown → 提取标准信息 → 切块 → 复制图片 → 入库
    """
    import shutil

    doc = Document(
        file_name=file_name, file_path=file_path,
        file_size=file_size, collection=target_collection,
        doc_id=doc_id, content_hash=content_hash,
        version_id=content_hash,
    )

    # 1. 读取 Markdown 内容
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            full_text = f.read()
    except UnicodeDecodeError:
        # v1.11.11：中文环境常见的 GBK 编码 .txt/.md 兜底（utf-8 解不动才回退）
        with open(file_path, "r", encoding="gbk", errors="replace") as f:
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

    # 2-4. 提取标准信息 + 切块 + 元数据（与 process_text 共用的内部 helper）
    ids, documents, metadatas, std_id, std_title = _index_markdown_text(
        full_text, file_name, store, target_collection, doc_id, content_hash,
        department=department, filepath=file_path)
    doc.std_id = std_id
    doc.std_title = std_title

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

    # 6. 两阶段换版：新块完整写入后才退休旧版本
    _report_progress("indexing", 80, f"切块完成（{len(ids)} 块），入库到知识库...")
    added = store.replace_document(
        target_collection, doc_id, file_name,
        ids, documents, metadatas,
    )

    doc.chunk_count = added
    doc.source = "markdown"
    doc.status = "done"
    doc.message = f"新版本换库 {added} 条切块"
    logger.info(f"  Markdown 处理完成: {file_name} → {added} 块")
    return doc


def _index_markdown_text(full_text: str, file_name: str, store: VectorStore,
                         target_collection: str, doc_id: str, content_hash: str,
                         department: str = "public", filepath: str = "",
                         ) -> tuple[list[str], list[str], list[dict], str, str]:
    """Markdown 文本 → 标准信息 + 切块 + 元数据（process_file 与 process_text 共用）

    Returns:
        (ids, documents, metadatas, std_id, std_title)
    """
    std_id, std_title = _extract_std_info(file_name, full_text)
    base_meta = {
        "std_id": std_id,
        "std_title": std_title,
        "file_name": file_name,
        "source": "mineru",  # 标记来源为 MinerU
        "doc_id": doc_id,
        "content_hash": content_hash,
        "version_id": content_hash,
    }
    _report_progress("chunking", 60, "Markdown 结构分析切块...")
    chunker = MarkdownChunker()
    chunks = chunker.chunk(full_text, base_meta, filepath=filepath)
    ids, documents, metadatas = _prepare_chunks(
        chunks, file_name, doc_id=doc_id, content_hash=content_hash,
        department=department,
    )
    return ids, documents, metadatas, std_id, std_title


def process_text(content: str, file_name: str,
                 target_collection: str = "standards",
                 department: str = "public", doc_id: str = "") -> Document:
    """直接把文本内容切块入库（v1.11.0，钉钉在线文档「帮我学习」用）

    与 process_file 共用 _index_markdown_text 的切块/入库逻辑，但：
    - 不写临时文件、不进 data/uploads（不触发文件生命周期/路径校验）
    - 无图片复制（在线文档没有本地 images 目录）
    """
    content = content or ""
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    doc_id = doc_id or content_hash[:20]
    doc = Document(
        file_name=file_name, collection=target_collection,
        file_size=len(content.encode("utf-8")),
        doc_id=doc_id, content_hash=content_hash, version_id=content_hash,
    )
    if not content.strip():
        doc.status = "error"
        doc.message = "内容为空"
        logger.warning(f"  process_text: 内容为空（{file_name}）")
        return doc

    doc.status = "processing"
    store = get_store()
    ids, documents, metadatas, std_id, std_title = _index_markdown_text(
        content, file_name, store, target_collection, doc_id, content_hash,
        department=department)
    doc.std_id = std_id
    doc.std_title = std_title

    _report_progress("indexing", 80, f"切块完成（{len(ids)} 块），入库到知识库...")
    added = store.replace_document(
        target_collection, doc_id, file_name,
        ids, documents, metadatas,
    )

    doc.chunk_count = added
    doc.source = "markdown"
    doc.status = "done"
    doc.message = f"文本直入 {added} 条切块"
    logger.info(f"  文本直入完成: {file_name} → {added} 块")
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

def _prepare_chunks(chunks: list[Chunk], file_name: str,
                    doc_id: str = "", content_hash: str = "",
                    department: str = "public"
                    ) -> tuple[list[str], list[str], list[dict]]:
    """将 Chunk 对象转为 Chroma 的 add() 需要的格式

    生成唯一 ID（带去重计数器），拼接 doc_id 和 department 到 metadata。
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
        meta["file_name"] = file_name
        meta["doc_id"] = doc_id
        meta["content_hash"] = content_hash
        meta["version_id"] = content_hash
        meta["department"] = department

        ids.append(uid)
        documents.append(ch.text)
        metadatas.append(meta)

    return ids, documents, metadatas
