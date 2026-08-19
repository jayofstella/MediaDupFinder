"""Small formatting and platform helpers."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def format_bytes(size: int) -> str:
    value = float(max(size, 0))
    units = ("B", "KB", "MB", "GB", "TB", "PB")
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            if unit == "B":
                return "{} {}".format(int(value), unit)
            return "{:.2f} {}".format(value, unit)
        value /= 1024.0
    return "{} B".format(size)


def format_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "未知"
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return "{:02d}:{:02d}:{:02d}".format(hours, minutes, secs)
    return "{:02d}:{:02d}".format(minutes, secs)


def user_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if base:
        return Path(base) / "MediaDupFinder"
    return Path.home() / ".media_dup_finder"

