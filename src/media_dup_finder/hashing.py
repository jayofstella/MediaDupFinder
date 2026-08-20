"""Adaptive exact-duplicate detection with quick sampling and MD5 cache."""

from __future__ import annotations

import hashlib
import os
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, DefaultDict, Dict, List, Optional, Sequence, Set, Tuple

from .hash_cache import HashCache
from .matching import make_group_id
from .models import DuplicateGroup, FileRecord
from .scan_filters import (
    SCOPE_ALL,
    comparison_group_allowed,
    scope_partition_key,
    validate_comparison_scope,
)


HASH_MODE_OFF = "off"
HASH_MODE_SMART = "smart"
HASH_MODE_DEEP = "deep"
VALID_HASH_MODES = frozenset({HASH_MODE_OFF, HASH_MODE_SMART, HASH_MODE_DEEP})
SAMPLE_CHUNK_SIZE = 1024 * 1024
FULL_HASH_CHUNK_SIZE = 4 * 1024 * 1024


@dataclass(frozen=True)
class HashWorkload:
    mode: str
    size_groups: int
    candidate_files: int
    quick_bytes: int
    maximum_full_bytes: int


@dataclass(frozen=True)
class HashProgressState:
    phase: str
    processed_bytes: int
    total_bytes: int
    current_name: str
    elapsed_seconds: float
    eta_seconds: Optional[float]

    @property
    def percent(self) -> float:
        if self.total_bytes <= 0:
            return 100.0
        return min(100.0, self.processed_bytes * 100.0 / self.total_bytes)


@dataclass
class HashScanStats:
    mode: str
    candidate_files: int = 0
    quick_bytes_read: int = 0
    full_bytes_read: int = 0
    md5_calculated_files: int = 0
    cache_hits: int = 0

    @property
    def total_bytes_read(self) -> int:
        return self.quick_bytes_read + self.full_bytes_read


HashProgress = Callable[[HashProgressState], None]


class _Cancelled(Exception):
    pass


class _ProgressMeter:
    def __init__(self, phase: str, total: int, callback: Optional[HashProgress]) -> None:
        self.phase = phase
        self.total = max(0, int(total))
        self.callback = callback
        self.processed = 0
        self.started = time.monotonic()
        self.last_emit = 0.0

    def advance(self, amount: int, name: str, force: bool = False) -> None:
        self.processed += max(0, int(amount))
        if not self.callback:
            return
        now = time.monotonic()
        if not force and now - self.last_emit < 0.25 and self.processed < self.total:
            return
        elapsed = max(0.001, now - self.started)
        speed = self.processed / elapsed
        remaining = max(0, self.total - self.processed)
        eta = remaining / speed if speed > 0 else None
        self.callback(HashProgressState(
            phase=self.phase,
            processed_bytes=self.processed,
            total_bytes=self.total,
            current_name=name,
            elapsed_seconds=elapsed,
            eta_seconds=eta,
        ))
        self.last_emit = now


def _candidate_size_groups(
    files: Sequence[FileRecord],
    comparison_scope: str = SCOPE_ALL,
) -> List[Tuple[int, List[FileRecord]]]:
    validate_comparison_scope(comparison_scope)
    by_size: DefaultDict[Tuple[int, str], List[FileRecord]] = defaultdict(list)
    for record in files:
        partition = scope_partition_key(record, comparison_scope)
        by_size[(record.size, partition)].append(record)
    return [
        (key[0], sorted(records, key=lambda item: str(item.path).casefold()))
        for key, records in sorted(by_size.items())
        if comparison_group_allowed(records, comparison_scope)
    ]


def _sample_spans(size: int) -> Tuple[Tuple[int, int], ...]:
    size = max(0, int(size))
    if size <= SAMPLE_CHUNK_SIZE * 3:
        return ((0, size),)
    positions = (0, (size - SAMPLE_CHUNK_SIZE) // 2, size - SAMPLE_CHUNK_SIZE)
    return tuple((position, SAMPLE_CHUNK_SIZE) for position in sorted(set(positions)))


def _sample_bytes(size: int) -> int:
    return sum(length for _, length in _sample_spans(size))


def estimate_hash_workload(
    files: Sequence[FileRecord],
    mode: str,
    comparison_scope: str = SCOPE_ALL,
) -> HashWorkload:
    """Estimate the bytes touched before any file content is opened."""

    if mode not in VALID_HASH_MODES:
        raise ValueError("unsupported hash mode: {}".format(mode))
    validate_comparison_scope(comparison_scope)
    if mode == HASH_MODE_OFF:
        return HashWorkload(mode, 0, 0, 0, 0)
    groups = _candidate_size_groups(files, comparison_scope)
    candidates = [record for _, records in groups for record in records]
    return HashWorkload(
        mode=mode,
        size_groups=len(groups),
        candidate_files=len(candidates),
        quick_bytes=sum(_sample_bytes(record.size) for record in candidates),
        maximum_full_bytes=sum(record.size for record in candidates),
    )


def _same_file_state(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_size == right.st_size
        and getattr(left, "st_mtime_ns", None) == getattr(right, "st_mtime_ns", None)
    )


def _check_cancel(cancel_event: Optional[threading.Event]) -> None:
    if cancel_event and cancel_event.is_set():
        raise _Cancelled()


def _quick_fingerprint(
    path: Path,
    size: int,
    meter: _ProgressMeter,
    cancel_event: Optional[threading.Event],
) -> Tuple[str, Optional[str], os.stat_result]:
    """Read start/middle/end; small files receive a full MD5 in the same pass."""

    before = path.stat()
    if before.st_size != size:
        raise OSError("文件大小在目录扫描后发生变化")
    spans = _sample_spans(size)
    covers_whole_file = len(spans) == 1 and spans[0] == (0, size)
    quick = hashlib.blake2b(digest_size=16)
    quick.update(size.to_bytes(8, byteorder="little", signed=False))
    complete_md5 = hashlib.md5() if covers_whole_file else None

    with path.open("rb") as handle:
        for offset, length in spans:
            _check_cancel(cancel_event)
            quick.update(offset.to_bytes(8, byteorder="little", signed=False))
            handle.seek(offset)
            remaining = length
            while remaining:
                _check_cancel(cancel_event)
                block = handle.read(min(SAMPLE_CHUNK_SIZE, remaining))
                if not block:
                    raise OSError("读取快速指纹时文件提前结束")
                quick.update(block)
                if complete_md5 is not None:
                    complete_md5.update(block)
                remaining -= len(block)
                meter.advance(len(block), path.name)

    after = path.stat()
    if not _same_file_state(before, after):
        raise OSError("文件在读取快速指纹时发生变化")
    return quick.hexdigest(), complete_md5.hexdigest() if complete_md5 else None, after


def _full_md5(
    path: Path,
    expected: os.stat_result,
    meter: _ProgressMeter,
    cancel_event: Optional[threading.Event],
) -> Tuple[str, os.stat_result]:
    before = path.stat()
    if not _same_file_state(expected, before):
        raise OSError("文件在快速指纹后发生变化")
    digest = hashlib.md5()
    with path.open("rb") as handle:
        while True:
            _check_cancel(cancel_event)
            block = handle.read(FULL_HASH_CHUNK_SIZE)
            if not block:
                break
            digest.update(block)
            meter.advance(len(block), path.name)
    after = path.stat()
    if not _same_file_state(before, after):
        raise OSError("文件在计算完整 MD5 时发生变化")
    return digest.hexdigest(), after


def hash_file(path: Path, cancel_event: Optional[threading.Event] = None) -> Optional[str]:
    """Compatibility helper: read one complete file and return MD5."""

    try:
        stat = path.stat()
        meter = _ProgressMeter("完整 MD5", stat.st_size, None)
        digest, _ = _full_md5(path, stat, meter, cancel_event)
        return digest
    except _Cancelled:
        return None


def _make_hash_groups(
    by_hash: DefaultDict[Tuple[int, str, str], List[FileRecord]],
    comparison_scope: str,
) -> List[DuplicateGroup]:
    groups: List[DuplicateGroup] = []
    for records in by_hash.values():
        if not comparison_group_allowed(records, comparison_scope):
            continue
        records.sort(key=lambda item: item.quality_rank, reverse=True)
        groups.append(DuplicateGroup(
            group_id=make_group_id(records),
            files=records,
            confidence=1.0,
            reason="文件大小与完整 MD5 均相同",
            match_kind="hash",
        ))
    groups.sort(key=lambda group: group.estimated_savings, reverse=True)
    return groups


def find_exact_duplicate_groups(
    files: Sequence[FileRecord],
    mode: str = HASH_MODE_SMART,
    comparison_scope: str = SCOPE_ALL,
    cancel_event: Optional[threading.Event] = None,
    progress: Optional[HashProgress] = None,
    cache_path: Optional[Path] = None,
    use_cache: bool = True,
) -> Tuple[List[DuplicateGroup], List[str], bool, HashScanStats]:
    """Find exact copies using size, quick sampling, then complete MD5."""

    if mode not in VALID_HASH_MODES:
        raise ValueError("unsupported hash mode: {}".format(mode))
    validate_comparison_scope(comparison_scope)
    workload = estimate_hash_workload(files, mode, comparison_scope)
    stats = HashScanStats(mode=mode, candidate_files=workload.candidate_files)
    if mode == HASH_MODE_OFF:
        return [], [], False, stats
    if cancel_event and cancel_event.is_set():
        return [], [], True, stats

    warnings: List[str] = []
    cache = HashCache(cache_path, enabled=use_cache)
    candidates = [
        record
        for _, records in _candidate_size_groups(files, comparison_scope)
        for record in records
    ]
    quick_meter = _ProgressMeter("快速指纹", workload.quick_bytes, progress)
    quick_by_file: Dict[str, str] = {}
    stable_stats: Dict[str, os.stat_result] = {}
    valid_records: List[FileRecord] = []

    try:
        for record in candidates:
            _check_cancel(cancel_event)
            try:
                before = record.path.stat()
                cached = cache.lookup(record, before)
                quick, complete_md5, stable = _quick_fingerprint(
                    record.path, record.size, quick_meter, cancel_event,
                )
                quick_by_file[record.file_id] = quick
                stable_stats[record.file_id] = stable
                valid_records.append(record)

                if complete_md5 is not None:
                    record.content_md5 = complete_md5
                    record.hash_source = "完整 MD5（小文件快速阶段）"
                    stats.md5_calculated_files += 1
                elif cached and cached.quick_fingerprint == quick and cached.content_md5:
                    record.content_md5 = cached.content_md5
                    record.hash_source = "缓存 MD5（快速指纹已验证）"
                    stats.cache_hits += 1
                else:
                    record.hash_source = "快速指纹已计算"
                cache.update(record, stable, quick, record.content_md5)
            except _Cancelled:
                raise
            except (OSError, ValueError) as exc:
                record.hash_source = "计算失败"
                warnings.append("无法读取快速指纹 {}：{}".format(record.path, exc))
        if candidates:
            quick_meter.advance(0, candidates[-1].path.name, force=True)
        stats.quick_bytes_read = quick_meter.processed

        if mode == HASH_MODE_DEEP:
            target_ids: Set[str] = {record.file_id for record in valid_records}
        else:
            by_quick: DefaultDict[Tuple[int, str, str], List[FileRecord]] = defaultdict(list)
            for record in valid_records:
                by_quick[(
                    record.size,
                    quick_by_file[record.file_id],
                    scope_partition_key(record, comparison_scope),
                )].append(record)
            target_ids = {
                record.file_id
                for records in by_quick.values()
                if comparison_group_allowed(records, comparison_scope)
                for record in records
            }
            for record in valid_records:
                if record.file_id not in target_ids and not record.content_md5:
                    record.hash_source = "快速指纹不同，跳过完整 MD5"

        targets = [record for record in valid_records if record.file_id in target_ids]
        to_hash = [record for record in targets if not record.content_md5]
        full_meter = _ProgressMeter(
            "完整 MD5", sum(record.size for record in to_hash), progress,
        )
        for record in to_hash:
            _check_cancel(cancel_event)
            try:
                digest, stable = _full_md5(
                    record.path,
                    stable_stats[record.file_id],
                    full_meter,
                    cancel_event,
                )
                record.content_md5 = digest
                record.hash_source = "完整 MD5"
                stable_stats[record.file_id] = stable
                stats.md5_calculated_files += 1
                cache.update(record, stable, quick_by_file[record.file_id], digest)
            except _Cancelled:
                raise
            except (OSError, ValueError) as exc:
                record.hash_source = "计算失败"
                warnings.append("无法计算完整 MD5 {}：{}".format(record.path, exc))
        if to_hash:
            full_meter.advance(0, to_hash[-1].path.name, force=True)
        stats.full_bytes_read = full_meter.processed

        by_hash: DefaultDict[Tuple[int, str, str], List[FileRecord]] = defaultdict(list)
        for record in targets:
            if record.content_md5:
                by_hash[(
                    record.size,
                    record.content_md5,
                    scope_partition_key(record, comparison_scope),
                )].append(record)
        groups = _make_hash_groups(by_hash, comparison_scope)
        cache.save()
        warnings.extend(cache.warnings)
        return groups, warnings, False, stats
    except _Cancelled:
        stats.quick_bytes_read = quick_meter.processed
        cache.save()
        warnings.extend(cache.warnings)
        return [], warnings, True, stats


class _GroupUnionFind:
    def __init__(self, count: int) -> None:
        self.parent = list(range(count))

    def find(self, index: int) -> int:
        while self.parent[index] != index:
            self.parent[index] = self.parent[self.parent[index]]
            index = self.parent[index]
        return index

    def union(self, left: int, right: int) -> None:
        a, b = self.find(left), self.find(right)
        if a != b:
            self.parent[b] = a


def merge_duplicate_groups(
    name_groups: Sequence[DuplicateGroup],
    hash_groups: Sequence[DuplicateGroup],
) -> List[DuplicateGroup]:
    """Combine name and MD5 results without transitive hash-group bridges.

    Exact-content groups are authoritative islands. A single name group may
    extend one such island with alternate encodes, but it may never join two
    different MD5 islands. This prevents one weak filename identity from
    chaining dozens of otherwise unrelated exact-duplicate pairs into a giant
    group, while still ensuring every file appears at most once in the UI.
    """

    hash_bins: List[Dict[str, object]] = []
    file_to_hash: Dict[str, int] = {}
    assigned_ids: Set[str] = set()
    for original in hash_groups:
        records = []
        for record in original.files:
            if record.file_id in assigned_ids:
                continue
            assigned_ids.add(record.file_id)
            records.append(record)
        if len(records) < 2:
            continue
        index = len(hash_bins)
        record_map = {record.file_id: record for record in records}
        hash_bins.append({
            "records": record_map,
            "name_confidences": [],
            "metadata_notes": [original.metadata_note] if original.metadata_note else [],
            "safety_warning": original.safety_warning,
        })
        for record in records:
            file_to_hash[record.file_id] = index

    standalone: List[DuplicateGroup] = []
    for name_group in name_groups:
        overlaps = {
            file_to_hash[record.file_id]
            for record in name_group.files
            if record.file_id in file_to_hash
        }
        extras = [
            record for record in name_group.files
            if record.file_id not in file_to_hash and record.file_id not in assigned_ids
        ]
        if len(overlaps) == 1:
            target_index = next(iter(overlaps))
            target = hash_bins[target_index]
            record_map = target["records"]
            assert isinstance(record_map, dict)
            for record in extras:
                record_map[record.file_id] = record
                assigned_ids.add(record.file_id)
            if extras:
                confidences = target["name_confidences"]
                assert isinstance(confidences, list)
                confidences.append(name_group.confidence)
                notes = target["metadata_notes"]
                assert isinstance(notes, list)
                if name_group.metadata_note:
                    notes.append(name_group.metadata_note)
                target["safety_warning"] = bool(
                    target["safety_warning"] or name_group.safety_warning
                )
            continue

        # No hash overlap: preserve the ordinary work-identity group. More
        # than one overlap is deliberately split; only its un-hashed remainder
        # may remain as a separate name group.
        if len(extras) >= 2:
            for record in extras:
                assigned_ids.add(record.file_id)
            records = sorted(extras, key=lambda item: item.quality_rank, reverse=True)
            standalone.append(DuplicateGroup(
                group_id=make_group_id(records),
                files=records,
                confidence=name_group.confidence,
                reason=name_group.reason,
                match_kind="name",
                metadata_note=name_group.metadata_note,
                safety_warning=name_group.safety_warning,
            ))

    merged: List[DuplicateGroup] = []
    for target in hash_bins:
        record_map = target["records"]
        confidences = target["name_confidences"]
        notes = target["metadata_notes"]
        assert isinstance(record_map, dict)
        assert isinstance(confidences, list)
        assert isinstance(notes, list)
        records = sorted(record_map.values(), key=lambda item: item.quality_rank, reverse=True)
        mixed = bool(confidences)
        merged.append(DuplicateGroup(
            group_id=make_group_id(records),
            files=records,
            confidence=min(confidences) if confidences else 1.0,
            reason=(
                "组内既有 MD5 完全相同文件，也有作品身份相同的不同版本"
                if mixed else "文件大小与完整 MD5 均相同"
            ),
            match_kind="mixed" if mixed else "hash",
            metadata_note="；".join(dict.fromkeys(notes)),
            safety_warning=bool(target["safety_warning"]),
        ))

    merged.extend(standalone)
    merged.sort(key=lambda group: (group.estimated_savings, len(group.files)), reverse=True)
    return merged
