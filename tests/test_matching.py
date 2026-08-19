from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from media_dup_finder.matching import compare_records, group_similar_files
from media_dup_finder.models import FileRecord
from media_dup_finder.normalization import normalize_stem


def record(folder: Path, name: str, size: int = 1000) -> FileRecord:
    path = folder / name
    return FileRecord(
        path=path,
        root=folder,
        size=size,
        modified_time=1.0,
        name_info=normalize_stem(path.stem),
    )


class MatchingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = Path(tempfile.gettempdir()) / "media-dup-tests"

    def test_user_catalog_example_is_one_group(self) -> None:
        files = [
            record(self.folder, "MIDA-630.mp4", 100),
            record(self.folder, "MIDA-630-C.mov", 200),
            record(self.folder, "MIDA-630-4k.mkv", 300),
        ]
        groups = group_similar_files(files)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0].files), 3)
        self.assertEqual(groups[0].confidence, 1.0)

    def test_user_chinese_example_is_one_group(self) -> None:
        files = [
            record(self.folder, "寒战.mp4"),
            record(self.folder, "寒战1.mov"),
            record(self.folder, "经典剧情《寒战1》.rmvb"),
        ]
        groups = group_similar_files(files)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0].files), 3)

    def test_different_catalog_numbers_never_match(self) -> None:
        left = record(self.folder, "MIDA-630.mp4")
        right = record(self.folder, "MIDA-631.mp4")
        score, _ = compare_records(left, right)
        self.assertEqual(score, 0.0)
        self.assertEqual(group_similar_files([left, right]), [])

    def test_split_parts_are_protected(self) -> None:
        left = record(self.folder, "MIDA-630-CD1.mp4")
        right = record(self.folder, "MIDA-630-CD2.mp4")
        score, reason = compare_records(left, right)
        self.assertEqual(score, 0.0)
        self.assertIn("分段", reason)
        self.assertEqual(group_similar_files([left, right]), [])

    def test_4k_hint_wins_quality_suggestion(self) -> None:
        low = record(self.folder, "MIDA-630-1080p.mp4", size=2_000_000)
        high = record(self.folder, "MIDA-630-4k.mkv", size=1_500_000)
        self.assertGreater(high.quality_rank, low.quality_rank)

    def test_title_with_optional_year_matches(self) -> None:
        files = [
            record(self.folder, "流浪地球.2019.2160p.mkv"),
            record(self.folder, "流浪地球【国语中字】1080P.mp4"),
        ]
        groups = group_similar_files(files)
        self.assertEqual(len(groups), 1)
        self.assertGreaterEqual(groups[0].confidence, 0.95)

    def test_different_explicit_years_are_protected(self) -> None:
        left = record(self.folder, "The Thing 1982.mkv")
        right = record(self.folder, "The Thing 2011.mp4")
        score, reason = compare_records(left, right)
        self.assertEqual(score, 0.0)
        self.assertIn("年份", reason)

    def test_reordered_english_title_words_match(self) -> None:
        left = record(self.folder, "Mission Impossible Fallout.mkv")
        right = record(self.folder, "Fallout Mission Impossible.mp4")
        score, reason = compare_records(left, right)
        self.assertGreaterEqual(score, 0.95)
        self.assertIn("顺序", reason)

    def test_traditional_and_simplified_titles_match(self) -> None:
        files = [record(self.folder, "無間道.mkv"), record(self.folder, "无间道.mp4")]
        self.assertEqual(len(group_similar_files(files)), 1)

    def test_website_prefix_does_not_block_match(self) -> None:
        files = [
            record(self.folder, "[www.example.com]寒战.1080p-RARBG.mp4"),
            record(self.folder, "寒战.mkv"),
        ]
        self.assertEqual(len(group_similar_files(files)), 1)

    def test_roman_and_arabic_sequel_numbers_match(self) -> None:
        files = [record(self.folder, "寒战Ⅱ.mkv"), record(self.folder, "寒战2.mp4")]
        self.assertEqual(len(group_similar_files(files)), 1)

    def test_roman_sequel_with_year_and_quality_suffix_matches(self) -> None:
        files = [
            record(self.folder, "無間道Ⅱ.2003.1080p.BluRay.mkv"),
            record(self.folder, "无间道2.mp4"),
        ]
        self.assertEqual(len(group_similar_files(files)), 1)

    def test_catalog_leading_zeros_match(self) -> None:
        files = [record(self.folder, "ABC-001.mkv"), record(self.folder, "ABC-1.mp4")]
        self.assertEqual(len(group_similar_files(files)), 1)



if __name__ == "__main__":
    unittest.main()
