from __future__ import annotations

import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from media_dup_finder.app import MediaDupFinderApp, build_file_information
from media_dup_finder.hashing import HASH_MODE_SMART, HashWorkload
from media_dup_finder.models import DuplicateGroup, FileRecord
from media_dup_finder.normalization import normalize_stem


class HashPromptTests(unittest.TestCase):
    def test_prompt_maps_continue_skip_and_cancel(self) -> None:
        app = object.__new__(MediaDupFinderApp)
        workload = HashWorkload(
            mode=HASH_MODE_SMART,
            size_groups=2,
            candidate_files=4,
            quick_bytes=12 * 1024 * 1024,
            maximum_full_bytes=24 * 1024 * 1024 * 1024,
        )
        for answer, expected in ((True, "continue"), (False, "skip"), (None, "cancel")):
            with self.subTest(answer=answer):
                response = queue.Queue(maxsize=1)
                with patch(
                    "media_dup_finder.app.messagebox.askyesnocancel",
                    return_value=answer,
                ):
                    app._handle_hash_confirmation((workload, response))
                self.assertEqual(response.get_nowait(), expected)

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


if __name__ == "__main__":
    unittest.main()
