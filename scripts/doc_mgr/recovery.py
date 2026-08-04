"""
崩溃恢复模块

doc_mgr 的文档版本替换（storage.replace_document）采用两阶段写入：
新版本先以 staging 状态分批写入并校验 → 切换为 active → 旧版本标记 retired → 物理删除。
若进程在这期间崩溃，会在 Chroma 里留下状态残留：

  1. staging 块（写入中途/切换前崩溃）——未完成替换的半成品
  2. 同一 doc 存在多个 active 版本（切换中途崩溃，新旧同时可见）——查询会重复
  3. retired 块（旧版本已隐藏但物理删除未执行）——持续占空间
  4. 仅有 retired 而无 active（极端情况）——存在数据丢失风险

本模块在启动时扫描所有 collection，按 doc_id → version_id 分组修复上述残留：

  - 含 staging 的版本组：未完成切换，整组作废（连同已激活的部分残块）
  - 多 active 版本组：保留块数最多的一个，其余删除
  - 存在 active 版本时：清理所有 retired / 状态混合组（旧版本残留）
  - 无 active 版本时：把 retired / 混合组恢复为 active（防数据丢失）

执行策略保守：只处理带 doc_id 且有版本机制的块，legacy 老数据（无版本字段）不受影响。
"""

import logging

logger = logging.getLogger("doc_mgr.recovery")


def plan_recovery(blocks) -> dict:
    """纯逻辑：分析一组块，规划恢复操作（不直接操作存储）。

    Args:
        blocks: 可迭代的 (id, metadata) 元组

    Returns:
        dict:
            delete_ids: list[str] 应物理删除的块 ID
            activate:   list[(id, 完整metadata)] 应恢复为 active 的块（metadata 已改好）
    """
    delete_ids: list[str] = []
    activate: list[tuple[str, dict]] = []

    # 按 doc_id 分组（无 doc_id 的 legacy 数据不参与版本机制，跳过）
    by_doc: dict[str, list] = {}
    for item_id, meta in blocks:
        doc_id = (meta or {}).get("doc_id")
        if not doc_id:
            continue
        by_doc.setdefault(doc_id, []).append((item_id, meta))

    for doc_id, doc_blocks in by_doc.items():
        # 按 version_id 分组
        by_ver: dict[str, list] = {}
        for item_id, meta in doc_blocks:
            vid = (meta or {}).get("version_id", "")
            by_ver.setdefault(vid, []).append((item_id, meta))

        remaining: dict[str, list] = {}

        # Step 1: 含 staging 的版本组 → 未完成切换，整组作废（含已激活的部分残块）
        for vid, ver_blocks in by_ver.items():
            states = {b[1].get("version_state") for b in ver_blocks}
            if "staging" in states:
                delete_ids.extend(b[0] for b in ver_blocks)
            else:
                remaining[vid] = ver_blocks

        # Step 2: 分类剩余版本组
        active_groups: list[list] = []
        retired_groups: list[list] = []
        mixed_groups: list[list] = []
        for vid, ver_blocks in remaining.items():
            states = {b[1].get("version_state") for b in ver_blocks}
            if states == {"active"}:
                active_groups.append(ver_blocks)
            elif states == {"retired"}:
                retired_groups.append(ver_blocks)
            else:  # 组内状态混合（active+retired），崩溃在批量 retired 中途
                mixed_groups.append(ver_blocks)

        if active_groups:
            # 2a: 存在可见版本
            if len(active_groups) > 1:
                # 多 active 并存 → 保留块数最多的版本，其余删除
                keep = max(active_groups, key=len)
                for g in active_groups:
                    if g is not keep:
                        delete_ids.extend(b[0] for b in g)
                        logger.warning(
                            f"[恢复][doc {doc_id[:16]}] 清理多余 active 版本 "
                            f"({len(g)} 块)，保留块数更多的版本")
            # 清理所有 retired / 混合组（旧版本残留 + 崩溃残块）
            for g in retired_groups + mixed_groups:
                delete_ids.extend(b[0] for b in g)
        else:
            # 2b: 无 active 版本，只有 retired/混合 → 数据丢失风险，恢复为 active
            for g in retired_groups + mixed_groups:
                for item_id, meta in g:
                    restored = dict(meta)
                    restored["version_state"] = "active"
                    activate.append((item_id, restored))
                logger.warning(
                    f"[恢复][doc {doc_id[:16]}] 恢复 {len(g)} 个块为 active"
                    "（无可见版本，防数据丢失）")

    return {"delete_ids": delete_ids, "activate": activate}


def recover_crashed_data(store, collections=None) -> dict:
    """扫描所有 collection，清理/恢复遗留版本状态残留，返回统计。

    由 main.py 启动时调用。失败不中断启动，仅记录日志。

    Returns:
        {collection: {"deleted": 物理删除块数, "restored": 恢复为 active 块数}}
    """
    stats: dict = {}
    if collections is None:
        try:
            collections = store.list_collections()
        except Exception:
            logger.exception("[恢复] 获取 collection 列表失败")
            return stats

    for coll in collections:
        try:
            data = store.get_raw(coll)
            ids = data.get("ids", []) or []
            metas = data.get("metadatas", []) or []
            plan = plan_recovery(list(zip(ids, metas)))

            deleted = restored = 0
            if plan["delete_ids"]:
                deleted = store.delete(coll, ids=plan["delete_ids"])
                logger.warning(f"[恢复][{coll}] 清理遗留块 {deleted} 个")
            if plan["activate"]:
                act_ids = [b[0] for b in plan["activate"]]
                act_metas = [b[1] for b in plan["activate"]]
                store.update_metadata(coll, act_ids, act_metas)
                restored = len(act_ids)
                logger.warning(f"[恢复][{coll}] 恢复 {restored} 个块为 active")
            stats[coll] = {"deleted": deleted, "restored": restored}
        except Exception:
            logger.exception(f"[恢复][{coll}] 处理失败")
            stats[coll] = {"deleted": 0, "restored": 0, "error": True}

    return stats
