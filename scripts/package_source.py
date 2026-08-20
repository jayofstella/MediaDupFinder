"""Create the complete source ZIP used for GitHub handoff."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_PARTS = frozenset({
    ".git", ".venv", ".venv-build", "__pycache__", ".pytest_cache",
    ".mypy_cache", "build", "dist", "release",
})
EXCLUDED_NAMES = frozenset({
    ".DS_Store", "Thumbs.db", "settings.json", "operation_history.jsonl",
})


def read_version() -> str:
    content = (
        PROJECT_ROOT / "src" / "media_dup_finder" / "__init__.py"
    ).read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', content, flags=re.MULTILINE)
    if not match:
        raise RuntimeError("Unable to read application version")
    return match.group(1)


def included_files() -> list:
    result = []
    for path in PROJECT_ROOT.rglob("*"):
        relative = path.relative_to(PROJECT_ROOT)
        if path.is_dir():
            continue
        if any(part in EXCLUDED_PARTS for part in relative.parts):
            continue
        if path.name in EXCLUDED_NAMES or path.suffix.casefold() in {".pyc", ".log"}:
            continue
        result.append(path)
    return sorted(result, key=lambda item: str(item.relative_to(PROJECT_ROOT)).casefold())


def main() -> None:
    version = read_version()
    output = PROJECT_ROOT.parent / "MediaDupFinder-v{}-Source.zip".format(version)
    with zipfile.ZipFile(
        output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9,
    ) as archive:
        for path in included_files():
            relative = path.relative_to(PROJECT_ROOT)
            archive.write(path, str(Path("MediaDupFinder") / relative))
    print(output)


if __name__ == "__main__":
    main()
