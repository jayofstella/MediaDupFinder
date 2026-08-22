from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from media_dup_finder.app import (
    REPORT_COLUMNS,
    MediaDupFinderApp,
    analyze_deletion_plan,
    build_safe_deletion_plan,
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

            record.snapshot_time_precision = "seconds"
            imported_row = build_report_row(1, group, record)
            self.assertEqual(
                imported_row[REPORT_COLUMNS.index("扫描快照时间戳")], "",
            )

    def test_missing_or_changed_keep_file_protects_the_rest_of_its_group(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            keep_path = folder / "MIDA-630-4K.mp4"
            delete_path = folder / "MIDA-630-rm.mp4"
            keep_path.write_bytes(b"keep")
            delete_path.write_bytes(b"delete")
            keep_stat = keep_path.stat()
            delete_stat = delete_path.stat()
            keep = FileRecord(
                keep_path, folder, keep_stat.st_size, keep_stat.st_mtime,
                normalize_stem(keep_path.stem), action="保留",
            )
            delete = FileRecord(
                delete_path, folder, delete_stat.st_size, delete_stat.st_mtime,
                normalize_stem(delete_path.stem), action="删除",
            )
            group = DuplicateGroup("g", [keep, delete], 1.0, "test")
            keep_path.unlink()

            ready, ignored = build_safe_deletion_plan([group])

            self.assertEqual(ready, [])
            self.assertEqual(len(ignored), 2)
            delete_issue = next(item for item in ignored if item.path == delete_path)
            self.assertIn("没有状态正常的保留文件", delete_issue.message)
            keep_issue = next(item for item in ignored if item.path == keep_path)
            self.assertTrue(keep_issue.preserve_action)
            self.assertTrue(delete_path.exists())

    def test_continue_all_survivor_choice_applies_to_following_groups(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            groups = []
            for number in (1, 2):
                keep_path = folder / "keep-{}.mp4".format(number)
                delete_path = folder / "delete-{}.mp4".format(number)
                keep_path.write_bytes(b"keep")
                delete_path.write_bytes(b"delete")
                keep_stat = keep_path.stat()
                delete_stat = delete_path.stat()
                keep = FileRecord(
                    keep_path, folder, keep_stat.st_size, keep_stat.st_mtime,
                    normalize_stem(keep_path.stem), action="保留",
                )
                delete = FileRecord(
                    delete_path, folder, delete_stat.st_size, delete_stat.st_mtime,
                    normalize_stem(delete_path.stem), action="删除",
                )
                groups.append(DuplicateGroup(
                    "g{}".format(number), [keep, delete], 1.0, "test",
                    display_name_override="作品 {}".format(number),
                ))
                keep_path.unlink()

            ready, ignored, conflicts = analyze_deletion_plan(groups)
            app = MediaDupFinderApp.__new__(MediaDupFinderApp)
            app.root = None
            with patch("media_dup_finder.app.SurvivorChangedDialog") as dialog:
                dialog.return_value.show.return_value = "continue_all"
                resolved = app._resolve_survivor_conflicts(
                    ready, ignored, conflicts,
                )

            self.assertIsNotNone(resolved)
            resolved_ready, resolved_ignored = resolved
            self.assertEqual(len(resolved_ready), 2)
            self.assertEqual(len(resolved_ignored), 2)
            self.assertTrue(all(item.preserve_action for item in resolved_ignored))
            self.assertEqual(dialog.call_count, 1)


if __name__ == "__main__":
    unittest.main()
