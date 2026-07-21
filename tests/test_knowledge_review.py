import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from dingtalk_notifier import DingTalkNotifier
from doc_mgr.identity import file_sha256
from doc_mgr.sync_tracker import SyncTracker
from file_handler import sanitize_file_name
from knowledge_review import KnowledgeReviewService


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

        def submitter(row, collection):
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

        def fail_submit(_row, _collection):
            raise RuntimeError("queue unavailable")

        self.service.submitter = fail_submit
        answer = self.service.handle_command(
            f"同意同步 {request['review_id']}", "boss-staff-id"
        )
        self.assertIn("已恢复为待审核", answer)
        row = self.tracker.get_review(request["review_id"])
        self.assertEqual("pending", row["review_status"])


if __name__ == "__main__":
    unittest.main()
