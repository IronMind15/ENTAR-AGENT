"""钉钉上传文件的知识库审核工作流。

当前采用 fixed 模式：所有申请推送给配置中的固定审核人。上传入口只依赖
resolve_reviewer_staff_ids()，以后切换为“按部门找主管”时无需重写下载和审批流程。
"""

import logging
import re
import threading
from pathlib import Path
from typing import Callable, Optional

from config import KNOWLEDGE_REVIEW_MODE, KNOWLEDGE_REVIEWER_STAFF_IDS
from dingtalk_notifier import DingTalkNotifier
from doc_mgr.sync_tracker import SyncTracker

logger = logging.getLogger("knowledge_review")

_UPLOAD_ROOT = Path(__file__).parent.parent / "data" / "uploads"
_SUPPORTED_EXTENSIONS = {".pdf", ".xlsx", ".xls", ".md"}
_ALLOWED_COLLECTIONS = {"standards", "error_codes"}
_COMMAND_RE = re.compile(
    r"^(同意同步|拒绝同步)\s+([A-Za-z0-9]{6,16})(?:\s+(.+?))?\s*$"
)


def _split_ids(raw_ids: str) -> list[str]:
    return list(dict.fromkeys(
        item.strip() for item in re.split(r"[,，;；\s]+", raw_ids or "")
        if item.strip()
    ))


def resolve_reviewer_staff_ids(_uploader_user_id: str = "",
                               mode: str = KNOWLEDGE_REVIEW_MODE,
                               fixed_ids: str = KNOWLEDGE_REVIEWER_STAFF_IDS
                               ) -> list[str]:
    """解析审核人。当前只开放 fixed，保留部门主管模式的替换点。"""
    if mode == "fixed":
        return _split_ids(fixed_ids)
    logger.error(f"未知知识审核模式，已停止主动推送: {mode}")
    return []


def default_collection(file_name: str) -> str:
    """给审核人提供默认目标库；仍可在批准口令中显式覆盖。"""
    ext = Path(file_name).suffix.lower()
    return "error_codes" if ext in {".xlsx", ".xls"} else "standards"


def _collection_from_text(value: str, file_name: str) -> Optional[str]:
    if not value:
        return default_collection(file_name)
    aliases = {
        "standards": "standards",
        "标准": "standards",
        "标准库": "standards",
        "标准文档": "standards",
        "error_codes": "error_codes",
        "故障": "error_codes",
        "故障库": "error_codes",
        "故障代码": "error_codes",
    }
    return aliases.get(value.strip().lower())


def _format_size(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / 1024 / 1024:.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} B"


class KnowledgeReviewService:
    """负责创建申请、主动通知、鉴权并启动已批准的同步任务。"""

    def __init__(self, tracker: Optional[SyncTracker] = None,
                 notifier: Optional[DingTalkNotifier] = None,
                 reviewer_ids: Optional[list[str]] = None,
                 submitter: Optional[Callable[[dict, str], str]] = None,
                 upload_root: Path = _UPLOAD_ROOT):
        self.tracker = tracker or SyncTracker()
        self.notifier = notifier or DingTalkNotifier()
        self.reviewer_ids = reviewer_ids
        self.submitter = submitter or self._submit_sync_task
        self.upload_root = Path(upload_root)

    def _reviewers_for(self, uploader_user_id: str) -> list[str]:
        if self.reviewer_ids is not None:
            return list(self.reviewer_ids)
        return resolve_reviewer_staff_ids(uploader_user_id)

    def create_request(self, file_path: str, file_name: str, file_size: int,
                       uploader_user_id: str, uploader_name: str) -> Optional[dict]:
        """登记审核申请；未配置审核人或文件不支持时返回 None。"""
        if Path(file_name).suffix.lower() not in _SUPPORTED_EXTENSIONS:
            return None
        reviewers = self._reviewers_for(uploader_user_id)
        if not reviewers:
            logger.warning(
                "未配置 KNOWLEDGE_REVIEWER_STAFF_IDS，文件保留在待处理区"
            )
            return None

        target = default_collection(file_name)
        self.tracker.update_collection(file_path, target)
        review_id = self.tracker.create_review(
            file_path, ",".join(reviewers)
        )
        return {
            "review_id": review_id,
            "reviewers": reviewers,
            "file_path": file_path,
            "file_name": file_name,
            "file_size": file_size,
            "uploader_user_id": uploader_user_id,
            "uploader_name": uploader_name or "未知用户",
            "target_collection": target,
        }

    @staticmethod
    def build_notification(request: dict) -> str:
        target_label = (
            "故障代码库" if request["target_collection"] == "error_codes"
            else "标准文档库"
        )
        review_id = request["review_id"]
        return (
            "### 知识库同步申请\n\n"
            f"- 申请编号：`{review_id}`\n"
            f"- 上传人：{request['uploader_name']}\n"
            f"- 文件名：{request['file_name']}\n"
            f"- 文件大小：{_format_size(int(request['file_size']))}\n"
            f"- 建议入库：{target_label}\n\n"
            f"同意请回复：`同意同步 {review_id}`\n\n"
            f"拒绝请回复：`拒绝同步 {review_id} 原因`\n\n"
            "如需改入另一类库，可在同意口令末尾加 `标准库` 或 `故障库`。"
        )

    def notify(self, request: dict) -> dict:
        """同步发送主动通知，主要供后台线程和测试调用。"""
        return self.notifier.send_markdown_to_users(
            request["reviewers"],
            title="恩特小助手 - 知识库同步审核",
            text=self.build_notification(request),
        )

    def notify_async(self, request: dict) -> None:
        """后台发送，钉钉 API 波动不会阻塞上传者的文件回执。"""
        def _send():
            try:
                self.notify(request)
                logger.info(f"审核通知已发送: {request['review_id']}")
            except Exception as exc:
                logger.error(
                    f"审核通知发送失败 {request['review_id']}: {exc}"
                )

        threading.Thread(
            target=_send,
            daemon=True,
            name=f"review-notify-{request['review_id']}",
        ).start()

    def handle_command(self, text: str,
                       sender_staff_id: str) -> Optional[str]:
        """处理批准/拒绝口令；非审核口令返回 None 交给普通技能路由。"""
        match = _COMMAND_RE.match(text.strip())
        if not match:
            return None

        action, review_id, extra = match.groups()
        review_id = review_id.upper()
        row = self.tracker.get_review(review_id)
        if not row:
            return f"未找到审核申请 {review_id}，请检查编号。"

        allowed = _split_ids(row.get("reviewer_staff_id", ""))
        current_reviewers = self._reviewers_for(row.get("upload_user_id", ""))
        if (not sender_staff_id or sender_staff_id not in allowed
                or sender_staff_id not in current_reviewers):
            return "你不是该申请的指定审核人，无法执行此操作。"

        status = row.get("review_status", "")
        if status != "pending":
            labels = {"approved": "已批准", "rejected": "已拒绝"}
            return f"申请 {review_id} {labels.get(status, '已处理')}，不能重复操作。"

        if action == "拒绝同步":
            if not self.tracker.decide_review(
                    review_id, "rejected", sender_staff_id):
                return f"申请 {review_id} 已被其他操作处理，请刷新后再看。"
            reason = (extra or "未填写原因").strip()
            logger.info(f"审核拒绝: {review_id}, reason={reason[:100]}")
            return f"已拒绝申请 {review_id}。文件仍保留在待处理区，没有进入知识库。"

        collection = _collection_from_text(extra or "", row["file_name"])
        if collection not in _ALLOWED_COLLECTIONS:
            return "目标库不正确，请使用“标准库”或“故障库”。"
        if not self._is_safe_upload_file(row["file_path"]):
            logger.error(f"审核文件路径越界或不存在: {row['file_path']}")
            return "该申请对应的文件不存在或路径不安全，已停止同步。"

        if not self.tracker.decide_review(
                review_id, "approved", sender_staff_id):
            return f"申请 {review_id} 已被其他操作处理，请刷新后再看。"

        try:
            task_id = self.submitter(row, collection)
        except Exception as exc:
            self.tracker.reset_review_pending(review_id)
            logger.exception(f"审核任务提交失败: {review_id}")
            return f"同步任务启动失败：{str(exc)[:100]}。申请已恢复为待审核，可稍后重试。"

        # 任务已启动后不能再恢复 pending；即使任务编号落库失败也只记录日志，
        # 避免审核人重试导致同一文件被提交两次。
        try:
            self.tracker.set_review_task(review_id, task_id)
        except Exception as exc:
            logger.error(f"记录审核任务编号失败 {review_id}: {exc}")

        target_label = "故障代码库" if collection == "error_codes" else "标准文档库"
        return (
            f"已批准申请 {review_id}，正在后台同步到{target_label}。\n\n"
            f"任务编号：{task_id}"
        )

    def _is_safe_upload_file(self, file_path: str) -> bool:
        try:
            root = self.upload_root.resolve(strict=True)
            candidate = Path(file_path).resolve(strict=True)
            return (candidate.is_file() and candidate.is_relative_to(root)
                    and candidate.suffix.lower() in _SUPPORTED_EXTENSIONS)
        except (OSError, RuntimeError):
            return False

    def _submit_sync_task(self, row: dict, collection: str) -> str:
        """复用现有文档引擎和后台任务管理器启动同步。"""
        from doc_mgr.engine import process_file
        from doc_mgr.task_manager import get_manager

        file_path = row["file_path"]
        file_name = row["file_name"]
        self.tracker.update_collection(file_path, collection)
        manager = get_manager()
        record = manager.submit(
            file_name=file_name,
            collection=collection,
            process_fn=lambda: process_file(
                file_path,
                file_name=file_name,
                target_collection=collection,
                force=False,
            ),
        )
        return record.task_id


_service: Optional[KnowledgeReviewService] = None


def get_review_service() -> KnowledgeReviewService:
    global _service
    if _service is None:
        _service = KnowledgeReviewService()
    return _service


def queue_review_for_upload(result: dict, uploader_user_id: str,
                            uploader_name: str) -> Optional[dict]:
    """钉钉文件保存成功后的统一入口。"""
    if not result.get("success") or not result.get("file_path"):
        return None
    service = get_review_service()
    request = service.create_request(
        file_path=result["file_path"],
        file_name=result["file_name"],
        file_size=int(result.get("file_size", 0)),
        uploader_user_id=uploader_user_id,
        uploader_name=uploader_name,
    )
    if request:
        service.notify_async(request)
    return request


def handle_review_message(text: str, sender_staff_id: str) -> Optional[str]:
    return get_review_service().handle_command(text, sender_staff_id)
