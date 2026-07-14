"""
Markdown 切块器

基于 Markdown 标题层级（# ## ###）进行切块，
适用于 MinerU 等工具输出的结构化 Markdown 文档。

特点：
  - 直接利用 Markdown 标题语法，无需字体/位置分析
  - 保留图片引用 ![](images/xxx.jpg) 在 Chunk 中
  - 支持中文标准的章节号提取（如 "1"、"3.1"、"附录A"）
"""

import logging
import re
from typing import Optional

from ..models import Chunk
from .base import Chunker

logger = logging.getLogger("doc_mgr.chunkers.markdown")


class MarkdownChunker(Chunker):
    """基于 Markdown 标题层级的切块器"""

    def __init__(self, min_chunk_chars: int = 50, max_chunk_chars: int = 3000):
        self.min_chunk_chars = min_chunk_chars
        self.max_chunk_chars = max_chunk_chars

    def chunk(self, text: str, metadata: Optional[dict] = None,
              filepath: str = "") -> list[Chunk]:
        """将 Markdown 按标题层级切块

        Args:
            text: Markdown 全文
            metadata: 基础 metadata
            filepath: 源文件路径（用于定位 images 目录）

        Returns:
            Chunk 列表
        """
        meta = metadata or {}

        # 按标题行分割
        sections = self._split_by_headings(text)

        if not sections:
            logger.warning("未检测到标题，使用全文单块")
            return [Chunk(text=text[:self.max_chunk_chars], metadata=meta, chapter="全文")]

        # 构建 Chunk
        chunks = self._build_chunks(sections, meta)

        # 后处理
        chunks = self.merge_small_chunks(chunks, min_chars=self.min_chunk_chars)
        chunks = self.split_oversized_chunks(chunks, max_chars=self.max_chunk_chars)

        logger.info(f"Markdown 切块完成: {len(chunks)} 块")
        return chunks

    def _split_by_headings(self, text: str) -> list[dict]:
        """按 Markdown 标题分割

        返回：[{"heading": "# 标题", "level": 1, "content": "正文..."}, ...]
        """
        lines = text.split("\n")
        sections = []
        current = {"heading": "", "level": 0, "content_lines": []}

        # 标题模式：# ## ### 等
        heading_pattern = re.compile(r'^(#{1,6})\s+(.+)$')

        for line in lines:
            match = heading_pattern.match(line)
            if match:
                # 保存上一节
                if current["content_lines"] or current["heading"]:
                    sections.append(current)

                # 新一节
                level = len(match.group(1))
                heading = match.group(2).strip()
                current = {
                    "heading": heading,
                    "level": level,
                    "content_lines": [],
                    "heading_raw": line,  # 保留原始标题行（含 #）
                }
            else:
                current["content_lines"].append(line)

        # 最后一节
        if current["content_lines"] or current["heading"]:
            sections.append(current)

        return sections

    def _extract_chapter_number(self, heading: str) -> str:
        """从标题文本中提取章节号

        支持格式：
          - "1 范围" → "1"
          - "3.1 术语" → "3.1"
          - "附录A" → "附录A"
          - "第1章" → "1"
        """
        # 中文 "第X章"
        m = re.match(r'^第\s*([一二三四五六七八九十百零\d]+)\s*章', heading)
        if m:
            cn = m.group(1)
            cn_map = {"一": "1", "二": "2", "三": "3", "四": "4", "五": "5",
                      "六": "6", "七": "7", "八": "8", "九": "9", "十": "10",
                      "零": "0"}
            if cn.isdigit():
                return cn
            return "".join(cn_map.get(c, c) for c in cn)

        # 数字章节号 "3.1.2"
        m = re.match(r'^(\d{1,2}(?:\.\d+)*)\s', heading)
        if m:
            return m.group(1)

        # 附录 "附录A" / "Annex A"
        m = re.match(r'^附录\s*([A-Z一二三四五])', heading)
        if m:
            return f"附录{m.group(1)}"

        m = re.match(r'^(Annex|Appendix)\s+([A-Z])', heading, re.IGNORECASE)
        if m:
            return f"{m.group(1)} {m.group(2)}"

        # 无章节号，返回标题前 20 字符
        return heading[:20] if heading else ""

    def _build_chunks(self, sections: list[dict], base_meta: dict) -> list[Chunk]:
        """从章节列表构建 Chunk 对象"""
        chunks = []

        for idx, sec in enumerate(sections):
            heading = sec["heading"]
            content = "\n".join(sec["content_lines"]).strip()

            # 跳过空内容的章节（如只有标题没有正文）
            if not content:
                continue

            meta = dict(base_meta)
            meta["chapter_title"] = heading
            meta["heading_level"] = sec["level"]

            chapter = self._extract_chapter_number(heading)
            if chapter:
                meta["chapter"] = chapter

            chunks.append(Chunk(
                text=content,
                metadata=meta,
                chunk_index=idx,
                chapter=meta.get("chapter", ""),
                chapter_title=heading,
                level=sec["level"],
            ))

        return chunks
