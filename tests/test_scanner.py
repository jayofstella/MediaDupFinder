from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from media_dup_finder.models import ScanStatistics
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

    def test_minimum_size_is_applied_before_comparison(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "small.mp4").write_bytes(b"123456789")
            (root / "boundary.mp4").write_bytes(b"1234567890")
            statistics = ScanStatistics()
            files, warnings, _ = scan_directories(
                [root], min_size_bytes=10, statistics=statistics,
            )
            self.assertEqual(warnings, [])
            self.assertEqual([item.path.name for item in files], ["boundary.mp4"])
            self.assertEqual(statistics.skipped_too_small, 1)
            self.assertEqual(statistics.included_files, 1)

    def test_optional_hidden_incomplete_and_keyword_filters(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".hidden.mp4").write_bytes(b"h")
            (root / "movie.mp4.part").write_bytes(b"p")
            (root / "movie-sample.mp4").write_bytes(b"s")
            (root / "movie-full.mp4").write_bytes(b"f")
            statistics = ScanStatistics()
            files, _, _ = scan_directories(
                [root],
                extensions=[],
                skip_hidden_system=True,
                skip_incomplete=True,
                exclude_name_keywords=("sample",),
                statistics=statistics,
            )
            self.assertEqual([item.path.name for item in files], ["movie-full.mp4"])
            self.assertEqual(statistics.skipped_hidden_system_files, 1)
            self.assertEqual(statistics.skipped_incomplete, 1)
            self.assertEqual(statistics.skipped_keyword, 1)

    def test_maximum_size_and_excluded_directory_are_applied(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            excluded = root / "cache"
            excluded.mkdir()
            (root / "normal.mp4").write_bytes(b"12345")
            (root / "too-large.mp4").write_bytes(b"12345678901")
            (excluded / "ignored.mp4").write_bytes(b"12345")
            statistics = ScanStatistics()
            files, warnings, cancelled = scan_directories(
                [root],
                max_size_bytes=10,
                excluded_directories=(excluded,),
                statistics=statistics,
            )
            self.assertFalse(cancelled)
            self.assertEqual(warnings, [])
            self.assertEqual([item.path.name for item in files], ["normal.mp4"])
            self.assertEqual(statistics.skipped_too_large, 1)
            self.assertEqual(statistics.skipped_excluded_directories, 1)

    def test_scanner_uses_parent_folder_for_generic_dvd_names(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            video_ts = root / "STARS-140" / "VIDEO_TS"
            video_ts.mkdir(parents=True)
            (video_ts / "VTS_01_1.VOB").write_bytes(b"video")
            files, warnings, cancelled = scan_directories([root])
            self.assertFalse(cancelled)
            self.assertEqual(warnings, [])
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].name_info.catalog_key, "stars-140")
            self.assertEqual(files[0].name_info.identity_source, "上级影片目录")


if __name__ == "__main__":
    unittest.main()
