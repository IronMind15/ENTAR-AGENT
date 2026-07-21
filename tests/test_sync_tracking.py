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
