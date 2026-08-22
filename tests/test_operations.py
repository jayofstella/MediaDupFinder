from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from media_dup_finder.operations import (
    DeletionCandidate,
    _portable_quarantine_one,
    classify_deletion_candidates,
    send_checked_deletion_candidates,
    validate_delete_paths,
    write_ignored_report,
)


class OperationTests(unittest.TestCase):
    def test_validation_rejects_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ValueError):
                validate_delete_paths([Path(temporary)])

    def test_portable_fallback_is_recoverable_move(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "duplicate.mp4"
            source.write_bytes(b"content")
            result = _portable_quarantine_one(source)
            self.assertTrue(result.success)
            self.assertFalse(source.exists())
            moved = list((Path(temporary) / ".MediaDupFinder_Recycle").rglob("duplicate.mp4"))
            self.assertEqual(len(moved), 1)
            self.assertEqual(moved[0].read_bytes(), b"content")

    def test_stale_candidate_is_ignored_while_valid_candidate_continues(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            valid = folder / "valid.mp4"
            valid.write_bytes(b"valid content")
            valid_stat = valid.stat()
            missing = folder / "missing.mp4"
            candidates = [
                DeletionCandidate(missing, 123, 1.0, group_name="作品 A"),
                DeletionCandidate(
                    valid, valid_stat.st_size, valid_stat.st_mtime,
                    group_name="作品 B",
                ),
            ]

            result = send_checked_deletion_candidates(
                candidates, history_path=folder / "history.jsonl",
            )

            self.assertEqual(len(result.succeeded), 1)
            self.assertEqual(len(result.ignored), 1)
            self.assertEqual(len(result.failed), 0)
            self.assertFalse(valid.exists())
            self.assertIn("不存在", result.ignored[0].message)

            ignored_report = write_ignored_report(
                result.ignored,
                preferred_directory=folder,
                source_report=folder / "old-report.csv",
            )
            self.assertIsNotNone(ignored_report)
            content = ignored_report.read_text(encoding="utf-8-sig")
            self.assertIn("忽略原因", content)
            self.assertIn("missing.mp4", content)
            self.assertIn("作品 A", content)

    def test_changed_size_is_classified_as_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "changed.mp4"
            path.write_bytes(b"new-size")
            ready, ignored = classify_deletion_candidates([
                DeletionCandidate(path, 1, path.stat().st_mtime),
            ])
            self.assertEqual(ready, [])
            self.assertEqual(len(ignored), 1)
            self.assertIn("大小已变化", ignored[0].message)


if __name__ == "__main__":
    unittest.main()
