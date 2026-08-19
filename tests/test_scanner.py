from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from media_dup_finder.scanner import scan_directories


class ScannerTests(unittest.TestCase):
    def test_video_only_recursive_and_overlapping_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            nested = root / "nested"
            nested.mkdir()
            (root / "A.mp4").write_bytes(b"a")
            (root / "notes.txt").write_text("x", encoding="utf-8")
            (nested / "A-4k.mkv").write_bytes(b"b")

            files, warnings, cancelled = scan_directories([root, nested], recursive=True)
            self.assertFalse(cancelled)
            self.assertEqual(warnings, [])
            self.assertEqual({item.path.name for item in files}, {"A.mp4", "A-4k.mkv"})

    def test_empty_extension_collection_scans_all_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "video.mp4").write_bytes(b"a")
            (root / "notes.txt").write_text("x", encoding="utf-8")
            files, _, _ = scan_directories([root], extensions=[])
            self.assertEqual({item.path.name for item in files}, {"video.mp4", "notes.txt"})


if __name__ == "__main__":
    unittest.main()

