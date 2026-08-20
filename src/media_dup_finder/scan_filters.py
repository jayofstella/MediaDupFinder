"""Shared scan filters and comparison-scope rules."""

from __future__ import annotations

import math
import os
import re
import unicodedata
from pathlib import Path
from typing import Iterable, Tuple


SCOPE_ALL = "all"
SCOPE_DIFFERENT_FOLDER = "different_folder"
SCOPE_SAME_FOLDER = "same_folder"
SCOPE_DIFFERENT_ROOT = "different_root"
VALID_COMPARISON_SCOPES = frozenset({
    SCOPE_ALL,
    SCOPE_DIFFERENT_FOLDER,
    SCOPE_SAME_FOLDER,
    SCOPE_DIFFERENT_ROOT,
})


def parse_minimum_size_mb(value: object) -> float:
    """Parse a non-negative MB value entered by the user."""

    text = str(value).strip().replace(",", ".")
    if not text:
        return 0.0
    try:
        result = float(text)
    except (TypeError, ValueError):
        raise ValueError("忽略小文件的大小必须是数字，例如 50 或 100.5")
    if not math.isfinite(result) or result < 0:
        raise ValueError("忽略小文件的大小必须是大于或等于 0 的数字")
    if result > 10_000_000:
        raise ValueError("忽略小文件的大小设置过大，请输入不超过 10000000 MB 的数值")
    return result


def parse_maximum_size_mb(value: object) -> float:
    """Parse an optional maximum MB value; zero means unlimited."""

    text = str(value).strip().replace(",", ".")
    if not text:
        return 0.0
    try:
        result = float(text)
    except (TypeError, ValueError):
        raise ValueError("最大文件大小必须是数字，例如 5000 或 10000.5")
    if not math.isfinite(result) or result < 0:
        raise ValueError("最大文件大小必须是大于或等于 0 的数字")
    if result > 10_000_000:
        raise ValueError("最大文件大小设置过大，请输入不超过 10000000 MB 的数值")
    return result


def megabytes_to_bytes(value: float) -> int:
    return max(0, int(float(value) * 1024 * 1024))


def format_megabytes_setting(value: object) -> str:
    try:
        number = parse_minimum_size_mb(value)
    except ValueError:
        return "0"
    if number.is_integer():
        return str(int(number))
    return ("{:.3f}".format(number)).rstrip("0").rstrip(".")


def normalize_exclude_keywords(values: Iterable[str]) -> Tuple[str, ...]:
    """Normalize filename-substring exclusions while preserving their order."""

    result = []
    seen = set()
    for raw in values:
        value = unicodedata.normalize("NFKC", str(raw)).strip().casefold()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return tuple(result)


def normalize_filter_text(value: object) -> str:
    return unicodedata.normalize("NFKC", str(value)).casefold()


def parse_exclude_keywords(text: str) -> Tuple[str, ...]:
    return normalize_exclude_keywords(
        re.split(r"[,;，；\n\r]+", str(text))
    )


def parse_extensions(text: str) -> Tuple[str, ...]:
    """Parse a user extension list into normalized dot-prefixed values."""

    result = []
    seen = set()
    for raw in re.split(r"[,;，；\s]+", str(text)):
        value = raw.strip().casefold()
        while value.startswith("*"):
            value = value[1:]
        value = value.lstrip(".")
        if not value:
            continue
        if not re.fullmatch(r"[a-z0-9]{1,12}", value):
            raise ValueError("扩展名“{}”无效，请输入例如 mp4;mkv;vob".format(raw))
        normalized = "." + value
        if normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return tuple(result)


def normalized_path_identity(path: Path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _folder_identity(record: object) -> str:
    return normalized_path_identity(Path(getattr(record, "path")).parent)


def _root_identity(record: object) -> str:
    return normalized_path_identity(Path(getattr(record, "root")))


def validate_comparison_scope(scope: str) -> str:
    if scope not in VALID_COMPARISON_SCOPES:
        raise ValueError("unsupported comparison scope: {}".format(scope))
    return scope


def comparison_pair_allowed(left: object, right: object, scope: str) -> bool:
    """Return whether two records are allowed to be compared in this scope."""

    validate_comparison_scope(scope)
    if scope == SCOPE_ALL:
        return True
    if scope == SCOPE_SAME_FOLDER:
        return _folder_identity(left) == _folder_identity(right)
    if scope == SCOPE_DIFFERENT_FOLDER:
        return _folder_identity(left) != _folder_identity(right)
    return _root_identity(left) != _root_identity(right)


def scope_partition_key(record: object, scope: str) -> str:
    """Partition exact hashes only when matches must stay in one folder."""

    validate_comparison_scope(scope)
    if scope == SCOPE_SAME_FOLDER:
        return _folder_identity(record)
    return ""


def comparison_group_allowed(records: Iterable[object], scope: str) -> bool:
    items = list(records)
    validate_comparison_scope(scope)
    if len(items) < 2:
        return False
    if scope == SCOPE_ALL:
        return True
    folders = {_folder_identity(record) for record in items}
    if scope == SCOPE_SAME_FOLDER:
        return len(folders) == 1
    if scope == SCOPE_DIFFERENT_FOLDER:
        return len(folders) >= 2
    roots = {_root_identity(record) for record in items}
    return len(roots) >= 2
