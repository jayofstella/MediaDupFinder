from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path

from media_dup_finder.app import (
    REPORT_COLUMNS,
    MediaDupFinderApp,
    build_file_information,
    build_report_row,
    content_relation_label,
    format_scan_filter_summary,
    format_matching_progress_text,
    normalize_column_order,
    sort_records_for_display,
)
from media_dup_finder.matching import MatchingProgressState
from media_dup_finder.models import DuplicateGroup, FileRecord
from media_dup_finder.models import ScanStatistics
from media_dup_finder.normalization import normalize_stem


class ApplicationBehaviorTests(unittest.TestCase):
    def test_result_sorting_uses_numeric_values_and_leaves_unknown_duration_last(self) -> None:
        folder = Path(tempfile.gettempdir()) / "media-dup-sort-tests"
        records = []
        for name, size, duration in (
            ("small.mp4", 100, 30.0),
            ("large.mp4", 300, 90.0),
            ("unknown.mp4", 200, None),
        ):
            path = folder / name
            item = FileRecord(path, folder, size, 1.0, normalize_stem(path.stem))
            item.duration_seconds = duration
            records.append(item)
        by_size = sort_records_for_display(records, "size", reverse=True)
        self.assertEqual([item.size for item in by_size], [300, 200, 100])
        by_duration = sort_records_for_display(records, "duration", reverse=True)
        self.assertEqual(
            [item.duration_seconds for item in by_duration], [90.0, 30.0, None]
        )

    def test_matching_progress_text_contains_remaining_count_speed_and_eta(self) -> None:
        state = MatchingProgressState(
            phase="核对作品身份",
            processed_items=25,
            total_items=100,
            current_name="寒战.mp4 ↔ 寒战1.mkv",
            elapsed_seconds=10.0,
            eta_seconds=30.0,
            unit="次比对",
        )
        text = format_matching_progress_text(state)
        self.assertIn("已比对 25/100", text)
        self.assertIn("剩余 75 次比对", text)
        self.assertIn("2.5 次/秒", text)
        self.assertIn("已用 00:10", text)
        self.assertIn("预计剩余 00:30", text)
        self.assertIn("寒战.mp4 ↔ 寒战1.mkv", text)

    def test_scan_has_no_mid_run_md5_confirmation(self) -> None:
        source = inspect.getsource(MediaDupFinderApp.start_scan)
        self.assertNotIn("hash_confirmation", source)
        self.assertNotIn("askyesnocancel", source)

    def test_filter_summary_explains_why_files_were_skipped(self) -> None:
        statistics = ScanStatistics(
            skipped_too_small=12,
            skipped_too_large=4,
            skipped_hidden_system_files=3,
            skipped_excluded_directories=2,
            skipped_keyword=2,
        )
        text = format_scan_filter_summary(statistics)
        self.assertIn("小文件 12", text)
        self.assertIn("超大文件 4", text)
        self.assertIn("隐藏/系统文件 3", text)
        self.assertIn("排除目录 2", text)
        self.assertIn("关键字排除 2", text)

    def test_saved_column_order_is_validated_and_completed(self) -> None:
        self.assertEqual(
            normalize_column_order(
                ["size", "name", "size", "removed"],
                ("name", "size", "duration"),
            ),
            ["size", "name", "duration"],
        )

    def test_content_relation_exposes_exact_md5_subset(self) -> None:
        folder = Path(tempfile.gettempdir()) / "media-dup-relation-tests"
        records = [
            FileRecord(folder / name, folder, 100, 1.0, normalize_stem(Path(name).stem))
            for name in ("one.mp4", "two.mkv", "three.mov")
        ]
        records[0].content_md5 = records[1].content_md5 = "a" * 32
        records[2].content_md5 = "b" * 32
        group = DuplicateGroup("g", records, 1.0, "test", match_kind="mixed")
        self.assertIn("MD5相同 ×2", content_relation_label(records[0], group))
        self.assertIn("组内不同", content_relation_label(records[2], group))

    def test_complete_file_information_contains_operational_details(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "MIDA-630-4K.mp4"
            path.write_bytes(b"video")
            stat = path.stat()
            record = FileRecord(
                path=path,
                root=path.parent,
                size=stat.st_size,
                modified_time=stat.st_mtime,
                name_info=normalize_stem(path.stem),
            )
            record.width = 3840
            record.height = 2160
            record.duration_seconds = 123.5
            record.codec = "hevc"
            record.content_md5 = "a" * 32
            record.hash_source = "完整 MD5"
            group = DuplicateGroup(
                group_id="test-group",
                files=[record],
                confidence=1.0,
                reason="作品编号完全相同",
            )
            content = build_file_information(record, group)
            self.assertIn(str(path), content)
            self.assertIn("3840×2160", content)
            self.assertIn("hevc", content)
            self.assertIn("a" * 32, content)
            self.assertIn("作品编号完全相同", content)

            row = build_report_row(1, group, record)
            self.assertEqual(len(row), len(REPORT_COLUMNS))
            self.assertEqual(row[REPORT_COLUMNS.index("识别作品")], "MIDA-630")
            self.assertEqual(row[REPORT_COLUMNS.index("身份类型")], "作品编号")
            self.assertEqual(row[REPORT_COLUMNS.index("编码")], "hevc")


if __name__ == "__main__":
    unittest.main()
