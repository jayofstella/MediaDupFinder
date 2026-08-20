"""Fast, read-only directory traversal."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence, Set

from .models import FileRecord, ScanStatistics, VIDEO_EXTENSIONS
from .normalization import normalize_path
from .scan_filters import normalize_exclude_keywords, normalize_filter_text


ProgressCallback = Callable[[int, str], None]

INCOMPLETE_SUFFIXES = (
    ".part", ".partial", ".tmp", ".temp", ".crdownload", ".download", ".!qb",
)
FILE_ATTRIBUTE_HIDDEN = 0x0002
FILE_ATTRIBUTE_SYSTEM = 0x0004
FILE_ATTRIBUTE_REPARSE_POINT = 0x0400


def _identity(path: Path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _is_hidden_or_system(name: str, attributes: int) -> bool:
    return name.startswith(".") or bool(
        attributes & (FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM)
    )


def _is_incomplete_name(name: str) -> bool:
    folded = name.casefold()
    return folded.startswith("~$") or folded.endswith(INCOMPLETE_SUFFIXES)


def _same_or_child(path_key: str, excluded_key: str) -> bool:
    try:
        return os.path.commonpath((path_key, excluded_key)) == excluded_key
    except ValueError:
        # Different Windows drives have no common path.
        return False


def scan_directories(
    roots: Sequence[Path],
    recursive: bool = True,
    extensions: Optional[Iterable[str]] = None,
    min_size_bytes: int = 0,
    max_size_bytes: int = 0,
    skip_hidden_system: bool = True,
    skip_incomplete: bool = True,
    exclude_name_keywords: Iterable[str] = (),
    excluded_directories: Iterable[Path] = (),
    statistics: Optional[ScanStatistics] = None,
    cancel_event: Optional[threading.Event] = None,
    progress: Optional[ProgressCallback] = None,
) -> tuple[List[FileRecord], List[str], bool]:
    """Scan roots without following symlinks or modifying any file."""

    if min_size_bytes < 0:
        raise ValueError("min_size_bytes must not be negative")
    if max_size_bytes < 0:
        raise ValueError("max_size_bytes must not be negative")
    if max_size_bytes and max_size_bytes < min_size_bytes:
        raise ValueError("max_size_bytes must be zero or at least min_size_bytes")
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
    stats = statistics if statistics is not None else ScanStatistics()
    keywords = normalize_exclude_keywords(exclude_name_keywords)
    excluded_keys = tuple(
        dict.fromkeys(_identity(Path(value).expanduser()) for value in excluded_directories)
    )

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
            if any(_same_or_child(dir_key, excluded) for excluded in excluded_keys):
                stats.skipped_excluded_directories += 1
                continue

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
                    attributes = int(getattr(entry_stat, "st_file_attributes", 0))
                    # Windows junctions and other reparse points can form
                    # cycles. Never traverse or process them.
                    if attributes & FILE_ATTRIBUTE_REPARSE_POINT:
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name.startswith(".MediaDupFinder_"):
                            continue
                        if skip_hidden_system and _is_hidden_or_system(entry.name, attributes):
                            stats.skipped_hidden_system_directories += 1
                            continue
                        if recursive:
                            stack.append(Path(entry.path))
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue

                    stats.regular_files_seen += 1
                    if skip_hidden_system and _is_hidden_or_system(entry.name, attributes):
                        stats.skipped_hidden_system_files += 1
                        continue

                    path = Path(entry.path)
                    if allowed is not None and path.suffix.casefold() not in allowed:
                        stats.skipped_extension += 1
                        continue
                    if skip_incomplete and _is_incomplete_name(entry.name):
                        stats.skipped_incomplete += 1
                        continue
                    folded_name = normalize_filter_text(entry.name)
                    if keywords and any(keyword in folded_name for keyword in keywords):
                        stats.skipped_keyword += 1
                        continue
                    if entry_stat.st_size < min_size_bytes:
                        stats.skipped_too_small += 1
                        continue
                    if max_size_bytes and entry_stat.st_size > max_size_bytes:
                        stats.skipped_too_large += 1
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
                        name_info=normalize_path(path, root),
                    ))
                    stats.included_files += 1
                    if progress and (len(found) == 1 or len(found) % 100 == 0):
                        progress(len(found), str(current))
                except (OSError, PermissionError) as exc:
                    warnings.append("跳过文件 {}：{}".format(entry.path, exc))

            if cancelled:
                break
        if cancelled:
            break

    return found, warnings, cancelled
