"""
滑动窗口回退切块器

当文档结构无法识别时（Unstructured 也没解析出来），
使用滑动窗口做最基础的切块，保证再烂的文档也有数据可用。
"""

import logging
from typing import Optional
from ..models import Chunk
from .base import Chunker

logger = logging.getLogger("doc_mgr.chunkers.fallback")


class FallbackChunker(Chunker):
    """滑动窗口回退切块器"""

    def __init__(self, chunk_size: int = 512, overlap: int = 128):
        self._chunk_size = chunk_size
        self._overlap = overlap

    def chunk(self, text: str, metadata: Optional[dict] = None,
              filepath: str = "") -> list[Chunk]:
        """用滑动窗口切块"""
        meta = metadata or {}
        if not text or len(text.strip()) == 0:
            return []

        # 优先按段落切
        paragraphs = text.split("\n\n")
        chunks = []

        if len(paragraphs) > 1:
            buffer = ""
            idx = 0
            for para in paragraphs:
                para = para.strip()
                if not para:
                    continue
                if len(buffer) + len(para) + 2 > self._chunk_size and buffer:
                    chunks.append(Chunk(
                        text=buffer.strip(),
                        metadata=dict(meta),
                        chunk_index=idx,
                        chapter="节" + str(idx + 1),
                    ))
                    idx += 1
                    # 重叠：保留末尾内容
                    overlap_text = buffer[-self._overlap:] if self._overlap > 0 else ""
                    buffer = (overlap_text + "\n\n" + para) if overlap_text else para
                else:
                    buffer = (buffer + "\n\n" + para) if buffer else para

            if buffer.strip():
                chunks.append(Chunk(
                    text=buffer.strip(),
                    metadata=dict(meta),
                    chunk_index=idx,
                    chapter="节" + str(idx + 1),
                ))
        else:
            # 整段文本，用字符级滑动窗口
            start = 0
            idx = 0
            while start < len(text):
                end = min(start + self._chunk_size, len(text))
                chunk_text = text[start:end].strip()
                if chunk_text:
                    chunks.append(Chunk(
                        text=chunk_text,
                        metadata=dict(meta),
                        chunk_index=idx,
                        chapter="节" + str(idx + 1),
                    ))
                    idx += 1
                start += self._chunk_size - self._overlap

        logger.info(f"Fallback 切块: {len(chunks)} 块 (每块 ~{self._chunk_size}字符)")
        return chunks
