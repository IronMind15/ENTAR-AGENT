"""
增强检索模块 — 混合检索（BM25 + 向量 RRF 融合）+ BGE-Reranker 重排

为 error_query / standards_query 的语义搜索路径提供统一增强入口，
输出与 chromadb query 兼容的 dict（ids/documents/metadatas/distances 嵌套结构），
其中 distances 保持「越小越相关」语义，现有格式化逻辑无需改动。

检索流水线：
  1. 向量粗召回（Chroma，Top-CANDIDATE）
  2. 混合检索（可选）：jieba 分词 + BM25 稀疏召回，与向量结果 RRF 融合
  3. 重排（可选）：bge-reranker CrossEncoder 对融合后候选精排
  4. 输出 Top-N，distance = 1 - 归一化相关分

降级策略（健壮性优先，绝不因增强失败而崩溃）：
  - jieba / rank_bm25 不可用 → 跳过混合，仅向量检索
  - bge-reranker 模型加载失败 → 跳过重排
  - 任一步异常 → 回退普通向量检索结果
"""

import logging
import os
import threading

# 国内 HuggingFace 镜像（首次加载 rerank 模型需要下载）
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

logger = logging.getLogger("enhanced_search")

# 粗召回候选数（向量 / BM25 各取这么多再融合）
_CANDIDATE_TOP_K = 20
# 送进重排器的候选上限（过多会拖慢）
_TOP_FOR_RERANK = 15
# RRF 融合常数
_RRF_K = 60
# 重排模型：中英双语 CrossEncoder，越小越快。
# 优先使用本地 models/bge-reranker-base（ModelScope 下载），
# 不存在则回退到 HuggingFace 在线名（不可用时自动降级不重排）。
_LOCAL_MODEL_DIR = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "models",
    "bge-reranker-base",
))
# 只有权重文件也下载完成才用本地路径，否则回退远程名（最终不可用也会自动降级）
_LOCAL_WEIGHT = os.path.join(_LOCAL_MODEL_DIR, "pytorch_model.bin")
_RERANK_MODEL_NAME = (
    _LOCAL_MODEL_DIR if (os.path.isdir(_LOCAL_MODEL_DIR)
                         and os.path.isfile(_LOCAL_WEIGHT))
    else "BAAI/bge-reranker-base"
)

# BM25 索引缓存（{collection|where_key: index}）
_bm25_cache: dict = {}
_bm25_lock = threading.Lock()
# 重排器单例（None=未加载，False=加载失败，其余=CrossEncoder 实例）
_reranker = None
_reranker_lock = threading.Lock()


def _get_store():
    from doc_mgr.storage import get_store
    return get_store()


def _tokenize(text: str) -> list[str]:
    """中文分词（jieba）"""
    import jieba
    return list(jieba.cut(text or ""))


def _where_key(where) -> str:
    """where 条件的缓存键"""
    return repr(where) if where else ""


def _empty_result() -> dict:
    """空结果（与 chromadb query 结构一致）"""
    return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}


def _load_bm25(collection: str, where=None):
    """构建或获取 collection 的 BM25 索引（带 where 缓存）

    Returns:
        {"ids", "docs", "metas", "bm25"} 或 None（不可用）
    """
    cache_key = f"{collection}|{_where_key(where)}"
    with _bm25_lock:
        if cache_key in _bm25_cache:
            return _bm25_cache[cache_key]

    try:
        store = _get_store()
        data = store.get(collection, where=where)
        ids = data.get("ids") or []
        docs = data.get("documents") or []
        metas = data.get("metadatas") or []
        if not ids:
            return None
        from rank_bm25 import BM25Okapi
        tokenized = [_tokenize(d or "") for d in docs]
        bm25 = BM25Okapi(tokenized)
    except ImportError:
        logger.warning("rank_bm25 未安装，混合检索降级为纯向量检索")
        return None
    except Exception as e:
        logger.warning(f"构建 BM25 索引失败，降级为纯向量检索: {e}")
        return None

    index = {"ids": ids, "docs": docs, "metas": metas, "bm25": bm25}
    with _bm25_lock:
        _bm25_cache[cache_key] = index
    return index


def _get_reranker():
    """懒加载重排模型（线程安全，失败标记为 False 不重复尝试）"""
    global _reranker
    if _reranker is None:
        with _reranker_lock:
            if _reranker is None:
                try:
                    from sentence_transformers import CrossEncoder
                    _reranker = CrossEncoder(_RERANK_MODEL_NAME)
                    logger.info(f"已加载重排模型: {_RERANK_MODEL_NAME}")
                except Exception as e:
                    logger.error(
                        f"重排模型加载失败，降级为不重排: {e}")
                    _reranker = False
    return _reranker


def _rrf_fuse(v_rank_map: dict, bm25_rank: dict) -> dict:
    """RRF 融合两个排名，返回 {id: rrf_score}（越大越相关）"""
    merged: dict = {}
    for cid in set(v_rank_map) | set(bm25_rank):
        score = 0.0
        if cid in v_rank_map:
            score += 1.0 / (_RRF_K + v_rank_map[cid] + 1)
        if cid in bm25_rank:
            score += 1.0 / (_RRF_K + bm25_rank[cid] + 1)
        merged[cid] = score
    return merged


def enhanced_query(collection: str, query_text: str, n_results: int = 5,
                   where: dict | None = None,
                   use_hybrid: bool = True,
                   use_rerank: bool = True) -> dict:
    """增强检索：混合检索（可选）→ 重排（可选）→ Chroma 兼容结果

    Args:
        collection: Chroma collection 名
        query_text: 查询文本
        n_results: 最终返回条数
        where: metadata 过滤条件（如多中心 department 过滤）
        use_hybrid: 是否启用 BM25 混合检索（不可用时自动降级）
        use_rerank: 是否启用 bge-reranker 重排（模型加载失败时自动降级）

    Returns:
        {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}
        distances 越小越相关，与 chromadb 语义一致
    """
    store = _get_store()

    # ---- 1. 向量粗召回 ----
    try:
        vector_res = store.query(
            collection, query_text=query_text,
            n_results=_CANDIDATE_TOP_K, where=where,
        )
    except Exception as e:
        logger.error(f"向量检索失败: {e}")
        return _empty_result()

    vec_ids = vector_res.get("ids", [[]])[0] or []
    if not vec_ids:
        return vector_res  # 无候选，直接返回（保持原结构）

    vec_docs = vector_res.get("documents", [[]])[0] or []
    vec_metas = vector_res.get("metadatas", [[]])[0] or []
    vec_dists = vector_res.get("distances", [[]])[0] or []

    candidates: dict = {}
    for i, cid in enumerate(vec_ids):
        candidates[cid] = {
            "doc": vec_docs[i] if i < len(vec_docs) else "",
            "meta": vec_metas[i] if i < len(vec_metas) else {},
            "v_dist": float(vec_dists[i]) if i < len(vec_dists) else 1.0,
        }

    # ---- 2. 混合检索（BM25 + RRF 融合）----
    relevance: dict = {}  # {id: 相关分，越大越相关}
    if use_hybrid:
        try:
            bm25_index = _load_bm25(collection, where=where)
            if bm25_index is not None:
                query_tokens = _tokenize(query_text)
                if query_tokens:
                    scores = bm25_index["bm25"].get_scores(query_tokens)
                    bm25_rank = {}
                    # BM25 Top-CANDIDATE 排名
                    for r, i in enumerate(
                            sorted(range(len(scores)),
                                   key=lambda i: scores[i], reverse=True)
                            [:_CANDIDATE_TOP_K]):
                        if scores[i] > 0:
                            bm25_rank[bm25_index["ids"][i]] = r

                    # 向量排名（distance 越小越靠前）
                    v_sorted = sorted(
                        candidates.items(), key=lambda kv: kv[1]["v_dist"])
                    v_rank_map = {cid: r for r, (cid, _) in enumerate(v_sorted)}

                    rrf = _rrf_fuse(v_rank_map, bm25_rank)
                    # 归一化 RRF 分到 [0,1]
                    max_rrf = max(rrf.values()) if rrf else 1.0
                    relevance = {
                        cid: (s / max_rrf if max_rrf else 0.0)
                        for cid, s in rrf.items()
                    }
        except Exception as e:
            logger.warning(f"混合检索失败，降级为纯向量: {e}")

    # 没有成功融合 → 用向量距离映射为相关分
    if not relevance:
        relevance = {
            cid: max(0.0, 1.0 - min(info["v_dist"], 1.0))
            for cid, info in candidates.items()
        }

    # ---- 3. 重排（bge-reranker 精排）----
    if use_rerank:
        reranker = _get_reranker()
        if reranker:
            try:
                # 取融合后 Top-TOP_FOR_RERANK 的候选文本送重排
                top_ids = sorted(
                    relevance, key=relevance.get, reverse=True)[:_TOP_FOR_RERANK]
                if top_ids:
                    pairs = [
                        [query_text, candidates[cid]["doc"]] for cid in top_ids
                    ]
                    rerank_scores = reranker.predict(pairs)
                    # CrossEncoder 分数越大越相关，clip 到 [0,1]
                    for cid, s in zip(top_ids, rerank_scores):
                        relevance[cid] = max(0.0, min(float(s), 1.0))
            except Exception as e:
                logger.warning(f"重排失败，保留融合/向量排序: {e}")

    # ---- 4. 输出 Top-N（保持 distance 越小越相关）----
    ranked_ids = sorted(
        relevance, key=relevance.get, reverse=True)[:n_results]
    if not ranked_ids:
        return _empty_result()

    out_ids, out_docs, out_metas, out_dists = [], [], [], []
    for cid in ranked_ids:
        info = candidates.get(cid, {"doc": "", "meta": {}})
        out_ids.append(cid)
        out_docs.append(info["doc"])
        out_metas.append(info["meta"])
        out_dists.append(round(max(0.0, 1.0 - relevance[cid]), 4))

    return {
        "ids": [out_ids],
        "documents": [out_docs],
        "metadatas": [out_metas],
        "distances": [out_dists],
    }
