"""
自动同步调度器

每 24 小时扫描 data/uploads/ 和 data/standards/ 目录，
发现新文件或已修改文件时，自动调用 engine.process_file() 入库。

基于 APScheduler 的 BackgroundScheduler 实现，
在独立线程中运行，与 uvicorn asyncio 事件循环无冲突。

启动后 60 秒执行首次扫描（方便调试），之后每 24h 执行。
"""

import datetime
import logging
import os

from apscheduler.schedulers.background import BackgroundScheduler

from .engine import process_file, check_chroma_has_file
from .sync_tracker import SyncTracker
from .identity import file_sha256
from scripts.paths import STANDARDS_DIR, UPLOADS_DIR

logger = logging.getLogger("doc_mgr.scheduler")

# 扫描配置：{相对目录: (默认 collection)}
SCAN_DIRS = {
    str(UPLOADS_DIR): "standards",
    str(STANDARDS_DIR): "standards",
}
# 注意：fault_codes/ 由手动上传，不自动扫描


def _resolve(relative: str) -> str:
    """将相对路径解析为绝对路径"""
    if os.path.isabs(relative):
        return os.path.normpath(relative)
    project_root = os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", ".."))
    return os.path.normpath(os.path.join(project_root, relative))


def _compute_hash(file_path: str) -> str:
    """使用文件内容 SHA-256 检测变化。"""
    try:
        return file_sha256(file_path)
    except OSError:
        return ""


def _is_supported_file(fname: str) -> bool:
    """判断文件类型是否支持自动同步"""
    ext = os.path.splitext(fname)[1].lower()
    return ext in (".pdf", ".xlsx", ".xls", ".md")


def _scan_and_sync(force: bool = False):
    """扫描目录，同步新文件/变更文件

    Args:
        force: 为 True 时跳过 Chroma 预检查，强制重新入库
    """
    mode = "强制全量同步" if force else "扫描增量同步"
    logger.info(f"[自动同步] 开始{mode}...")
    tracker = SyncTracker()

    for rel_dir, default_collection in SCAN_DIRS.items():
        abs_dir = _resolve(rel_dir)
        if not os.path.isdir(abs_dir):
            logger.warning(f"  目录不存在, 跳过: {abs_dir}")
            continue

        logger.info(f"  扫描: {abs_dir}")
        for root, dirs, files in os.walk(abs_dir):
            # 跳过 MinerU 中间产物目录
            if "mineru_output" in root:
                continue
            for fname in sorted(files):
                if not _is_supported_file(fname):
                    continue

                fpath = os.path.join(root, fname)
                try:
                    fsize = os.path.getsize(fpath)
                    fhash = _compute_hash(fpath)
                except OSError as e:
                    logger.warning(f"  无法访问: {fname} ({e})")
                    continue

                # 预检查 1: sync_status 表里有没有且无变化？
                existing = tracker.get_status(fpath)
                if existing:
                    if (existing.get("sync_status") == "synced"
                            and existing.get("file_hash") == fhash
                            and not force):
                        continue  # 无变化，跳过
                    legacy_hash = str(existing.get("file_hash", ""))
                    current_mtime = str(int(os.path.getmtime(fpath)))
                    if (existing.get("sync_status") == "synced"
                            and legacy_hash.isdigit()
                            and legacy_hash == current_mtime
                            and not force):
                        target = existing.get("target_collection") or default_collection
                        tracker.upsert_file(fpath, fname, fsize, fhash, target)
                        tracker.mark_synced(fpath)
                        logger.info(f"  哈希记录升级为 SHA-256（内容未重新处理）: {fname}")
                        continue
                    logger.info(f"  变更/重试: {fname}")

                # 预检查 2: Chroma 里是否已有这个文件的切块？（仅非强制模式）
                if not force and existing is None:
                    # 判断 target_collection
                    target = default_collection
                    if check_chroma_has_file(target, fname):
                        logger.info(f"  Chroma 已有 {fname}，标记已同步（跳过处理）")
                        tracker.upsert_file(fpath, fname, fsize, fhash, target)
                        tracker.mark_synced(fpath)
                        continue

                # 判断 target_collection
                target = default_collection

                # 记录待处理
                tracker.upsert_file(fpath, fname, fsize, fhash, target)

                # 执行同步（沿用记录中已有的建议部门，避免批量/调度路径
                # 把上传时指定的部门静默清成 public）
                department = (
                    existing.get("suggested_department") or "public"
                    if existing else "public"
                )

                try:
                    doc = process_file(fpath, file_name=fname,
                                       target_collection=target,
                                       force=force,
                                       department=department)
                    if doc.status in ("done", "skipped"):
                        tracker.mark_synced(fpath)
                        logger.info(f"  ✅ {fname} → {doc.chunk_count} 块（来源: {doc.source}）")
                    else:
                        msg = doc.message or "处理返回异常状态"
                        tracker.mark_error(fpath, msg)
                        logger.warning(f"  ⚠️ {fname}: {msg}")
                except Exception as e:
                    tracker.mark_error(fpath, str(e))
                    logger.error(f"  ❌ {fname}: {e}")

    logger.info(f"[自动同步] {mode}完成")


# ===== 调度器生命周期管理 =====

_scheduler: BackgroundScheduler = None


def start_scheduler():
    """启动后台调度器（在 main.py 中调用）"""
    global _scheduler
    if _scheduler is not None:
        return

    _scheduler = BackgroundScheduler(daemon=True)

    # 24h 间隔任务（首次不立即执行）
    _scheduler.add_job(
        _scan_and_sync,
        "interval",
        hours=24,
        next_run_time=None,
        id="auto_sync_24h",
        name="24h 自动同步",
    )

    # 首次扫描在启动后 60 秒触发
    first_run = datetime.datetime.now() + datetime.timedelta(seconds=60)
    _scheduler.add_job(
        _scan_and_sync,
        "date",
        run_date=first_run,
        id="auto_sync_first",
        name="首次自动同步",
    )

    _scheduler.start()
    logger.info(f"后台调度器已启动")
    logger.info(f"  首次自动同步: {first_run.strftime('%H:%M:%S')}")
    logger.info(f"  之后每 24h 执行一次")


def stop_scheduler():
    """停止调度器"""
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("后台调度器已停止")


def trigger_manual_sync(force: bool = False) -> dict:
    """手动触发一次同步（管理员调用），返回结果统计

    Args:
        force: True=跳过 Chroma 预检，强制重新入库
    """
    mode = "强制同步" if force else "增量同步"
    logger.info(f"[手动同步] 由管理员触发（{mode}）")
    _scan_and_sync(force=force)
    tracker = SyncTracker()
    return tracker.get_stats()
