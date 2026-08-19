from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from media_dup_finder.locations import (
    drive_roots_from_mask,
    merge_unique_directory_paths,
    parse_pasted_directory_paths,
)


class LocationTests(unittest.TestCase):
    def test_drive_mask_supports_multiple_drive_letters(self) -> None:
        mask = (1 << 2) | (1 << 3) | (1 << 25)
        self.assertEqual(drive_roots_from_mask(mask), ["C:\\", "D:\\", "Z:\\"])

    def test_explorer_quoted_paths_are_parsed_one_per_line(self) -> None:
        text = '"C:\\Movies"\n\n"D:\\Video Library"\nE:\\Archive\n'
        self.assertEqual(
            parse_pasted_directory_paths(text),
            ["C:\\Movies", "D:\\Video Library", "E:\\Archive"],
        )

    def test_paths_are_normalized_and_deduplicated_against_existing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first"
            second = root / "second"
            first.mkdir()
            second.mkdir()
            result = merge_unique_directory_paths(
                [str(first), str(first / "."), str(second), str(second)],
                existing=[str(first)],
            )
            self.assertEqual(len(result), 1)
            # Windows may expose the same temporary directory through an 8.3
            # short path (RUNNER~1) while Path.resolve() expands it to the long
            # name (runneradmin). Compare the actual directory identity instead
            # of requiring those two valid spellings to be textually identical.
            self.assertTrue(Path(result[0]).samefile(second))


if __name__ == "__main__":
    unittest.main()
