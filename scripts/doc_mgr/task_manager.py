"""
后台任务管理器

管理异步文件处理任务，支持提交、状态查询、进度追踪。
使用 ThreadPoolExecutor 执行，避免阻塞 FastAPI 事件循环。

进度追踪：
  内部模块通过 report_progress() 上报进度（基于线程本地存储），
  不污染函数签名，跨任意深度调用链自动关联到当前任务。

用法：
    manager = get_manager()
    record = manager.submit(file_name, collection, process_fn)
    status = manager.get_status(task_id)
"""

import uuid
import time
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

logger = logging.getLogger("doc_mgr.task_manager")

# 任务状态常量
TASK_PENDING = "pending"
TASK_PROCESSING = "processing"
TASK_DONE = "done"
TASK_ERROR = "error"
TASK_TIMEOUT = "timeout"

# 线程本地存储 — 用于跨函数调用链上报进度
_local = threading.local()


def _get_current_task_id() -> Optional[str]:
    """获取当前线程正在处理的任务 ID"""
    return getattr(_local, "task_id", None)


def _set_current_task_id(task_id: Optional[str]):
    """设置当前线程的任务 ID"""
    _local.task_id = task_id


def report_progress(step: str, progress: int, message: str):
    """从任意深度调用链上报进度（自动关联当前任务）

    Args:
        step: 步骤标识（如 'mineru_upload', 'chunking', 'indexing'）
        progress: 进度百分比 0-100
        message: 人类可读的描述（如 "正在上传到 MinerU..."）
    """
    task_id = _get_current_task_id()
    if task_id:
        manager = get_manager()
        manager.update_progress(task_id, step, progress, message)


class TaskRecord:
    """单个后台任务记录"""

    __slots__ = (
        "task_id", "file_name", "collection", "status",
        "step", "progress", "progress_text", "result", "error",
        "created_at", "updated_at",
    )

    def __init__(self, file_name: str, collection: str):
        self.task_id = uuid.uuid4().hex[:12]
        self.file_name = file_name
        self.collection = collection
        self.status = TASK_PENDING
        self.step = ""               # 当前步骤标识
        self.progress = 0            # 进度 0-100
        self.progress_text = "排队等待..."  # 进度描述
        self.result: Optional[dict] = None
        self.error: Optional[str] = None
        self.created_at = time.time()
        self.updated_at = time.time()


class TaskManager:
    """后台任务管理器（全局单例）

    使用有界线程池执行文件处理任务，线程安全。
    """

    def __init__(self, max_workers: int = 2):
        self._tasks: dict[str, TaskRecord] = {}
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._lock = threading.Lock()

    def submit(self, file_name: str, collection: str,
               process_fn: Callable) -> TaskRecord:
        """提交一个后台处理任务

        Args:
            file_name: 文件名（仅用于记录）
            collection: 目标 collection
            process_fn: 实际处理函数（无参，返回 Document）

        Returns:
            TaskRecord（已包含 task_id）
        """
        record = TaskRecord(file_name, collection)
        with self._lock:
            self._tasks[record.task_id] = record

        self._executor.submit(self._run_task, record, process_fn)
        logger.info(f"[任务] 已提交: {record.task_id} → {file_name}")
        return record

    def _run_task(self, record: TaskRecord, process_fn: Callable):
        """在线程池中执行实际处理"""
        # 设置线程本地任务 ID，使 report_progress() 能自动关联
        _set_current_task_id(record.task_id)
        try:
            self.update_progress(record.task_id, "start", 5, "开始处理...")

            # 执行处理（同步调用，可能耗时较长）
            doc = process_fn()

            # 处理完成
            with self._lock:
                record.status = TASK_DONE
                record.progress = 100
                record.step = "done"
                record.progress_text = "处理完成"
                record.result = {
                    "status": doc.status,
                    "file_name": doc.file_name,
                    "collection": doc.collection,
                    "chunk_count": doc.chunk_count,
                    "source": doc.source or "",
                    "std_id": doc.std_id or "",
                    "std_title": doc.std_title or "",
                    "message": doc.message or "",
                }
                record.updated_at = time.time()

            logger.info(f"[任务] 完成: {record.task_id} → "
                        f"{doc.file_name} ({doc.chunk_count} 块)")

        except Exception as e:
            logger.exception(f"[任务] 失败: {record.task_id} → {record.file_name}")
            with self._lock:
                record.status = TASK_ERROR
                record.progress = 0
                record.step = "error"
                record.progress_text = str(e)[:200]
                record.error = str(e)
                record.updated_at = time.time()
        finally:
            _set_current_task_id(None)

    def update_progress(self, task_id: str, step: str,
                        progress: int, message: str):
        """更新任务进度（线程安全）"""
        with self._lock:
            record = self._tasks.get(task_id)
            if record:
                record.step = step
                record.progress = min(progress, 99)  # 100 留给 done
                record.progress_text = message
                record.updated_at = time.time()

    def get_status(self, task_id: str) -> Optional[dict]:
        """查询任务状态（线程安全）"""
        with self._lock:
            record = self._tasks.get(task_id)
            if not record:
                return None
            return {
                "task_id": record.task_id,
                "file_name": record.file_name,
                "collection": record.collection,
                "status": record.status,
                "step": record.step,
                "progress": record.progress,
                "progress_text": record.progress_text,
                "result": record.result,
                "error": record.error,
                "created_at": record.created_at,
                "updated_at": record.updated_at,
            }

    def get_recent_tasks(self, limit: int = 20) -> list[dict]:
        """获取最近任务列表（按创建时间倒序）"""
        with self._lock:
            tasks = sorted(
                self._tasks.values(),
                key=lambda r: r.created_at,
                reverse=True,
            )[:limit]
            return [
                {
                    "task_id": r.task_id,
                    "file_name": r.file_name,
                    "collection": r.collection,
                    "status": r.status,
                    "step": r.step,
                    "progress": r.progress,
                    "progress_text": r.progress_text,
                    "error": r.error,
                    "created_at": r.created_at,
                    "updated_at": r.updated_at,
                }
                for r in tasks
            ]

    def cleanup(self, max_age: int = 600):
        """清理已完成/失败的任务记录

        Args:
            max_age: 保留时间（秒），默认 10 分钟
        """
        now = time.time()
        with self._lock:
            expired = [
                tid for tid, rec in self._tasks.items()
                if rec.status in (TASK_DONE, TASK_ERROR)
                and now - rec.updated_at > max_age
            ]
            for tid in expired:
                del self._tasks[tid]
        if expired:
            logger.info(f"[任务] 清理了 {len(expired)} 个过期任务")


# ===== 全局单例 =====

_manager: Optional[TaskManager] = None


def get_manager() -> TaskManager:
    """获取全局 TaskManager 单例"""
    global _manager
    if _manager is None:
        _manager = TaskManager()
    return _manager
