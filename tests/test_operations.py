from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from media_dup_finder.operations import _portable_quarantine_one, validate_delete_paths


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


if __name__ == "__main__":
    unittest.main()

