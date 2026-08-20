from __future__ import annotations

import unittest
from pathlib import Path

from media_dup_finder.models import FileRecord
from media_dup_finder.normalization import normalize_stem
from media_dup_finder.scan_filters import (
    SCOPE_ALL,
    SCOPE_DIFFERENT_FOLDER,
    SCOPE_DIFFERENT_ROOT,
    SCOPE_SAME_FOLDER,
    comparison_pair_allowed,
    format_megabytes_setting,
    megabytes_to_bytes,
    parse_exclude_keywords,
    parse_extensions,
    parse_maximum_size_mb,
    parse_minimum_size_mb,
)


def record(path: Path, root: Path) -> FileRecord:
    return FileRecord(path, root, 100, 1.0, normalize_stem(path.stem))


class ScanFilterTests(unittest.TestCase):
    def test_minimum_size_accepts_integer_decimal_and_comma_decimal(self) -> None:
        self.assertEqual(parse_minimum_size_mb("50"), 50.0)
        self.assertEqual(parse_minimum_size_mb("100.5"), 100.5)
        self.assertEqual(parse_minimum_size_mb("1,25"), 1.25)
        self.assertEqual(megabytes_to_bytes(1.5), 1_572_864)
        self.assertEqual(format_megabytes_setting("50.000"), "50")

    def test_minimum_size_rejects_negative_and_non_finite_values(self) -> None:
        for value in ("-1", "nan", "inf", "abc"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    parse_minimum_size_mb(value)

    def test_exclude_keywords_support_chinese_and_multiple_separators(self) -> None:
        self.assertEqual(
            parse_exclude_keywords(" Sample;预告，TRAILER\nsample "),
            ("sample", "预告", "trailer"),
        )

    def test_maximum_size_and_custom_extensions_are_parsed(self) -> None:
        self.assertEqual(parse_maximum_size_mb("5000.5"), 5000.5)
        self.assertEqual(
            parse_extensions("*.MP4; mkv，.vob mp4"),
            (".mp4", ".mkv", ".vob"),
        )
        with self.assertRaises(ValueError):
            parse_extensions("mp4;bad/ext")

    def test_comparison_scope_distinguishes_folder_and_scan_root(self) -> None:
        root_a = Path("drive-a")
        root_b = Path("drive-b")
        left = record(root_a / "movies" / "MIDA-630.mp4", root_a)
        same_folder = record(root_a / "movies" / "MIDA-630.mkv", root_a)
        other_folder = record(root_a / "archive" / "MIDA-630.mov", root_a)
        other_root = record(root_b / "backup" / "MIDA-630.avi", root_b)

        self.assertTrue(comparison_pair_allowed(left, same_folder, SCOPE_ALL))
        self.assertTrue(comparison_pair_allowed(left, same_folder, SCOPE_SAME_FOLDER))
        self.assertFalse(comparison_pair_allowed(left, same_folder, SCOPE_DIFFERENT_FOLDER))
        self.assertTrue(comparison_pair_allowed(left, other_folder, SCOPE_DIFFERENT_FOLDER))
        self.assertFalse(comparison_pair_allowed(left, other_folder, SCOPE_DIFFERENT_ROOT))
        self.assertTrue(comparison_pair_allowed(left, other_root, SCOPE_DIFFERENT_ROOT))


if __name__ == "__main__":
    unittest.main()
