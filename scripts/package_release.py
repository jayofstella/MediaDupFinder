"""Create a deterministic, user-facing ZIP after a Windows build."""

from __future__ import annotations

import argparse
import re
import zipfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def read_version() -> str:
    content = (PROJECT_ROOT / "src" / "media_dup_finder" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', content, flags=re.MULTILINE)
    if not match:
        raise RuntimeError("Unable to read application version")
    return match.group(1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--arch", choices=("x64", "x86"), required=True)
    args = parser.parse_args()

    executable = PROJECT_ROOT / "dist" / "MediaDupFinder.exe"
    if not executable.is_file():
        raise FileNotFoundError("Build output not found: {}".format(executable))

    version = read_version()
    release_dir = PROJECT_ROOT / "release"
    release_dir.mkdir(parents=True, exist_ok=True)
    archive = release_dir / "MediaDupFinder-v{}-Windows-{}.zip".format(version, args.arch)
    prefix = "MediaDupFinder-v{}".format(version)
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as output:
        output.write(executable, "{}/MediaDupFinder.exe".format(prefix))
        output.write(PROJECT_ROOT / "README.md", "{}/README.md".format(prefix))
        output.write(PROJECT_ROOT / "LICENSE", "{}/LICENSE".format(prefix))
        output.write(PROJECT_ROOT / "tools" / "README.md", "{}/ffprobe-可选说明.md".format(prefix))
    print(archive)


if __name__ == "__main__":
    main()

