"""Fast, read-only directory traversal."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence, Set

from .models import FileRecord, VIDEO_EXTENSIONS
from .normalization import normalize_stem


ProgressCallback = Callable[[int, str], None]


def _identity(path: Path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def scan_directories(
    roots: Sequence[Path],
    recursive: bool = True,
    extensions: Optional[Iterable[str]] = None,
    cancel_event: Optional[threading.Event] = None,
    progress: Optional[ProgressCallback] = None,
) -> tuple[List[FileRecord], List[str], bool]:
    """Scan roots without following symlinks or modifying any file."""

    source_extensions = VIDEO_EXTENSIONS if extensions is None else extensions
    normalized_extensions = {
        item.casefold() if item.startswith(".") else "." + item.casefold()
        for item in source_extensions
    }
    # An explicitly empty collection means "all regular files".
    allowed: Optional[Set[str]] = normalized_extensions or None
    found: List[FileRecord] = []
    warnings: List[str] = []
    seen_files: Set[str] = set()
    seen_dirs: Set[str] = set()
    cancelled = False

    for raw_root in roots:
        root = Path(raw_root).expanduser()
        if not root.exists() or not root.is_dir():
            warnings.append("目录不存在或无法访问：{}".format(root))
            continue

        stack = [root]
        while stack:
            if cancel_event and cancel_event.is_set():
                cancelled = True
                break

            current = stack.pop()
            dir_key = _identity(current)
            if dir_key in seen_dirs:
                continue
            seen_dirs.add(dir_key)

            try:
                entries = list(os.scandir(str(current)))
            except (OSError, PermissionError) as exc:
                warnings.append("无法读取目录 {}：{}".format(current, exc))
                continue

            for entry in entries:
                if cancel_event and cancel_event.is_set():
                    cancelled = True
                    break
                try:
                    entry_stat = entry.stat(follow_symlinks=False)
                    # Windows junctions and other reparse points can form
                    # cycles. Never traverse or process them.
                    if getattr(entry_stat, "st_file_attributes", 0) & 0x0400:
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if recursive and not entry.name.startswith(".MediaDupFinder_"):
                            stack.append(Path(entry.path))
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue

                    path = Path(entry.path)
                    if allowed is not None and path.suffix.casefold() not in allowed:
                        continue
                    file_key = _identity(path)
                    if file_key in seen_files:
                        continue
                    seen_files.add(file_key)
                    found.append(FileRecord(
                        path=path,
                        root=root,
                        size=entry_stat.st_size,
                        modified_time=entry_stat.st_mtime,
                        name_info=normalize_stem(path.stem),
                    ))
                    if progress and (len(found) == 1 or len(found) % 100 == 0):
                        progress(len(found), str(current))
                except (OSError, PermissionError) as exc:
                    warnings.append("跳过文件 {}：{}".format(entry.path, exc))

            if cancelled:
                break
        if cancelled:
            break

    return found, warnings, cancelled
