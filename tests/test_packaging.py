from __future__ import annotations

import runpy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class PackagingEntrypointTests(unittest.TestCase):
    def test_package_main_can_run_without_package_context(self) -> None:
        entrypoint = PROJECT_ROOT / "src" / "media_dup_finder" / "__main__.py"
        with patch("media_dup_finder.app.main") as mocked_main:
            runpy.run_path(str(entrypoint), run_name="__main__")
        mocked_main.assert_called_once_with()

    def test_source_launcher_calls_application_main(self) -> None:
        entrypoint = PROJECT_ROOT / "MediaDupFinder.pyw"
        with patch("media_dup_finder.app.main") as mocked_main:
            runpy.run_path(str(entrypoint), run_name="__main__")
        mocked_main.assert_called_once_with()

    def test_source_launcher_startup_check_skips_gui(self) -> None:
        entrypoint = PROJECT_ROOT / "MediaDupFinder.pyw"
        with patch("media_dup_finder.app.main") as mocked_main:
            with patch.object(sys, "argv", [str(entrypoint), "--startup-check"]):
                runpy.run_path(str(entrypoint), run_name="__main__")
        mocked_main.assert_not_called()

    def test_pyinstaller_uses_top_level_launcher(self) -> None:
        specification = (PROJECT_ROOT / "MediaDupFinder.spec").read_text(encoding="utf-8")
        self.assertIn('project_root / "MediaDupFinder.pyw"', specification)
        self.assertNotIn('media_dup_finder" / "__main__.py"', specification)


if __name__ == "__main__":
    unittest.main()
