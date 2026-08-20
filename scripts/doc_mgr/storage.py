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
import threading
import uuid
from abc import ABC, abstractmethod
from typing import Optional, Any

from scripts.paths import KNOWLEDGE_BASE_DIR

logger = logging.getLogger("doc_mgr.storage")

_HIDDEN_VERSION_STATES = ["staging", "retired"]


def _visible_where(where: Optional[dict] = None) -> dict:
    """组合查询条件，兼容无版本字段的旧数据并隐藏暂存/退休版本。"""
    visible = {"version_state": {"$nin": _HIDDEN_VERSION_STATES}}
    if not where:
        return visible
    return {"$and": [visible, where]}

# ===== Chroma 路径（从本文件定位到 project root） =====
CHROMA_DIR = str(KNOWLEDGE_BASE_DIR)

os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"


class VectorStore(ABC):
    """向量数据库统一接口"""

    @abstractmethod
    def add(self, collection: str, ids: list[str],
            documents: list[str], metadatas: list[dict]) -> int:
        """添加文档到指定 collection，返回本次新增条数"""
        ...

    @abstractmethod
    def replace_document(self, collection: str, doc_id: str, file_name: str,
                         ids: list[str], documents: list[str],
                         metadatas: list[dict],
                         legacy_ids: Optional[list[str]] = None) -> int:
        """完整写入新版本后替换指定文档，失败时保留旧版本。"""
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
        self._visibility_lock = threading.RLock()
        self._replace_lock = threading.RLock()

        os.makedirs(persist_dir, exist_ok=True)
        logger.info(f"ChromaStore 初始化 (持久化目录: {persist_dir})")

    def _get_embedding(self):
        """懒加载 embedding 模型（首次调用时加载 ~30MB）"""
        if self._ef is None:
            # 离线加载：模型已本地缓存，避免联网检查更新导致加载失败
            # （国内访问 HF 不稳定，联网检查会抛异常导致检索全空）
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
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
        with self._replace_lock, self._visibility_lock:
            for i in range(0, total, BATCH_SIZE):
                end = min(i + BATCH_SIZE, total)
                coll.add(
                    ids=new_ids[i:end],
                    documents=new_docs[i:end],
                    metadatas=new_metas[i:end],
                )

        logger.info(f"  [增量] 写入 {total} 条（{len(ids) - total} 条已存在跳过）")
        return total

    def replace_document(self, collection: str, doc_id: str, file_name: str,
                         ids: list[str], documents: list[str],
                         metadatas: list[dict],
                         legacy_ids: Optional[list[str]] = None) -> int:
        """串行执行文档换版，避免并发任务留下多个 active 版本。"""
        replace_lock = getattr(self, "_replace_lock", self._visibility_lock)
        with replace_lock:
            return self._replace_document_locked(
                collection, doc_id, file_name, ids, documents, metadatas,
                legacy_ids=legacy_ids,
            )

    def _replace_document_locked(
        self, collection: str, doc_id: str, file_name: str,
        ids: list[str], documents: list[str], metadatas: list[dict],
        legacy_ids: Optional[list[str]] = None,
    ) -> int:
        """以两阶段方式替换一份文档。

        新版本先作为 staging 写入并校验；切换阶段由进程内锁保护，
        先激活新版本，再将旧版本标记 retired 并删除。任何写入或切换
        异常都会尽量清理新版本并恢复旧版本可见性。
        """
        if not doc_id:
            raise ValueError("replace_document 缺少 doc_id")
        if not ids or not (len(ids) == len(documents) == len(metadatas)):
            raise ValueError("replace_document 的切块数据为空或长度不一致")
        if len(set(ids)) != len(ids):
            raise ValueError("replace_document 收到重复切块 ID")

        coll = self._get_collection(collection)
        attempt = uuid.uuid4().hex[:12]
        version_id = str(metadatas[0].get("version_id", ""))
        stage_ids = [
            f"{doc_id}:{version_id[:16]}:{attempt}:{index:06d}"
            for index in range(len(ids))
        ]
        stage_metas = []
        for meta in metadatas:
            staged = dict(meta)
            staged.update({
                "doc_id": doc_id,
                "file_name": file_name,
                "version_id": version_id,
                "version_state": "staging",
            })
            stage_metas.append(staged)

        old_ids: list[str] = []
        old_meta_by_id: dict[str, dict] = {}

        def collect_old(result: dict) -> None:
            result_ids = result.get("ids", []) if result else []
            result_metas = result.get("metadatas", []) if result else []
            for index, existing_id in enumerate(result_ids):
                if existing_id in stage_ids or existing_id in old_meta_by_id:
                    continue
                meta = result_metas[index] if index < len(result_metas) else {}
                meta = dict(meta or {})
                existing_doc_id = meta.get("doc_id", "")
                if existing_doc_id and existing_doc_id != doc_id:
                    continue
                old_ids.append(existing_id)
                old_meta_by_id[existing_id] = meta

        collect_old(coll.get(where={"doc_id": doc_id}))
        collect_old(coll.get(where={"file_name": file_name}))
        if legacy_ids:
            collect_old(coll.get(ids=list(dict.fromkeys(legacy_ids))))

        batch_size = 50
        written_stage_ids: list[str] = []
        try:
            for start in range(0, len(stage_ids), batch_size):
                end = min(start + batch_size, len(stage_ids))
                coll.add(
                    ids=stage_ids[start:end],
                    documents=documents[start:end],
                    metadatas=stage_metas[start:end],
                )
                written_stage_ids.extend(stage_ids[start:end])

            verified = coll.get(ids=stage_ids)
            if len(verified.get("ids", [])) != len(stage_ids):
                raise RuntimeError(
                    f"新版本写入校验失败: {len(verified.get('ids', []))}/{len(stage_ids)}"
                )
        except Exception:
            if written_stage_ids:
                try:
                    coll.delete(ids=written_stage_ids)
                except Exception:
                    logger.exception("清理失败的新版本暂存块时发生异常")
            raise

        active_metas = []
        for meta in stage_metas:
            active = dict(meta)
            active["version_state"] = "active"
            active_metas.append(active)

        retired_ids: list[str] = []
        try:
            with self._visibility_lock:
                for start in range(0, len(stage_ids), batch_size):
                    end = min(start + batch_size, len(stage_ids))
                    coll.update(
                        ids=stage_ids[start:end],
                        metadatas=active_metas[start:end],
                    )

                for start in range(0, len(old_ids), batch_size):
                    batch_ids = old_ids[start:start + batch_size]
                    retired_metas = []
                    for existing_id in batch_ids:
                        retired = dict(old_meta_by_id[existing_id])
                        retired["version_state"] = "retired"
                        retired_metas.append(retired)
                    coll.update(ids=batch_ids, metadatas=retired_metas)
                    retired_ids.extend(batch_ids)

                if old_ids:
                    try:
                        coll.delete(ids=old_ids)
                    except Exception:
                        logger.exception("旧版本已隐藏，但物理删除失败，需后续清理")
        except Exception:
            with self._visibility_lock:
                if retired_ids:
                    try:
                        original_metas = [old_meta_by_id[item] for item in retired_ids]
                        coll.update(ids=retired_ids, metadatas=original_metas)
                    except Exception:
                        logger.exception("恢复旧版本 metadata 失败")
                try:
                    coll.delete(ids=stage_ids)
                except Exception:
                    logger.exception("回滚新版本失败")
            raise

        logger.info(
            f"  [换版] {file_name}: 新版本 {len(stage_ids)} 块，旧版本 {len(old_ids)} 块"
        )
        return len(stage_ids)

    def query(self, collection: str, query_text: str,
              n_results: int = 5, where: Optional[dict] = None) -> dict:
        coll = self._get_collection(collection)
        kwargs = {"query_texts": [query_text], "n_results": n_results}
        kwargs["where"] = _visible_where(where)
        try:
            with self._visibility_lock:
                return coll.query(**kwargs)
        except Exception as e:
            logger.exception(f"Chroma 语义搜索失败: {e}")
            return {"documents": [[]], "metadatas": [[]], "distances": [[]]}

    def get(self, collection: str, ids: Optional[list[str]] = None,
            where: Optional[dict] = None, include_hidden: bool = False) -> dict:
        coll = self._get_collection(collection)
        kwargs: dict = {}
        if ids:
            kwargs["ids"] = ids
        # include_hidden=True 时不叠加可见性过滤，返回含 staging/retired 的全部版本
        # （删除学习等场景需要：否则 retired 残留会被崩溃恢复误恢复为 active，造成删除复活）
        kwargs["where"] = where if include_hidden else _visible_where(where)
        try:
            with self._visibility_lock:
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
            kwargs["where"] = _visible_where(where)
        if not kwargs:
            logger.warning("delete 调用缺少 ids 或 where 参数，跳过")
            return 0
        try:
            with self._replace_lock, self._visibility_lock:
                coll.delete(**kwargs)
            return len(kwargs.get("ids", []))
        except Exception as e:
            logger.exception(f"Chroma delete 失败: {e}")
            return 0

    def get_raw(self, collection: str,
                where: Optional[dict] = None) -> dict:
        """读取原始块数据（含 staging/retired），崩溃恢复用，不做可见性过滤。"""
        coll = self._get_collection(collection)
        kwargs: dict = {}
        if where:
            kwargs["where"] = where
        try:
            with self._visibility_lock:
                return coll.get(**kwargs)
        except Exception as e:
            logger.exception(f"Chroma get_raw 失败: {e}")
            return {"ids": [], "documents": [], "metadatas": []}

    def update_metadata(self, collection: str, ids: list[str],
                        metadatas: list[dict]) -> None:
        """批量更新 metadata（崩溃恢复用，需传完整 metadata，Chroma 为覆盖式）。"""
        coll = self._get_collection(collection)
        with self._visibility_lock:
            coll.update(ids=ids, metadatas=metadatas)

    def count(self, collection: str) -> int:
        coll = self._get_collection(collection)
        with self._visibility_lock:
            return len(coll.get(where=_visible_where()).get("ids", []))

    def list_collections(self) -> list[str]:
        return [c.name for c in self._client.list_collections()]


# ===== 全局单例 =====
_store: Optional[VectorStore] = None
_store_lock = threading.Lock()


def get_store(store_type: str = "chroma") -> VectorStore:
    """获取存储实例（单例，线程安全双检锁）

    整个应用共享同一个 store。v1.6.0 起消息处理放线程池，
    首次调用可能在多线程下并发加载 ChromaStore（含 embedding 模型，
    初始化较重），加锁避免重复实例化（v1.6.1）。

    Args:
        store_type: 存储类型，当前仅支持 "chroma"

    Returns:
        VectorStore 实例
    """
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                if store_type == "chroma":
                    _store = ChromaStore()
                else:
                    raise ValueError(f"不支持的存储类型: {store_type}")
    return _store
