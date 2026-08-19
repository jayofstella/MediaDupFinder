from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from media_dup_finder.app import MediaDupFinderApp
from media_dup_finder.metadata import assess_group_metadata
from media_dup_finder.models import DuplicateGroup, FileRecord
from media_dup_finder.normalization import normalize_stem


def record(folder: Path, name: str, duration: float) -> FileRecord:
    path = folder / name
    item = FileRecord(path, folder, 100, 0, normalize_stem(path.stem))
    item.duration_seconds = duration
    return item


class MetadataSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = Path(tempfile.gettempdir()) / "media-dup-metadata-tests"

    def test_close_duration_adds_positive_note(self) -> None:
        group = DuplicateGroup("g", [
            record(self.folder, "电影.mp4", 7200),
            record(self.folder, "电影-4k.mkv", 7210),
        ], 0.9, "文件名相似")
        assess_group_metadata(group)
        self.assertFalse(group.safety_warning)
        self.assertIn("时长接近", group.metadata_note)

    def test_large_duration_difference_blocks_bulk_suggestion(self) -> None:
        group = DuplicateGroup("g", [
            record(self.folder, "电影.mp4", 3600),
            record(self.folder, "电影-4k.mkv", 7200),
        ], 0.9, "文件名相似")
        assess_group_metadata(group)
        self.assertTrue(group.safety_warning)
        selected_all = MediaDupFinderApp._apply_smart_choice(group)
        self.assertFalse(selected_all)
        self.assertEqual(sum(item.action == "删除" for item in group.files), 0)
        self.assertEqual(sum(item.action == "保留" for item in group.files), 1)

    def test_three_percent_duration_difference_already_requires_review(self) -> None:
        group = DuplicateGroup("g", [
            record(self.folder, "电影.mp4", 3600),
            record(self.folder, "电影-4k.mkv", 3750),
        ], 0.9, "作品身份相同")
        assess_group_metadata(group)
        self.assertTrue(group.safety_warning)
        self.assertIn("2.5 分钟", group.metadata_note)

    def test_hash_only_group_does_not_need_duration_review(self) -> None:
        files = [
            record(self.folder, "a.mp4", 3600),
            record(self.folder, "b.mkv", 7200),
        ]
        for item in files:
            item.content_md5 = "abc"
        group = DuplicateGroup("g", files, 1.0, "MD5相同", match_kind="hash")
        assess_group_metadata(group)
        self.assertFalse(group.safety_warning)
        self.assertIn("MD5", group.metadata_note)


if __name__ == "__main__":
    unittest.main()
