"""
doc_mgr — 文档管理子系统

提供文档上传、智能切块、向量入库、在线搜索、存储抽象等能力。
所有文档管理功能集中在此，不与 skills/ 对话技能混在一起。
"""

from .models import Chunk, Document, ChunkResult
from .storage import VectorStore, ChromaStore, get_store
from .engine import process_file, process_files
