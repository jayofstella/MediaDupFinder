from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from media_dup_finder.hashing import (
    HASH_MODE_DEEP,
    HASH_MODE_SMART,
    estimate_hash_workload,
    find_exact_duplicate_groups,
    merge_duplicate_groups,
)
from media_dup_finder.matching import group_similar_files
from media_dup_finder.models import FileRecord
from media_dup_finder.normalization import normalize_stem


def record(path: Path) -> FileRecord:
    stat = path.stat()
    return FileRecord(path, path.parent, stat.st_size, stat.st_mtime, normalize_stem(path.stem))


class HashingTests(unittest.TestCase):
    def test_identical_bytes_with_unrelated_names_are_found(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "unrelated-name.mp4"
            second = root / "完全不同.mkv"
            first.write_bytes(b"identical content")
            second.write_bytes(b"identical content")
            groups, warnings, cancelled, stats = find_exact_duplicate_groups(
                [record(first), record(second)], use_cache=False,
            )
            self.assertFalse(cancelled)
            self.assertEqual(warnings, [])
            self.assertEqual(len(groups), 1)
            self.assertEqual(groups[0].match_kind, "hash")
            self.assertEqual(len({item.content_md5 for item in groups[0].files}), 1)
            self.assertEqual(stats.md5_calculated_files, 2)

    def test_same_size_different_content_is_not_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first.bin"
            second = root / "second.bin"
            first.write_bytes(b"AAAA")
            second.write_bytes(b"BBBB")
            groups, _, _, _ = find_exact_duplicate_groups(
                [record(first), record(second)], use_cache=False,
            )
            self.assertEqual(groups, [])

    def test_unique_sizes_are_not_hashed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "one.mp4"
            second = root / "two.mp4"
            first.write_bytes(b"1")
            second.write_bytes(b"22")
            records = [record(first), record(second)]
            find_exact_duplicate_groups(records, use_cache=False)
            self.assertTrue(all(item.hash_source == "未计算" for item in records))

    def test_pre_cancelled_hash_scan_stops(self) -> None:
        event = threading.Event()
        event.set()
        groups, _, cancelled, _ = find_exact_duplicate_groups(
            [], cancel_event=event, use_cache=False,
        )
        self.assertEqual(groups, [])
        self.assertTrue(cancelled)

    def test_overlapping_name_and_hash_groups_are_merged_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "寒战.mp4"
            second = root / "寒战1.mkv"
            third = root / "different-name.mov"
            first.write_bytes(b"same")
            second.write_bytes(b"different-size")
            third.write_bytes(b"same")
            records = [record(first), record(second), record(third)]
            name_groups = group_similar_files(records)
            hash_groups, _, _, _ = find_exact_duplicate_groups(records, use_cache=False)
            groups = merge_duplicate_groups(name_groups, hash_groups)
            self.assertEqual(len(groups), 1)
            self.assertEqual(groups[0].match_kind, "mixed")
            self.assertEqual(len(groups[0].files), 3)
            self.assertEqual(len({item.file_id for item in groups[0].files}), 3)

    def test_smart_mode_skips_full_read_when_large_samples_differ(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            size = 4 * 1024 * 1024 + 17
            first = root / "first.bin"
            second = root / "second.bin"
            first.write_bytes(b"A" * size)
            second.write_bytes(b"B" * size)
            records = [record(first), record(second)]
            progress_states = []
            groups, warnings, cancelled, stats = find_exact_duplicate_groups(
                records,
                mode=HASH_MODE_SMART,
                use_cache=False,
                progress=progress_states.append,
            )
            self.assertFalse(cancelled)
            self.assertEqual(warnings, [])
            self.assertEqual(groups, [])
            self.assertEqual(stats.full_bytes_read, 0)
            self.assertTrue(all(item.content_md5 is None for item in records))
            self.assertTrue(progress_states)
            self.assertEqual(progress_states[-1].phase, "快速指纹")
            self.assertEqual(progress_states[-1].percent, 100.0)

    def test_matching_samples_still_require_full_md5(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            size = 4 * 1024 * 1024 + 17
            first_payload = bytearray(b"A" * size)
            second_payload = bytearray(first_payload)
            # This position is outside the three sampled 1 MB regions.
            second_payload[1_300_000] = ord("B")
            first = root / "first.bin"
            second = root / "second.bin"
            first.write_bytes(first_payload)
            second.write_bytes(second_payload)
            groups, warnings, cancelled, stats = find_exact_duplicate_groups(
                [record(first), record(second)],
                mode=HASH_MODE_SMART,
                use_cache=False,
            )
            self.assertFalse(cancelled)
            self.assertEqual(warnings, [])
            self.assertEqual(groups, [])
            self.assertEqual(stats.full_bytes_read, size * 2)

    def test_deep_mode_hashes_all_same_size_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            size = 4 * 1024 * 1024 + 17
            first = root / "first.bin"
            second = root / "second.bin"
            first.write_bytes(b"A" * size)
            second.write_bytes(b"B" * size)
            records = [record(first), record(second)]
            groups, warnings, cancelled, stats = find_exact_duplicate_groups(
                records, mode=HASH_MODE_DEEP, use_cache=False,
            )
            self.assertFalse(cancelled)
            self.assertEqual(warnings, [])
            self.assertEqual(groups, [])
            self.assertEqual(stats.full_bytes_read, size * 2)
            self.assertTrue(all(item.content_md5 for item in records))

    def test_validated_cache_avoids_repeating_full_read(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            size = 4 * 1024 * 1024 + 17
            first = root / "first.bin"
            second = root / "second.bin"
            payload = b"same" * (size // 4) + b"x" * (size % 4)
            first.write_bytes(payload)
            second.write_bytes(payload)
            cache_path = root / "hash-cache.json"

            first_groups, _, _, first_stats = find_exact_duplicate_groups(
                [record(first), record(second)],
                mode=HASH_MODE_SMART,
                cache_path=cache_path,
            )
            second_groups, _, _, second_stats = find_exact_duplicate_groups(
                [record(first), record(second)],
                mode=HASH_MODE_SMART,
                cache_path=cache_path,
            )
            self.assertEqual(len(first_groups), 1)
            self.assertEqual(len(second_groups), 1)
            self.assertEqual(first_stats.full_bytes_read, size * 2)
            self.assertEqual(second_stats.full_bytes_read, 0)
            self.assertEqual(second_stats.cache_hits, 2)

    def test_workload_ignores_unique_sizes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = [root / "a.bin", root / "b.bin", root / "c.bin"]
            paths[0].write_bytes(b"A" * 10)
            paths[1].write_bytes(b"B" * 10)
            paths[2].write_bytes(b"C" * 11)
            workload = estimate_hash_workload([record(path) for path in paths], HASH_MODE_SMART)
            self.assertEqual(workload.size_groups, 1)
            self.assertEqual(workload.candidate_files, 2)
            self.assertEqual(workload.maximum_full_bytes, 20)


if __name__ == "__main__":
    unittest.main()
