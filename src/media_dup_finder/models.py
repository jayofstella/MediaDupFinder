"""Domain models shared by the scanner, matcher and user interface."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from .normalization import NormalizedName


VIDEO_EXTENSIONS = frozenset({
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".rm", ".rmvb",
    ".m4v", ".webm", ".mpg", ".mpeg", ".ts", ".m2ts", ".vob", ".3gp",
})


def stable_file_id(path: Path) -> str:
    normalized = str(path.resolve()).casefold().encode("utf-8", errors="replace")
    return hashlib.sha1(normalized).hexdigest()[:16]


@dataclass
class FileRecord:
    path: Path
    root: Path
    size: int
    modified_time: float
    name_info: NormalizedName
    width: Optional[int] = None
    height: Optional[int] = None
    duration_seconds: Optional[float] = None
    codec: Optional[str] = None
    metadata_source: str = "未读取"
    content_md5: Optional[str] = None
    hash_source: str = "未计算"
    action: str = "未决定"
    file_id: str = field(init=False)

    def __post_init__(self) -> None:
        self.file_id = stable_file_id(self.path)

    @property
    def extension(self) -> str:
        return self.path.suffix.casefold()

    @property
    def resolution(self) -> str:
        if self.width and self.height:
            return "{}×{}".format(self.width, self.height)
        guessed = self.guessed_height
        if guessed:
            return "约 {}P（文件名）".format(guessed)
        return "未知"

    @property
    def guessed_height(self) -> Optional[int]:
        import re

        name = self.path.stem.casefold()
        if "8k" in name:
            return 4320
        if "4k" in name or "2160p" in name:
            return 2160
        for value in (1440, 1080, 720, 576, 480, 360):
            if re.search(r"(?<!\d){}[pi]?(?!\d)".format(value), name):
                return value
        return None

    @property
    def quality_rank(self) -> Tuple[int, int, int, int]:
        """A deterministic suggestion rank; users retain final control."""

        height = self.height or self.guessed_height or 0
        width = self.width or (height * 16 // 9 if height else 0)
        pixels = width * height
        extension_rank = {
            ".mkv": 10, ".mp4": 9, ".mov": 8, ".m4v": 8, ".webm": 7,
            ".avi": 6, ".m2ts": 6, ".ts": 5, ".wmv": 4, ".flv": 3,
            ".rmvb": 2, ".rm": 1,
        }.get(self.extension, 0)
        metadata_certainty = 1 if self.height else 0
        return pixels, metadata_certainty, extension_rank, self.size


@dataclass
class DuplicateGroup:
    group_id: str
    files: List[FileRecord]
    confidence: float
    reason: str
    match_kind: str = "name"
    metadata_note: str = ""
    safety_warning: bool = False

    @property
    def total_size(self) -> int:
        return sum(item.size for item in self.files)

    @property
    def estimated_savings(self) -> int:
        if len(self.files) < 2:
            return 0
        keep = max(self.files, key=lambda item: item.quality_rank)
        return self.total_size - keep.size

    @property
    def duration_span_seconds(self) -> Optional[float]:
        durations = [
            item.duration_seconds for item in self.files
            if item.duration_seconds is not None and item.duration_seconds > 0
        ]
        if len(durations) < 2:
            return None
        return max(durations) - min(durations)

    @property
    def display_name(self) -> str:
        for item in self.files:
            if item.name_info.catalog_key:
                return item.name_info.catalog_key.upper()
        shortest = min(self.files, key=lambda item: len(item.name_info.cleaned_display))
        return shortest.name_info.cleaned_display

    @property
    def match_label(self) -> str:
        return {"hash": "MD5", "mixed": "混合", "name": "作品身份"}.get(
            self.match_kind, "作品身份"
        )


@dataclass
class ScanStatistics:
    regular_files_seen: int = 0
    included_files: int = 0
    skipped_extension: int = 0
    skipped_too_small: int = 0
    skipped_too_large: int = 0
    skipped_hidden_system_files: int = 0
    skipped_hidden_system_directories: int = 0
    skipped_excluded_directories: int = 0
    skipped_incomplete: int = 0
    skipped_keyword: int = 0

    @property
    def filtered_files(self) -> int:
        return (
            self.skipped_extension
            + self.skipped_too_small
            + self.skipped_too_large
            + self.skipped_hidden_system_files
            + self.skipped_incomplete
            + self.skipped_keyword
        )


@dataclass
class ScanResult:
    files: List[FileRecord]
    groups: List[DuplicateGroup]
    warnings: List[str]
    cancelled: bool = False
    hash_mode: str = "off"
    hash_candidate_files: int = 0
    hash_bytes_read: int = 0
    hash_cache_hits: int = 0
    scan_statistics: ScanStatistics = field(default_factory=ScanStatistics)
    comparison_scope: str = "all"
    name_matching_enabled: bool = True
