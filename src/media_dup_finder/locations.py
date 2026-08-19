"""Helpers for selecting and normalizing multiple scan locations."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List


DRIVE_TYPE_LABELS = {
    0: "未知类型",
    1: "不可用",
    2: "可移动磁盘",
    3: "固定磁盘",
    4: "网络驱动器",
    5: "光盘驱动器",
    6: "RAM 磁盘",
}


@dataclass(frozen=True)
class DriveLocation:
    path: str
    kind: str


def drive_roots_from_mask(mask: int) -> List[str]:
    """Convert the Windows GetLogicalDrives bit mask to drive-root paths."""

    return [
        "{}:\\".format(chr(ord("A") + index))
        for index in range(26)
        if mask & (1 << index)
    ]


def list_drive_locations() -> List[DriveLocation]:
    """Return logical drives without touching their contents.

    Avoiding directory reads here prevents an offline network drive or empty
    optical drive from freezing the selector. Accessibility is checked only
    for locations the user actually confirms.
    """

    if os.name != "nt":
        anchor = Path.cwd().anchor or os.sep
        return [DriveLocation(anchor, "文件系统根目录")]

    import ctypes

    kernel32 = ctypes.windll.kernel32
    mask = int(kernel32.GetLogicalDrives())
    get_drive_type = kernel32.GetDriveTypeW
    get_drive_type.argtypes = [ctypes.c_wchar_p]
    get_drive_type.restype = ctypes.c_uint
    return [
        DriveLocation(root, DRIVE_TYPE_LABELS.get(int(get_drive_type(root)), "未知类型"))
        for root in drive_roots_from_mask(mask)
    ]


def parse_pasted_directory_paths(text: str) -> List[str]:
    """Parse one path per line, including Explorer's quoted Copy-as-path form."""

    result: List[str] = []
    for line in text.splitlines():
        value = line.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1].strip()
        if value:
            result.append(value)
    return result


def directory_identity(value: str) -> str:
    expanded = os.path.expandvars(os.path.expanduser(str(value).strip()))
    return os.path.normcase(os.path.abspath(os.path.normpath(expanded)))


def merge_unique_directory_paths(
    candidates: Iterable[str],
    existing: Iterable[str] = (),
) -> List[str]:
    """Normalize and return only new paths, preserving candidate order."""

    seen = {directory_identity(value) for value in existing if str(value).strip()}
    result: List[str] = []
    for candidate in candidates:
        value = str(candidate).strip()
        if not value:
            continue
        expanded = os.path.expandvars(os.path.expanduser(value))
        normalized = os.path.abspath(os.path.normpath(expanded))
        identity = os.path.normcase(normalized)
        if identity in seen:
            continue
        seen.add(identity)
        result.append(normalized)
    return result
