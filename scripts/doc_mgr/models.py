"""
doc_mgr 数据模型

统一的数据结构，贯穿整个文档管理流程：上传 → 提取 → 切块 → 入库 → 搜索。
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Chunk:
    """一个切块结果

    每个 Chunk 代表一段可独立入库的文本 + 其 metadata。
    """
    text: str
    metadata: dict = field(default_factory=dict)
    doc_id: str = ""       # 所属文档唯一 ID
    chunk_index: int = 0   # 块序号
    chapter: str = ""      # 章节号（如 "1"、"3.1"）
    chapter_title: str = ""  # 章节标题
    level: int = 0         # 标题层级（1=章, 2=节, 3=小节, 0=未识别）


@dataclass
class Document:
    """上传文档的处理记录"""
    file_name: str = ""
    collection: str = ""       # 存入的 collection 名
    file_path: str = ""
    file_size: int = 0
    chunk_count: int = 0
    status: str = ""           # pending | processing | done | error | ocr_needed
    std_id: str = ""           # 标准编号（PDF 特有）
    std_title: str = ""        # 标准名称（PDF 特有）
    source: str = ""           # 处理来源: mineru | pymupdf | excel | markdown
    message: str = ""          # 处理信息/错误信息


@dataclass
class ChunkResult:
    """搜索匹配结果"""
    text: str
    metadata: dict = field(default_factory=dict)
    score: float = 0.0
    match_type: str = "semantic"  # exact | semantic
