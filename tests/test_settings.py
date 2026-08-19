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


if __name__ == "__main__":
    unittest.main()
