from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from media_dup_finder.settings import load_settings, save_settings


class SettingsTests(unittest.TestCase):
    def test_v11_hash_checkbox_is_migrated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "settings.json"
            path.write_text('{"detect_hash": false}', encoding="utf-8")
            self.assertEqual(load_settings(path)["hash_mode"], "off")
            path.write_text('{"detect_hash": true}', encoding="utf-8")
            self.assertEqual(load_settings(path)["hash_mode"], "smart")

    def test_hash_mode_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "settings.json"
            save_settings({"hash_mode": "deep"}, path)
            self.assertEqual(load_settings(path)["hash_mode"], "deep")

    def test_scan_filter_options_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "settings.json"
            save_settings({
                "min_size_mb": "125.5",
                "max_size_mb": "9000",
                "comparison_scope": "different_folder",
                "name_matching_enabled": False,
                "skip_hidden_system": False,
                "skip_incomplete": False,
                "exclude_name_keywords": "sample;预告",
                "custom_extensions": "mp4;mkv;vob",
                "excluded_directories": ["D:/cache", "E:/temp"],
                "detail_column_order": ["name", "size", "folder"],
                "file_mode": "全部文件",
            }, path)
            loaded = load_settings(path)
            self.assertEqual(loaded["min_size_mb"], "125.5")
            self.assertEqual(loaded["max_size_mb"], "9000")
            self.assertEqual(loaded["comparison_scope"], "different_folder")
            self.assertFalse(loaded["name_matching_enabled"])
            self.assertFalse(loaded["skip_hidden_system"])
            self.assertFalse(loaded["skip_incomplete"])
            self.assertEqual(loaded["exclude_name_keywords"], "sample;预告")
            self.assertEqual(loaded["custom_extensions"], "mp4;mkv;vob")
            self.assertEqual(loaded["excluded_directories"], ["D:/cache", "E:/temp"])
            self.assertEqual(loaded["detail_column_order"], ["name", "size", "folder"])
            self.assertEqual(loaded["file_mode"], "全部文件")


if __name__ == "__main__":
    unittest.main()
