"""
Unstructured 切块器

核心改进：用 Unstructured 解析 PDF 文档结构，按 heading 位置切块。
替代了之前 sync_standards.py 里 ~200 行的正则匹配。

使用方式：
    chunker = UnstructuredChunker()
    chunks = chunker.chunk(text, metadata, filepath="doc.pdf")

Unstructured 自动处理：
  - 中/英/双语文档的结构识别
  - 目录页排除
  - heading 层级检测（H1/H2/H3）
  - paragraph / list / table 分类
"""

import logging
from typing import Optional
from ..models import Chunk
from .base import Chunker

logger = logging.getLogger("doc_mgr.chunkers.unstructured")


class UnstructuredChunker(Chunker):
    """基于 Unstructured 的 PDF 文档智能切块器"""

    # Unstructured 返回的 element 类型中，需要跳过的
    SKIP_CATEGORIES = {"Title", "Header", "Footer", "PageBreak", "Figure"}

    def chunk(self, text: str, metadata: Optional[dict] = None,
              filepath: str = "") -> list[Chunk]:
        """解析 PDF 并按 heading 位置切块

        Args:
            text: PyMuPDF 提取的全文（当前未使用，由 Unstructured 重新读取）
            metadata: 基础 metadata
            filepath: PDF 文件路径（Unstructured 需要直接读取文件）

        Returns:
            Chunk 列表
        """
        meta = metadata or {}

        if not filepath:
            logger.warning("未提供 filepath，无法调用 Unstructured 解析")
            return [Chunk(text=text[:3000], metadata=meta, chapter="全文")]

        elements = self._parse_pdf(filepath)
        if not elements:
            logger.warning("Unstructured 未能解析文档，回退到全文单块")
            return [Chunk(text=text[:3000], metadata=meta, chapter="全文")]

        chunks = self._chunk_by_headings(elements, meta)
        logger.info(f"Unstructured 切块完成: {len(chunks)} 块")
        return chunks

    def _parse_pdf(self, filepath: str) -> list:
        """调用 Unstructured 解析 PDF，返回 element 列表"""
        try:
            from unstructured.partition.pdf import partition_pdf
        except ImportError:
            logger.error("unstructured 未安装，请执行: pip install 'unstructured[pdf]'")
            return []

        try:
            elements = partition_pdf(
                filepath,
                strategy="auto",         # 自动选择最佳策略
                infer_table_structure=False,  # 暂不解析表格结构
                include_page_breaks=False,
            )
            logger.info(f"Unstructured 解析完成: {len(elements)} 个 elements")
            return elements
        except Exception as e:
            logger.exception(f"Unstructured 解析失败: {e}")
            return []

    def _chunk_by_headings(self, elements: list, base_meta: dict) -> list[Chunk]:
        """按 heading 元素位置切块"""
        chunks = []
        current_items = []   # 当前章节的所有元素
        current_heading = "全文"
        current_level = 0
        current_page = None
        chunk_index = 0

        for el in elements:
            cat = el.category
            page = getattr(el, "metadata", {}).get("page_number", None)
            if page:
                current_page = page

            # 跳过无关元素
            if cat in self.SKIP_CATEGORIES:
                continue

            # 遇到标题 → 结束上一节，开始新一节
            if cat == "Heading":
                if current_items:
                    chunks.append(self._build_chunk(
                        current_items, current_heading, current_level,
                        base_meta, chunk_index
                    ))
                    chunk_index += 1
                    current_items = []

                # 用标题文本作为新章节名
                level = getattr(el, "metadata", {}).get("heading_level", 1)
                level = level if 1 <= level <= 6 else 1
                current_heading = (el.text or "").strip()
                current_level = level

                # 如果标题本身有内容（很短的一行），也作为正文
                if current_heading and len(current_heading) < 100:
                    current_items.append(current_heading)
                continue

            # 正文元素
            text = (el.text or "").strip()
            if text:
                current_items.append(text)

        # 最后一节
        if current_items:
            chunks.append(self._build_chunk(
                current_items, current_heading, current_level,
                base_meta, chunk_index
            ))

        # 后处理：合并小块、拆分大块
        chunks = self.merge_small_chunks(chunks, min_chars=50)
        chunks = self.split_oversized_chunks(chunks, max_chars=3000)

        return chunks

    def _build_chunk(self, items: list[str], heading: str, level: int,
                     base_meta: dict, index: int) -> Chunk:
        """从一组元素构建一个 Chunk"""
        text = "\n\n".join(items)
        meta = dict(base_meta)
        meta["chapter_title"] = heading
        meta["heading_level"] = level

        # 尝试从 heading 中提取章节号（如 "1.1"、"第2章"）
        chapter = self._extract_chapter_number(heading)
        if chapter:
            meta["chapter"] = chapter

        return Chunk(
            text=text,
            metadata=meta,
            chunk_index=index,
            chapter=meta.get("chapter", ""),
            chapter_title=heading,
            level=level,
        )

    @staticmethod
    def _extract_chapter_number(text: str) -> str:
        """从标题文本中提取章节号

        如 "第1章 范围" → "1"
           "3.1 一般术语" → "3.1"
           "Annex A" → "附录A"
        """
        import re
        # 中文 "第X章"
        m = re.match(r'第\s*([一二三四五六七八九十百零\d]+)\s*章', text)
        if m:
            cn = m.group(1)
            # 中文数字转阿拉伯
            cn_map = {"一": "1", "二": "2", "三": "3", "四": "4", "五": "5",
                      "六": "6", "七": "7", "八": "8", "九": "9", "十": "10",
                      "零": "0"}
            if cn.isdigit():
                return cn
            return "".join(cn_map.get(c, c) for c in cn)

        # 英文数字章节 "X.Y[.Z]"
        m = re.match(r'(\d+(?:\.\d+)*)\s', text)
        if m:
            return m.group(1)

        # 附录
        m = re.match(r'附录\s*([A-Z])', text)
        if m:
            return f"附录{m.group(1)}"

        m = re.match(r'(Annex|Appendix)\s+([A-Z])', text)
        if m:
            return f"{m.group(1)} {m.group(2)}"

        return text[:20]  # 无匹配时返回截断标题
