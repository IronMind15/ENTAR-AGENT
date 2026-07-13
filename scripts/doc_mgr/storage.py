"""
存储抽象层

定义 VectorStore 接口，当前实现为 ChromaStore。
将来切换 Qdrant / Milvus 只需：
  1. 实现 VectorStore 接口
  2. 改 get_store() 一行

整个应用共用同一个 store 实例（单例），避免加载多个 embedding 模型浪费内存。
"""

import os
import re
import logging
from abc import ABC, abstractmethod
from typing import Optional, Any

logger = logging.getLogger("doc_mgr.storage")

# ===== Chroma 路径（从本文件定位到 project root） =====
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.join(_SCRIPT_DIR, "..")
CHROMA_DIR = os.path.join(_PROJECT_ROOT, "..", "knowledge_base")

os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"


class VectorStore(ABC):
    """向量数据库统一接口"""

    @abstractmethod
    def add(self, collection: str, ids: list[str],
            documents: list[str], metadatas: list[dict]) -> int:
        """添加文档到指定 collection，返回本次新增条数"""
        ...

    @abstractmethod
    def query(self, collection: str, query_text: str,
              n_results: int = 5, where: Optional[dict] = None) -> dict:
        """语义搜索"""
        ...

    @abstractmethod
    def get(self, collection: str, ids: Optional[list[str]] = None,
            where: Optional[dict] = None) -> dict:
        """按 ID 或条件获取文档"""
        ...

    @abstractmethod
    def delete(self, collection: str, ids: Optional[list[str]] = None,
               where: Optional[dict] = None) -> int:
        """删除文档，返回删除条数"""
        ...

    @abstractmethod
    def count(self, collection: str) -> int:
        """获取 collection 中的文档总数"""
        ...

    @abstractmethod
    def list_collections(self) -> list[str]:
        """列出所有 collection 名称"""
        ...


class ChromaStore(VectorStore):
    """Chroma 实现"""

    def __init__(self, persist_dir: str = CHROMA_DIR,
                 model_name: str = "BAAI/bge-small-zh-v1.5"):
        import chromadb
        from chromadb import PersistentClient
        from chromadb.utils import embedding_functions

        self._persist_dir = persist_dir
        self._model_name = model_name
        self._client = PersistentClient(path=persist_dir)
        self._ef: Any = None  # 懒加载
        self._collection_cache: dict[str, Any] = {}

        os.makedirs(persist_dir, exist_ok=True)
        logger.info(f"ChromaStore 初始化 (持久化目录: {persist_dir})")

    def _get_embedding(self):
        """懒加载 embedding 模型（首次调用时加载 ~30MB）"""
        if self._ef is None:
            from chromadb.utils import embedding_functions
            logger.info("首次使用，加载 embedding 模型 BAAI/bge-small-zh-v1.5（~30MB）...")
            self._ef = embedding_functions.SentenceTransformerEmbeddingFunction(
                model_name=self._model_name
            )
        return self._ef

    def _get_collection(self, name: str):
        """获取或创建 collection（带缓存）"""
        if name not in self._collection_cache:
            import chromadb
            ef = self._get_embedding()
            try:
                coll = self._client.get_collection(name, embedding_function=ef)
                logger.info(f"  连接已有 collection: {name}（{coll.count()} 条）")
            except (ValueError, chromadb.errors.NotFoundError):
                coll = self._client.create_collection(
                    name=name,
                    embedding_function=ef,
                    metadata={"hnsw:space": "cosine"},
                )
                logger.info(f"  新建 collection: {name}")
            self._collection_cache[name] = coll
        return self._collection_cache[name]

    def add(self, collection: str, ids: list[str],
            documents: list[str], metadatas: list[dict]) -> int:
        """写入文档（增量模式，跳过已存在的 ID）

        Returns:
            实际新增的条数
        """
        coll = self._get_collection(collection)

        # 跳过已存在的 ID
        existing = coll.get(ids=ids)
        existing_ids = set(existing["ids"]) if existing and existing.get("ids") else set()
        new_ids, new_docs, new_metas = [], [], []
        for i, doc_id in enumerate(ids):
            if doc_id not in existing_ids:
                new_ids.append(doc_id)
                new_docs.append(documents[i])
                new_metas.append(metadatas[i])

        if not new_ids:
            logger.info(f"  [增量] 全部 {len(ids)} 条已存在，跳过")
            return 0

        # 分批写入（每批 50 条，避免 Chroma 大量写入卡住）
        BATCH_SIZE = 50
        total = len(new_ids)
        for i in range(0, total, BATCH_SIZE):
            end = min(i + BATCH_SIZE, total)
            coll.add(
                ids=new_ids[i:end],
                documents=new_docs[i:end],
                metadatas=new_metas[i:end],
            )

        logger.info(f"  [增量] 写入 {total} 条（{len(ids) - total} 条已存在跳过）")
        return total

    def query(self, collection: str, query_text: str,
              n_results: int = 5, where: Optional[dict] = None) -> dict:
        coll = self._get_collection(collection)
        kwargs = {"query_texts": [query_text], "n_results": n_results}
        if where:
            kwargs["where"] = where
        try:
            return coll.query(**kwargs)
        except Exception as e:
            logger.exception(f"Chroma 语义搜索失败: {e}")
            return {"documents": [[]], "metadatas": [[]], "distances": [[]]}

    def get(self, collection: str, ids: Optional[list[str]] = None,
            where: Optional[dict] = None) -> dict:
        coll = self._get_collection(collection)
        kwargs: dict = {}
        if ids:
            kwargs["ids"] = ids
        if where:
            kwargs["where"] = where
        try:
            return coll.get(**kwargs)
        except Exception as e:
            logger.exception(f"Chroma get 失败: {e}")
            return {"ids": [], "documents": [], "metadatas": []}

    def delete(self, collection: str, ids: Optional[list[str]] = None,
               where: Optional[dict] = None) -> int:
        coll = self._get_collection(collection)
        kwargs: dict = {}
        if ids:
            kwargs["ids"] = ids
        if where:
            kwargs["where"] = where
        if not kwargs:
            logger.warning("delete 调用缺少 ids 或 where 参数，跳过")
            return 0
        try:
            coll.delete(**kwargs)
            return len(kwargs.get("ids", []))
        except Exception as e:
            logger.exception(f"Chroma delete 失败: {e}")
            return 0

    def count(self, collection: str) -> int:
        coll = self._get_collection(collection)
        return coll.count()

    def list_collections(self) -> list[str]:
        return [c.name for c in self._client.list_collections()]


# ===== 全局单例 =====
_store: Optional[VectorStore] = None


def get_store(store_type: str = "chroma") -> VectorStore:
    """获取存储实例（单例，整个应用共享同一个 store）

    Args:
        store_type: 存储类型，当前仅支持 "chroma"

    Returns:
        VectorStore 实例
    """
    global _store
    if _store is None:
        if store_type == "chroma":
            _store = ChromaStore()
        else:
            raise ValueError(f"不支持的存储类型: {store_type}")
    return _store
