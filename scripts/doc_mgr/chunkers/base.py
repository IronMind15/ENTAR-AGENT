"""
切块器基类

所有切块器继承 Chunker 基类，统一 chunk() 接口。
"""

from abc import ABC, abstractmethod
from typing import Optional
from ..models import Chunk


class Chunker(ABC):
    """切块器抽象基类"""

    @abstractmethod
    def chunk(self, text: str, metadata: Optional[dict] = None,
              filepath: str = "") -> list[Chunk]:
        """将文本切分为 Chunk 列表

        Args:
            text: 文档全文（从 extractor 提取的纯文本）
            metadata: 基础 metadata，会合并到每个 chunk
            filepath: 源文件路径（某些 chunker 需要重新读取文件）

        Returns:
            Chunk 列表
        """
        ...

    @staticmethod
    def merge_small_chunks(chunks: list[Chunk], min_chars: int = 50) -> list[Chunk]:
        """合并相邻过小的 chunk"""
        if not chunks:
            return chunks
        result = [chunks[0]]
        for ch in chunks[1:]:
            if len(result[-1].text) < min_chars:
                result[-1].text += "\n\n" + ch.text
                result[-1].metadata.update(ch.metadata)
            else:
                result.append(ch)
        return result

    @staticmethod
    def split_oversized_chunks(chunks: list[Chunk], max_chars: int = 3000) -> list[Chunk]:
        """拆分过大的 chunk（按段落拆分）"""
        result = []
        for ch in chunks:
            if len(ch.text) <= max_chars:
                result.append(ch)
                continue
            # 按段落再切分
            paragraphs = ch.text.split("\n\n")
            buffer = ""
            for para in paragraphs:
                if len(buffer) + len(para) + 1 > max_chars and buffer:
                    result.append(Chunk(
                        text=buffer.strip(),
                        metadata=dict(ch.metadata),
                    ))
                    buffer = para
                else:
                    buffer = (buffer + "\n\n" + para) if buffer else para
            if buffer.strip():
                result.append(Chunk(text=buffer.strip(), metadata=dict(ch.metadata)))
        return result
