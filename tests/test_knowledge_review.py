import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from dingtalk_notifier import DingTalkNotifier
from doc_mgr.identity import file_sha256
from doc_mgr.sync_tracker import SyncTracker
from file_handler import sanitize_file_name
from knowledge_review import (
    KnowledgeReviewService, default_collection, _collection_from_text,
    clear_pending_learn, get_pending_learn, learn_file_path_for_user,
    set_pending_learn,
)


class _FakeStore:
    """模拟 doc_mgr 存储层：只实现 delete_for_user 用到的 get/delete"""

    def __init__(self):
        self.gets = []
        self.deletes = []

    def get(self, collection, ids=None, where=None, include_hidden=False):
        self.gets.append((collection, ids, where, include_hidden))
        return {"ids": ["blk-1", "blk-2"],
                "metadatas": [{"doc_id": "d"}, {"doc_id": "d"}]}

    def delete(self, collection, ids=None, where=None):
        self.deletes.append((collection, ids, where))
        return len(ids or [])


class _FakeResponse:
    def __init__(self, payload, content=b"{}"):
        self._payload = payload
        self.content = content

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self):
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if url.endswith("/oauth2/accessToken"):
            return _FakeResponse({"accessToken": "token-1", "expireIn": 7200})
        return _FakeResponse({"processQueryKey": "query-1"})


class DingTalkNotifierTests(unittest.TestCase):
    def test_active_markdown_message_uses_staff_ids_and_cached_token(self):
        session = _FakeSession()
        notifier = DingTalkNotifier(
            client_id="robot-code",
            client_secret="secret",
            api_base="https://api.dingtalk.test",
            session=session,
        )

        notifier.send_markdown_to_users(["boss", "boss"], "标题", "内容")
        notifier.send_markdown_to_users(["boss"], "标题2", "内容2")

        self.assertEqual(3, len(session.calls))
        send_call = session.calls[1]
        self.assertTrue(send_call[0].endswith("/robot/oToMessages/batchSend"))
        self.assertEqual(["boss"], send_call[1]["json"]["userIds"])
        self.assertEqual("robot-code", send_call[1]["json"]["robotCode"])
        self.assertEqual(
            "token-1",
            send_call[1]["headers"]["x-acs-dingtalk-access-token"],
        )


class FileNameSafetyTests(unittest.TestCase):
    def test_upload_name_cannot_escape_user_directory(self):
        self.assertEqual("secret.pdf", sanitize_file_name("../../secret.pdf"))
        self.assertEqual("secret.pdf", sanitize_file_name(r"..\..\secret.pdf"))

    def test_windows_reserved_name_is_prefixed(self):
        self.assertEqual("_CON.txt", sanitize_file_name("CON.txt"))


class KnowledgeReviewServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "uploads"
        self.root.mkdir()
        self.file_path = self.root / "sample.pdf"
        self.file_path.write_bytes(b"pdf-test")
        self.tracker = SyncTracker(str(Path(self.temp_dir.name) / "test.db"))
        self.tracker.upsert_file(
            str(self.file_path),
            self.file_path.name,
            self.file_path.stat().st_size,
            file_sha256(str(self.file_path)),
            upload_user_id="uploader-union-id",
            upload_user_name="测试员工",
        )
        self.submissions = []

        def submitter(row, collection, **kwargs):
            self.submissions.append((row["review_id"], collection))
            return "task-001"

        self.service = KnowledgeReviewService(
            tracker=self.tracker,
            reviewer_ids=["boss-staff-id"],
            submitter=submitter,
            upload_root=self.root,
        )

    def tearDown(self):
        self.tracker.close()
        self.temp_dir.cleanup()

    def _create_request(self):
        return self.service.create_request(
            file_path=str(self.file_path),
            file_name=self.file_path.name,
            file_size=self.file_path.stat().st_size,
            uploader_user_id="uploader-union-id",
            uploader_name="测试员工",
        )

    def test_request_is_pending_and_contains_no_server_path(self):
        request = self._create_request()
        self.assertIsNotNone(request)
        row = self.tracker.get_review(request["review_id"])
        self.assertEqual("pending", row["review_status"])
        self.assertEqual("standards", row["target_collection"])

        message = self.service.build_notification(request)
        self.assertIn("测试员工", message)
        self.assertIn(request["review_id"], message)
        self.assertNotIn(str(self.root), message)

    def test_only_configured_reviewer_can_approve_once(self):
        request = self._create_request()
        command = f"同意同步 {request['review_id']}"

        denied = self.service.handle_command(command, "other-staff-id")
        self.assertIn("不是该申请的指定审核人", denied)
        self.assertEqual([], self.submissions)

        approved = self.service.handle_command(command, "boss-staff-id")
        self.assertIn("task-001", approved)
        self.assertEqual([(request["review_id"], "standards")], self.submissions)

        repeated = self.service.handle_command(command, "boss-staff-id")
        self.assertIn("不能重复操作", repeated)
        self.assertEqual(1, len(self.submissions))

    def test_reject_does_not_submit_sync_task(self):
        request = self._create_request()
        answer = self.service.handle_command(
            f"拒绝同步 {request['review_id']} 内容不适合入库",
            "boss-staff-id",
        )
        self.assertIn("已拒绝", answer)
        self.assertEqual([], self.submissions)
        row = self.tracker.get_review(request["review_id"])
        self.assertEqual("rejected", row["review_status"])

    def test_approval_rejects_file_outside_upload_root(self):
        request = self._create_request()
        other_root = Path(self.temp_dir.name) / "other"
        other_root.mkdir()
        self.service.upload_root = other_root

        answer = self.service.handle_command(
            f"同意同步 {request['review_id']}", "boss-staff-id"
        )
        self.assertIn("路径不安全", answer)
        self.assertEqual([], self.submissions)

    def test_submit_failure_restores_pending_for_retry(self):
        request = self._create_request()

        def fail_submit(_row, _collection, **kwargs):
            raise RuntimeError("queue unavailable")

        self.service.submitter = fail_submit
        answer = self.service.handle_command(
            f"同意同步 {request['review_id']}", "boss-staff-id"
        )
        self.assertIn("已恢复为待审核", answer)
        row = self.tracker.get_review(request["review_id"])
        self.assertEqual("pending", row["review_status"])

    def test_experience_md_default_collection(self):
        """经验 .md 文件默认落经验知识库（experience_kb），通知含库名"""
        md_path = self.root / "经验排查.md"
        md_path.write_text("# 经验条目：测试\n\n## 故障现象\n内容", encoding="utf-8")
        self.tracker.upsert_file(
            str(md_path), md_path.name, md_path.stat().st_size,
            file_sha256(str(md_path)),
            upload_user_id="uploader-union-id",
            upload_user_name="测试员工",
        )
        request = self.service.create_request(
            file_path=str(md_path), file_name=md_path.name,
            file_size=md_path.stat().st_size,
            uploader_user_id="uploader-union-id",
            uploader_name="测试员工",
        )
        self.assertEqual("experience_kb", request["target_collection"])
        message = self.service.build_notification(request)
        self.assertIn("经验知识库", message)

    # ===== v1.10.2 直接学习 / 我的文件 / 删除（审核流程已停用但代码保留） =====

    def _upsert_for(self, name: str, content: bytes = b"x") -> Path:
        p = self.root / name
        p.write_bytes(content)
        self.tracker.upsert_file(
            str(p), p.name, p.stat().st_size, file_sha256(str(p)),
            upload_user_id="uploader-union-id", upload_user_name="测试员工",
        )
        return p

    def test_learn_for_user_syncs_latest_pending_file(self):
        """「帮我学习」取最新待学习文件，同步 process_file（department=public）"""
        with patch("doc_mgr.engine.process_file") as process:
            process.return_value = SimpleNamespace(
                status="done", chunk_count=5, collection="standards", message="")
            result = self.service.learn_for_user("uploader-union-id", "测试员工")
        self.assertEqual("ok", result["status"])
        self.assertEqual("sample.pdf", result["file_name"])
        self.assertEqual("standards", result["collection"])
        self.assertEqual(5, result["chunk_count"])
        process.assert_called_once()
        self.assertEqual("public", process.call_args.kwargs["department"])
        self.assertEqual(False, process.call_args.kwargs["force"])

    def test_learn_for_user_invalidates_bm25_cache(self):
        """v1.12.x（审查 Critical 3）：学习成功后清 BM25 缓存（防混合检索旧索引）"""
        with patch("doc_mgr.engine.process_file") as process, \
             patch("skills.enhanced_search.invalidate_bm25_cache") as invalidate:
            process.return_value = SimpleNamespace(
                status="done", chunk_count=5, collection="standards", message="")
            result = self.service.learn_for_user("uploader-union-id", "测试员工")
        self.assertEqual(result["status"], "ok")
        invalidate.assert_called_once_with("standards")

    def test_learn_md_uses_explicit_experience_collection(self):
        """关键：.md 必须显式传 experience_kb，不能落 engine 默认的 standards"""
        # 移除 setUp 里的 sample.pdf，避免同秒时间戳下「最新」排序不稳定
        self.tracker.delete_file(str(self.file_path))
        md_path = self._upsert_for("经验排查.md")
        with patch("doc_mgr.engine.process_file") as process:
            process.return_value = SimpleNamespace(
                status="done", chunk_count=2, collection="experience_kb", message="")
            result = self.service.learn_for_user("uploader-union-id")
        self.assertEqual("experience_kb",
                         process.call_args.kwargs["target_collection"])
        self.assertEqual("experience_kb", result["collection"])

    def test_learn_ignores_other_users_pending_files(self):
        """只能学习自己上传的文件；别人 pending 的文件不处理"""
        with patch("doc_mgr.engine.process_file") as process:
            result = self.service.learn_for_user("another-user")
        self.assertEqual("no_file", result["status"])
        process.assert_not_called()

    def test_learn_process_error_marks_tracker_error(self):
        with patch("doc_mgr.engine.process_file",
                   side_effect=RuntimeError("boom")):
            result = self.service.learn_for_user("uploader-union-id")
        self.assertEqual("failed", result["status"])
        row = self.tracker.get_status(str(self.file_path))
        self.assertEqual("error", row["sync_status"])

    def test_delete_own_file_removes_content_source_and_record(self):
        """删除：按 doc_id 删知识库内容 + 删源文件 + 清 tracker 记录"""
        fake = _FakeStore()
        with patch("doc_mgr.engine.get_store", return_value=fake):
            result = self.service.delete_for_user("uploader-union-id", "1")
        self.assertEqual("ok", result["status"])
        self.assertEqual(2, result["deleted_chunks"])
        self.assertTrue(result["source_deleted"])
        self.assertFalse(self.file_path.exists())
        self.assertIsNone(self.tracker.get_status(str(self.file_path)))
        self.assertEqual("standards", fake.deletes[0][0])
        self.assertEqual(["blk-1", "blk-2"], fake.deletes[0][1])

    def test_delete_requests_all_versions_include_hidden(self):
        """审查 Critical 4 回归：删除学习必须请求全部版本（include_hidden=True）。
        此前 get 走可见性过滤只取 active，retired 块残留，崩溃恢复会把它恢复为
        active —— 用户删掉的内容隔天「复活」。"""
        fake = _FakeStore()
        with patch("doc_mgr.engine.get_store", return_value=fake):
            result = self.service.delete_for_user("uploader-union-id", "1")
        self.assertEqual("ok", result["status"])
        # 取版本时必须 include_hidden=True（覆盖 retired/staging）
        get_call = fake.gets[-1]
        self.assertIs(get_call[-1], True)
        # delete 按 ids 物理删除全部版本（ids 路径不受可见性过滤限制）
        self.assertEqual(["blk-1", "blk-2"], fake.deletes[0][1])

    def test_other_user_cannot_see_or_delete_others_file(self):
        """越权防护：其他用户看不到/删不掉 uploader 的文件，且不触碰任何数据"""
        with patch("doc_mgr.engine.get_store") as store:
            result = self.service.delete_for_user("other-user", "sample.pdf")
        # 该用户自己的文件列表为空 → no_file（找不到即可，数据毫发无损）
        self.assertEqual("no_file", result["status"])
        store.assert_not_called()
        self.assertTrue(self.file_path.exists())
        self.assertIsNotNone(self.tracker.get_status(str(self.file_path)))

    def test_admin_can_delete_any_users_file(self):
        """管理员模式可删任意用户文件"""
        fake = _FakeStore()
        with patch("doc_mgr.engine.get_store", return_value=fake):
            result = self.service.delete_for_user("admin-user", "1",
                                                  is_admin=True)
        self.assertEqual("ok", result["status"])
        self.assertFalse(self.file_path.exists())

    def test_delete_ambiguous_filename_asks_for_sequence(self):
        """文件名模糊匹配多个时要求用序号指定，不误删"""
        a = self._upsert_for("报告.pdf")
        b = self._upsert_for("报告2.pdf")
        result = self.service.delete_for_user("uploader-union-id", "报告")
        self.assertEqual("ambiguous", result["status"])
        self.assertTrue(a.exists())
        self.assertTrue(b.exists())

    def test_relearn_forces_reprocessing(self):
        """重新学习：force=True 重新解析入库"""
        with patch("doc_mgr.engine.process_file") as process:
            process.return_value = SimpleNamespace(
                status="done", chunk_count=3, collection="standards", message="")
            result = self.service.relearn_for_user("uploader-union-id", "1")
        self.assertEqual("ok", result["status"])
        self.assertEqual(True, process.call_args.kwargs["force"])
        self.assertEqual("public", process.call_args.kwargs["department"])

    def test_list_files_scope_user_vs_admin(self):
        mine = self.service.list_files_for_user("uploader-union-id")
        self.assertEqual(1, len(mine))
        all_files = self.service.list_files_for_user("uploader-union-id",
                                                     is_admin=True)
        self.assertEqual(1, len(all_files))


class ExperienceReviewRoutingTests(unittest.TestCase):
    """经验知识库审核路由（第二步 experience_kb）"""

    def test_default_collection_md_is_experience_kb(self):
        self.assertEqual(default_collection("某经验.md"), "experience_kb")

    def test_default_collection_pdf_standards(self):
        self.assertEqual(default_collection("标准.pdf"), "standards")

    def test_collection_from_text_experience_alias(self):
        self.assertEqual(_collection_from_text("经验库", "某经验.md"), "experience_kb")
        self.assertEqual(_collection_from_text("经验知识库", "某经验.md"), "experience_kb")
        self.assertEqual(_collection_from_text("经验知识", "某经验.md"), "experience_kb")


class LearnFilePathTests(unittest.TestCase):
    """v1.11.0 按指定路径入库（上传后「推荐入库」确认用）"""

    def _svc(self):
        return KnowledgeReviewService()

    def test_learn_file_path_success(self):
        fake_doc = SimpleNamespace(status="done", chunk_count=3, message="")
        svc = self._svc()
        with patch.object(svc, "_is_safe_upload_file", return_value=True), \
             patch("doc_mgr.engine.process_file",
                   return_value=fake_doc) as m_process:
            result = svc.learn_file_path("u1", "/x/测试.md", "测试.md")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["chunk_count"], 3)
        # .md → default_collection 应入 experience_kb（engine 内部默认 standards 已显式覆盖）
        self.assertEqual(m_process.call_args.kwargs["target_collection"], "experience_kb")

    def test_learn_file_path_unsafe_rejected(self):
        svc = self._svc()
        with patch.object(svc, "_is_safe_upload_file", return_value=False), \
             patch("doc_mgr.engine.process_file") as m_process:
            result = svc.learn_file_path("u1", "/bad/path.pdf", "bad.pdf")
        self.assertEqual(result["status"], "failed")
        m_process.assert_not_called()

    def test_learn_file_path_empty(self):
        svc = self._svc()
        with patch("doc_mgr.engine.process_file") as m_process:
            result = svc.learn_file_path("u1", "", "")
        self.assertEqual(result["status"], "no_file")
        m_process.assert_not_called()

    def test_learn_file_path_process_failure(self):
        fake_doc = SimpleNamespace(status="error", chunk_count=0, message="解析失败")
        svc = self._svc()
        with patch.object(svc, "_is_safe_upload_file", return_value=True), \
             patch("doc_mgr.engine.process_file", return_value=fake_doc):
            result = svc.learn_file_path("u1", "/x/a.pdf", "a.pdf")
        self.assertEqual(result["status"], "failed")
        self.assertIn("解析失败", result["message"])


class PendingLearnTests(unittest.TestCase):
    """上传推荐入库的内存 pending"""

    def test_pending_flow(self):
        set_pending_learn("u1", "/x/文件.pdf", "文件.pdf")
        self.assertEqual(get_pending_learn("u1"),
                         {"file_path": "/x/文件.pdf", "file_name": "文件.pdf"})
        clear_pending_learn("u1")
        self.assertIsNone(get_pending_learn("u1"))

    def test_learn_file_path_for_user_entry(self):
        svc = KnowledgeReviewService()
        with patch.object(svc, "learn_file_path",
                          return_value={"status": "ok"}) as m_learn, \
             patch("knowledge_review.get_review_service", return_value=svc):
            result = learn_file_path_for_user("u1", "/x/a.pdf", "a.pdf")
        self.assertEqual(result["status"], "ok")
        # v1.11.5：模块级入口透传 kb 参数（未指定时 None）
        m_learn.assert_called_once_with("u1", "/x/a.pdf", "a.pdf", kb=None)


if __name__ == "__main__":
    unittest.main()
