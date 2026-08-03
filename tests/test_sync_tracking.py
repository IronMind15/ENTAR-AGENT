import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from doc_mgr import scheduler
from doc_mgr.models import Document
from doc_mgr.sync_tracker import SyncTracker


class SyncTrackerTests(unittest.TestCase):
    def test_processing_upsert_keeps_original_uploader(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            tracker = SyncTracker(str(Path(temp_dir) / "tracker.db"))
            tracker.upsert_file(
                "a.pdf", "a.pdf", 1, "hash-a", "standards",
                upload_user_id="union-1", upload_user_name="上传员工",
            )

            # 文档引擎处理完成时不会再次携带上传者参数。
            tracker.upsert_file("a.pdf", "a.pdf", 1, "hash-a", "standards")

            row = tracker.get_status("a.pdf")
            self.assertEqual("union-1", row["upload_user_id"])
            self.assertEqual("上传员工", row["upload_user_name"])
            tracker.close()

    def test_upsert_without_department_keeps_existing(self):
        """同步路径不带 department 时，不能把已指定的部门覆盖为 public"""
        with tempfile.TemporaryDirectory() as temp_dir:
            tracker = SyncTracker(str(Path(temp_dir) / "tracker.db"))
            # 上传时指定研发中心
            tracker.upsert_file(
                "a.pdf", "a.pdf", 1, "hash-a", "standards",
                suggested_department="rd",
            )
            # 同步处理时未携带 department（等同默认空串）
            tracker.upsert_file("a.pdf", "a.pdf", 1, "hash-a", "standards")

            row = tracker.get_status("a.pdf")
            self.assertEqual("rd", row["suggested_department"])
            tracker.close()

    def test_upsert_without_department_first_insert_defaults_public(self):
        """首次插入未指定部门时，落为 public"""
        with tempfile.TemporaryDirectory() as temp_dir:
            tracker = SyncTracker(str(Path(temp_dir) / "tracker.db"))
            tracker.upsert_file("a.pdf", "a.pdf", 1, "hash-a", "standards")

            row = tracker.get_status("a.pdf")
            self.assertEqual("public", row["suggested_department"])
            tracker.close()

    def test_update_department_writes_after_registration(self):
        """审核申请创建时 update_department 应真正落库，供批准后读取"""
        with tempfile.TemporaryDirectory() as temp_dir:
            tracker = SyncTracker(str(Path(temp_dir) / "tracker.db"))
            tracker.upsert_file("a.pdf", "a.pdf", 1, "hash-a", "standards")
            tracker.update_department("a.pdf", "rd")

            row = tracker.get_status("a.pdf")
            self.assertEqual("rd", row["suggested_department"])
            tracker.close()

    def test_scan_sync_passes_existing_department_to_process(self):
        """批量/调度同步路径应从记录读取部门并传给 process_file"""
        with tempfile.TemporaryDirectory() as temp_dir:
            watched = Path(temp_dir) / "watched"
            watched.mkdir()
            file_path = watched / "dept.md"
            file_path.write_text("content", encoding="utf-8")

            tracker = Mock()
            tracker.get_status.return_value = {
                "sync_status": "error",       # 未 synced → 走处理分支
                "file_hash": "old-hash",
                "target_collection": "standards",
                "suggested_department": "rd",
            }
            process = Mock(return_value=Document(
                file_name="dept.md", collection="standards",
                status="done", chunk_count=1, source="markdown",
            ))
            with patch.object(scheduler, "SCAN_DIRS", {"watched": "standards"}), \
                    patch.object(scheduler, "_resolve", return_value=str(watched)), \
                    patch.object(scheduler, "SyncTracker", return_value=tracker), \
                    patch.object(scheduler, "check_chroma_has_file") as chroma_check, \
                    patch.object(scheduler, "process_file", process):
                scheduler._scan_and_sync()

            process.assert_called_once()
            self.assertEqual("rd", process.call_args.kwargs["department"])
            tracker.close()

    def test_content_change_moves_synced_file_back_to_pending(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            tracker = SyncTracker(str(Path(temp_dir) / "tracker.db"))
            tracker.upsert_file("a.pdf", "a.pdf", 1, "hash-a", "standards")
            tracker.mark_synced("a.pdf")

            tracker.upsert_file("a.pdf", "a.pdf", 1, "hash-a", "standards")
            self.assertEqual("synced", tracker.get_status("a.pdf")["sync_status"])

            tracker.upsert_file("a.pdf", "a.pdf", 1, "hash-b", "standards")
            self.assertEqual("pending", tracker.get_status("a.pdf")["sync_status"])
            tracker.close()

    def test_sha256_detects_change_even_when_mtime_is_unchanged(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "same-time.pdf"
            fixed_time = 1_700_000_000
            file_path.write_bytes(b"AAAA")
            os.utime(file_path, (fixed_time, fixed_time))
            first = scheduler._compute_hash(str(file_path))

            file_path.write_bytes(b"BBBB")
            os.utime(file_path, (fixed_time, fixed_time))
            second = scheduler._compute_hash(str(file_path))
            self.assertNotEqual(first, second)

    def test_changed_tracked_file_is_not_skipped_by_chroma_name_check(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            watched = Path(temp_dir) / "watched"
            watched.mkdir()
            file_path = watched / "changed.md"
            file_path.write_text("new content", encoding="utf-8")

            tracker = Mock()
            tracker.get_status.return_value = {
                "sync_status": "synced",
                "file_hash": "old-hash",
            }
            process = Mock(return_value=Document(
                file_name="changed.md", collection="standards",
                status="done", chunk_count=1, source="markdown",
            ))
            with patch.object(scheduler, "SCAN_DIRS", {"watched": "standards"}), \
                    patch.object(scheduler, "_resolve", return_value=str(watched)), \
                    patch.object(scheduler, "SyncTracker", return_value=tracker), \
                    patch.object(scheduler, "check_chroma_has_file") as chroma_check, \
                    patch.object(scheduler, "process_file", process):
                scheduler._scan_and_sync()

            chroma_check.assert_not_called()
            process.assert_called_once()
            self.assertEqual("standards", process.call_args.kwargs["target_collection"])

    def test_unchanged_legacy_mtime_record_is_migrated_without_reprocessing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            watched = Path(temp_dir) / "watched"
            watched.mkdir()
            file_path = watched / "legacy.md"
            file_path.write_text("same content", encoding="utf-8")
            legacy_mtime = str(int(os.path.getmtime(file_path)))

            tracker = Mock()
            tracker.get_status.return_value = {
                "sync_status": "synced",
                "file_hash": legacy_mtime,
                "target_collection": "standards",
            }
            with patch.object(scheduler, "SCAN_DIRS", {"watched": "standards"}), \
                    patch.object(scheduler, "_resolve", return_value=str(watched)), \
                    patch.object(scheduler, "SyncTracker", return_value=tracker), \
                    patch.object(scheduler, "check_chroma_has_file") as chroma_check, \
                    patch.object(scheduler, "process_file") as process:
                scheduler._scan_and_sync()

            process.assert_not_called()
            chroma_check.assert_not_called()
            tracker.upsert_file.assert_called_once()
            tracker.mark_synced.assert_called_once_with(str(file_path))
            migrated_hash = tracker.upsert_file.call_args.args[3]
            self.assertEqual(64, len(migrated_hash))


if __name__ == "__main__":
    unittest.main()
