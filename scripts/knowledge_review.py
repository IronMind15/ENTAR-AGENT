"""钉钉上传文件的知识库审核工作流。

当前采用 fixed 模式：所有申请推送给配置中的固定审核人。上传入口只依赖
resolve_reviewer_staff_ids()，以后切换为“按部门找主管”时无需重写下载和审批流程。
"""

import logging
import os
import re
import threading
from pathlib import Path
from typing import Callable, Optional

from config import KNOWLEDGE_REVIEW_MODE, KNOWLEDGE_REVIEWER_STAFF_IDS
from center_config import resolve_center, get_center_name
from dingtalk_notifier import DingTalkNotifier
from doc_mgr.sync_tracker import SyncTracker

logger = logging.getLogger("knowledge_review")

_UPLOAD_ROOT = Path(__file__).parent.parent / "data" / "uploads"
_SUPPORTED_EXTENSIONS = {".pdf", ".xlsx", ".xls", ".md", ".docx", ".pptx", ".csv"}
_ALLOWED_COLLECTIONS = {"standards", "error_codes", "experience_kb"}
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
    if ext in {".xlsx", ".xls"}:
        return "error_codes"
    if ext == ".md":
        return "experience_kb"
    return "standards"


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
        "experience_kb": "experience_kb",
        "经验": "experience_kb",
        "经验库": "experience_kb",
        "经验知识": "experience_kb",
        "经验知识库": "experience_kb",
    }
    return aliases.get(value.strip().lower())


def _parse_extra_tokens(extra: str, file_name: str
                        ) -> tuple[str, str]:
    """从审核命令的额外文本中解析部门和目标库

    支持灵活顺序：
      "研发中心"          → (rd, 默认库)
      "研发中心 标准库"    → (rd, standards)
      "标准库 研发中心"    → (rd, standards)
      "标准库"            → (public, standards)
      ""                  → (public, 默认库)

    Returns:
        (department_id, collection_name)
    """
    if not extra:
        return "public", default_collection(file_name)

    tokens = extra.strip().split()
    department = "public"
    collection = default_collection(file_name)

    for token in tokens:
        # 尝试解析为部门
        dept = resolve_center(token)
        if dept:
            department = dept
            continue
        # 尝试解析为目标库
        coll = _collection_from_text(token, file_name)
        if coll:
            collection = coll
            continue

    return department, collection


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
                       uploader_user_id: str, uploader_name: str,
                       suggested_department: str = "public",
                       uploader_centers: list[str] | None = None) -> Optional[dict]:
        """登记审核申请；未配置审核人或文件不支持时返回 None。

        Args:
            suggested_department: 建议所属中心 ID
        """
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
        # 把建议部门真正落库（create_review 只登记申请），审批通过后
        # _submit_sync_task 才能从记录读取真实部门而非默认 public。
        try:
            self.tracker.update_department(file_path, suggested_department)
        except Exception as exc:
            logger.warning(f"写入建议部门失败（不影响申请创建）: {exc}")
        return {
            "review_id": review_id,
            "reviewers": reviewers,
            "file_path": file_path,
            "file_name": file_name,
            "file_size": file_size,
            "uploader_user_id": uploader_user_id,
            "uploader_name": uploader_name or "未知用户",
            "target_collection": target,
            "suggested_department": suggested_department,
            "uploader_centers": uploader_centers or [],
        }

    @staticmethod
    def build_notification(request: dict) -> str:
        target_label = {
            "error_codes": "故障代码库",
            "experience_kb": "经验知识库",
        }.get(request["target_collection"], "标准文档库")
        dept_id = request.get("suggested_department", "public")
        dept_label = get_center_name(dept_id)
        review_id = request["review_id"]

        # 上传人归属中心展示
        centers = request.get("uploader_centers", [])
        centers_display = _format_centers_display(centers)
        uploader_info = request['uploader_name']
        if centers_display and centers_display != "全公司公开":
            uploader_info += f"（{centers_display}）"

        return (
            "### 知识库同步申请\n\n"
            f"- 申请编号：`{review_id}`\n"
            f"- 上传人：{uploader_info}\n"
            f"- 文件名：{request['file_name']}\n"
            f"- 文件大小：{_format_size(int(request['file_size']))}\n"
            f"- 建议入库：{target_label}\n"
            f"- **建议部门：{dept_label}**\n\n"
            f"✅ 同意入库（{dept_label}）：`同意同步 {review_id}`\n"
            f"🔄 改部门或库：`同意同步 {review_id} 新部门` 或 `同意同步 {review_id} 新部门 新库`\n"
            f"❌ 拒绝：`拒绝同步 {review_id} 原因`\n\n"
            "部门可选：公共 / PMO / 研发 / 制造 / 商业 / 运营\n"
            "库可选：标准库 / 故障库 / 经验库"
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

        # 从额外文本解析部门和目标库
        department, collection = _parse_extra_tokens(
            extra or "", row["file_name"]
        )
        if collection not in _ALLOWED_COLLECTIONS:
            return "目标库不正确，请使用“标准库”“故障库”或“经验库”。"
        if not self._is_safe_upload_file(row["file_path"]):
            logger.error(f"审核文件路径越界或不存在: {row['file_path']}")
            return "该申请对应的文件不存在或路径不安全，已停止同步。"

        if not self.tracker.decide_review(
                review_id, "approved", sender_staff_id):
            return f"申请 {review_id} 已被其他操作处理，请刷新后再看。"

        try:
            task_id = self.submitter(row, collection, department=department)
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

        target_label = {
            "error_codes": "故障代码库",
            "experience_kb": "经验知识库",
        }.get(collection, "标准文档库")
        return (
            f"已批准申请 {review_id}，正在后台同步到{target_label}。\n\n"
            f"任务编号：{task_id}"
        )

    # ===== v1.10.2 直接学习 / 我的文件 / 删除 能力 =====
    # 说明：审核流程（create_request/handle_command/notify_*）已停用但代码保留，
    # 未来恢复部门划分与主管审核时，在 dingtalk_bot 恢复 queue_review_for_upload
    # 调用，并把 process_file 的 department 从 "public" 改为用户主部门即可。

    def list_files_for_user(self, user_id: str, is_admin: bool = False) -> list[dict]:
        """列出文件：普通用户只看自己上传的；管理员看全库。"""
        if is_admin:
            return self.tracker.list_all()
        return self.tracker.get_files_by_user(user_id)

    def _locate_file(self, user_id: str, target: str,
                     is_admin: bool = False) -> dict:
        """按序号（1-based）或文件名模糊匹配定位文件记录。

        普通用户只在自己上传的文件中定位；管理员在全库中定位。
        返回 {"status":"ok","row":...}，否则返回错误原因。
        """
        rows = (self.tracker.list_all() if is_admin
                else self.tracker.get_files_by_user(user_id))
        if not rows:
            return {"status": "no_file", "message": "当前没有文件记录。"}
        target = (target or "").strip()
        if target.isdigit():
            idx = int(target) - 1
            if 0 <= idx < len(rows):
                return {"status": "ok", "row": rows[idx]}
            return {"status": "not_found",
                    "message": f"序号超出范围（共 {len(rows)} 个）。"}
        # 文件名模糊匹配；提示用全列表序号，避免用户再输入序号错位
        matches = [(i, r) for i, r in enumerate(rows)
                   if target in (r.get("file_name") or "")]
        if not matches:
            return {"status": "not_found",
                    "message": f"未找到文件名包含「{target}」的文件。"}
        if len(matches) > 1:
            names = "\n".join(f"{i + 1}. {r['file_name']}" for i, r in matches)
            return {"status": "ambiguous",
                    "message": f"匹配到多个文件，请用序号指定：\n{names}"}
        return {"status": "ok", "row": matches[0][1]}

    def learn_for_user(self, user_id: str, user_name: str = "",
                       kb: Optional[dict] = None) -> dict:
        """用户上传文件后回复「帮我学习」→ 直接同步入库（无审核）。

        取该用户最新待学习文件（pending/error），同步调 process_file，
        复用 engine 内部 _record_sync_status 自动 mark_synced/mark_error。
        kb: 可选，指定知识库（v1.11.5 多库：resolve_kb 结果 dict），
            其 collection/department 覆盖默认库与 'public'。
        """
        from doc_mgr.engine import process_file

        rows = [r for r in self.tracker.get_pending_files()
                if r.get("upload_user_id") == user_id]
        if not rows:
            return {"status": "no_file",
                    "message": "当前没有待学习的文件，请先发送文件再回复「帮我学习」。"}
        row = rows[0]  # get_pending_files 已按 updated_at DESC
        file_path, file_name = row["file_path"], row["file_name"]

        if not self._is_safe_upload_file(file_path):
            return {"status": "failed", "file_name": file_name,
                    "message": "文件不存在或路径不安全，已停止学习。请重新发送文件。"}

        # 必须显式传 collection：engine 内部 .md 默认走 standards，
        # 与 default_collection 的 .md→experience_kb 不一致，按登记库入库。
        if kb:
            collection = kb.get("collection") or kb.get("key")
            department = kb.get("department") or "public"
        else:
            collection = row.get("target_collection") or default_collection(file_name)
            department = "public"  # v1.10.2 暂不划分部门；未来改传用户主部门
        try:
            doc = process_file(
                file_path,
                file_name=file_name,
                target_collection=collection,
                force=False,
                department=department,
            )
        except Exception as exc:
            logger.exception(f"用户直接学习失败: {file_path}")
            try:
                self.tracker.mark_error(file_path, str(exc)[:500])
            except Exception:
                pass
            return {"status": "failed", "file_name": file_name,
                    "message": str(exc)[:200]}

        if doc.status == "done":
            return {"status": "ok", "file_name": file_name,
                    "collection": collection,
                    "chunk_count": int(getattr(doc, "chunk_count", 0) or 0),
                    "message": ""}
        return {"status": "failed", "file_name": file_name,
                "message": getattr(doc, "message", "") or "处理失败"}

    def learn_file_path(self, user_id: str, file_path: str,
                        file_name: str = "",
                        kb: Optional[dict] = None) -> dict:
        """按指定文件路径直接入库（v1.11.0 上传后「自动推荐→确认」用）。

        与 learn_for_user 同构：校验路径安全 → 取 target_collection →
        同步 process_file → 返回同构结果。engine.process_file 行为不变。
        kb: 可选，指定知识库（v1.11.5），覆盖 tracker 登记库与默认库。
        """
        from doc_mgr.engine import process_file

        if not file_path:
            return {"status": "no_file", "message": "未找到待学习的文件。"}
        fname = file_name or os.path.basename(file_path)
        if not self._is_safe_upload_file(file_path):
            return {"status": "failed", "file_name": fname,
                    "message": "文件不存在或路径不安全，请重新发送文件。"}

        # 指定知识库优先；否则用 tracker 已登记的目标库；无则按扩展名默认
        if kb:
            collection = kb.get("collection") or kb.get("key")
            department = kb.get("department") or "public"
        else:
            collection = "standards"
            try:
                st = self.tracker.get_status(file_path)
                if st and st.get("target_collection"):
                    collection = st["target_collection"]
                else:
                    collection = default_collection(fname)
            except Exception:
                collection = default_collection(fname)
            department = "public"

        try:
            doc = process_file(
                file_path,
                file_name=fname,
                target_collection=collection,
                force=False,
                department=department,
            )
        except Exception as exc:
            logger.exception(f"按路径学习失败: {file_path}")
            try:
                self.tracker.mark_error(file_path, str(exc)[:500])
            except Exception:
                pass
            return {"status": "failed", "file_name": fname,
                    "message": str(exc)[:200]}

        if doc.status == "done":
            return {"status": "ok", "file_name": fname, "collection": collection,
                    "chunk_count": int(getattr(doc, "chunk_count", 0) or 0),
                    "message": ""}
        return {"status": "failed", "file_name": fname,
                "message": getattr(doc, "message", "") or "处理失败"}

    def relearn_for_user(self, user_id: str, target: str,
                         is_admin: bool = False,
                         kb: Optional[dict] = None) -> dict:
        """对已上传文件强制重新学习（force=True 重新解析入库）。

        kb: 可选，指定知识库（v1.11.5），覆盖登记库。
        """
        from doc_mgr.engine import process_file

        located = self._locate_file(user_id, target, is_admin)
        if located["status"] != "ok":
            return located
        row = located["row"]
        if not is_admin and row.get("upload_user_id", "") != user_id:
            return {"status": "denied",
                    "message": "你无权重新学习其他用户上传的文件。"}
        file_path, file_name = row["file_path"], row["file_name"]
        if not self._is_safe_upload_file(file_path):
            return {"status": "failed", "file_name": file_name,
                    "message": "文件不存在或路径不安全，请重新发送文件。"}
        if kb:
            collection = kb.get("collection") or kb.get("key")
            department = kb.get("department") or "public"
        else:
            collection = row.get("target_collection") or default_collection(file_name)
            department = "public"
        try:
            doc = process_file(
                file_path,
                file_name=file_name,
                target_collection=collection,
                force=True,
                department=department,
            )
        except Exception as exc:
            logger.exception(f"强制重学失败: {file_path}")
            try:
                self.tracker.mark_error(file_path, str(exc)[:500])
            except Exception:
                pass
            return {"status": "failed", "file_name": file_name,
                    "message": str(exc)[:200]}
        if doc.status == "done":
            return {"status": "ok", "file_name": file_name,
                    "collection": collection,
                    "chunk_count": int(getattr(doc, "chunk_count", 0) or 0),
                    "message": ""}
        return {"status": "failed", "file_name": file_name,
                "message": getattr(doc, "message", "") or "处理失败"}

    def delete_for_user(self, user_id: str, target: str,
                        is_admin: bool = False) -> dict:
        """删除用户自己上传的文件：学习内容（向量）+ 源文件 + tracker 记录。

        权限硬校验：非管理员只能删 upload_user_id == user_id 的记录。
        """
        from doc_mgr.engine import get_store
        from doc_mgr.identity import stable_document_id

        located = self._locate_file(user_id, target, is_admin)
        if located["status"] != "ok":
            return located
        row = located["row"]

        # 权限硬校验：普通用户严禁删除其他用户上传的任何数据
        if not is_admin and row.get("upload_user_id", "") != user_id:
            return {"status": "denied",
                    "message": "你无权删除其他用户上传的文件。"}

        file_path, file_name = row["file_path"], row["file_name"]
        collection = row.get("target_collection") or default_collection(file_name)
        deleted_chunks = 0

        # 1. 删除知识库学习内容（按 doc_id 精确删，不误伤同名文件）
        try:
            doc_id = stable_document_id(file_path)
            store = get_store()
            # include_hidden=True：必须覆盖 staging/retired 全部版本，否则 retired
            # 残留会被崩溃恢复（recovery 2b）误恢复为 active，造成删除复活（审查 Critical 4）
            before = store.get(collection, where={"doc_id": doc_id},
                               include_hidden=True) or {}
            before_ids = before.get("ids", []) or []
            if before_ids:
                store.delete(collection, ids=before_ids)
                deleted_chunks = len(before_ids)
        except Exception as exc:
            logger.warning(f"删除知识库内容失败（继续删源文件）: {exc}")

        # 2. 删除源文件（白名单校验：必须在上传目录内）
        source_deleted = False
        try:
            if self._is_safe_upload_file(file_path):
                os.remove(file_path)
                source_deleted = True
        except OSError as exc:
            logger.warning(f"删除源文件失败（保留 tracker 记录）: {exc}")

        # 3. 删除 tracker 记录
        try:
            self.tracker.delete_file(file_path)
        except Exception as exc:
            logger.warning(f"删除追踪记录失败: {exc}")

        return {"status": "ok", "file_name": file_name,
                "deleted_chunks": deleted_chunks,
                "source_deleted": source_deleted,
                "message": ""}

    def _is_safe_upload_file(self, file_path: str) -> bool:
        try:
            root = self.upload_root.resolve(strict=True)
            candidate = Path(file_path).resolve(strict=True)
            return (candidate.is_file() and candidate.is_relative_to(root)
                    and candidate.suffix.lower() in _SUPPORTED_EXTENSIONS)
        except (OSError, RuntimeError):
            return False

    def _submit_sync_task(self, row: dict, collection: str,
                          department: str = "public") -> str:
        """复用现有文档引擎和后台任务管理器启动同步。

        Args:
            department: 所属中心 ID，优先用参数值，无则从 row 中读取
        """
        from doc_mgr.engine import process_file
        from doc_mgr.task_manager import get_manager

        if not department or department == "public":
            department = row.get("suggested_department", "public")

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
                department=department,
            ),
        )
        return record.task_id


_service: Optional[KnowledgeReviewService] = None
_service_lock = threading.Lock()


def get_review_service() -> KnowledgeReviewService:
    """获取全局审核服务实例（单例，线程安全双检锁）

    v1.6.0 起消息处理放线程池，首次调用可能并发创建
    SyncTracker + DingTalkNotifier，加锁避免重复实例化（v1.6.1）。
    """
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = KnowledgeReviewService()
    return _service


def _get_user_centers(user_id: str) -> list[str]:
    """从 user_store 查询用户的归属中心列表

    优先读取 centers 字段（JSON 数组），回退到单 center 字段，
    再回退到 public。
    """
    try:
        from user_store import get_store
        store = get_store()
        centers = store.get_user_centers(user_id)
        if centers:
            return centers
        # 回退：检查单 center 字段
        user = store.get_user(user_id)
        if user and user.get("center") and user["center"] != "public":
            return [user["center"]]
    except Exception as e:
        logger.debug(f"查询用户中心失败: {e}")
    return ["public"]


def _format_centers_display(centers: list[str]) -> str:
    """格式化中心列表为展示文本"""
    names = [get_center_name(c) for c in centers if c and c != "public"]
    if names:
        return "、".join(names)
    return "全公司公开"


def queue_review_for_upload(result: dict, uploader_user_id: str,
                            uploader_name: str) -> Optional[dict]:
    """钉钉文件保存成功后的统一入口。"""
    if not result.get("success") or not result.get("file_path"):
        return None
    service = get_review_service()
    user_centers = _get_user_centers(uploader_user_id)
    suggested_dept = user_centers[0] if user_centers else "public"
    request = service.create_request(
        file_path=result["file_path"],
        file_name=result["file_name"],
        file_size=int(result.get("file_size", 0)),
        uploader_user_id=uploader_user_id,
        uploader_name=uploader_name,
        suggested_department=suggested_dept,
        uploader_centers=user_centers,
    )
    if request:
        service.notify_async(request)
    return request


def handle_review_message(text: str, sender_staff_id: str) -> Optional[str]:
    return get_review_service().handle_command(text, sender_staff_id)


# ===== v1.10.2 直接学习 / 我的文件 / 删除 模块级入口（供 dingtalk_bot 调用） =====

def learn_file_for_user(user_id: str, user_name: str = "",
                        kb: Optional[dict] = None) -> dict:
    """用户回复「帮我学习」→ 直接入库最新待学习文件。

    kb: 可选，指定知识库（v1.11.5 多库），覆盖默认库。
    """
    return get_review_service().learn_for_user(user_id, user_name, kb=kb)


# ===== v1.11.0 上传后「自动推荐入库」确认 =====
# v1.12.1（M3）：pending 统一收口到 pending_context（type=learn），薄封装保留 API。
def set_pending_learn(user_id: str, file_path: str, file_name: str = ""):
    """保存文件成功后登记推荐确认（精确对应刚上传的文件，规避多文件歧义）"""
    from pending_context import PT_LEARN, set as pc_set
    pc_set(user_id, PT_LEARN, {"file_path": file_path, "file_name": file_name})


def get_pending_learn(user_id: str) -> Optional[dict]:
    from pending_context import PT_LEARN, get as pc_get
    entry = pc_get(user_id)
    if entry and entry["type"] == PT_LEARN:
        return dict(entry["payload"])
    return None


def clear_pending_learn(user_id: str):
    from pending_context import PT_LEARN, clear_type
    clear_type(user_id, PT_LEARN)


def learn_file_path_for_user(user_id: str, file_path: str,
                             file_name: str = "",
                             kb: Optional[dict] = None) -> dict:
    """按指定路径入库（bot 确认分支调用）

    kb: 可选，指定知识库（v1.11.5 多库），覆盖登记库。
    """
    return get_review_service().learn_file_path(user_id, file_path, file_name, kb=kb)


def list_files_for_user(user_id: str, is_admin: bool = False) -> list[dict]:
    """列出文件：普通用户只看自己的，管理员看全库。"""
    return get_review_service().list_files_for_user(user_id, is_admin)


def delete_file_for_user(user_id: str, target: str,
                         is_admin: bool = False) -> dict:
    """删除指定文件：学习内容 + 源文件 + tracker 记录（限本人/管理员）。"""
    return get_review_service().delete_for_user(user_id, target, is_admin)


def relearn_file_for_user(user_id: str, target: str,
                          is_admin: bool = False) -> dict:
    """对指定文件强制重新学习（限本人/管理员）。"""
    return get_review_service().relearn_for_user(user_id, target, is_admin)
