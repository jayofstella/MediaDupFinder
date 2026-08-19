"""Explainable grouping based on parsed work identities."""

from __future__ import annotations

import hashlib
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from itertools import combinations
from typing import Callable, DefaultDict, Iterable, List, Optional, Sequence, Set, Tuple

from .models import DuplicateGroup, FileRecord
from .normalization import NormalizedName


@dataclass(frozen=True)
class MatchingProgressState:
    phase: str
    processed_items: int
    total_items: int
    current_name: str
    elapsed_seconds: float
    eta_seconds: Optional[float]
    unit: str

    @property
    def percent(self) -> float:
        if self.total_items <= 0:
            return 100.0
        return min(100.0, self.processed_items * 100.0 / self.total_items)

    @property
    def remaining_items(self) -> int:
        return max(0, self.total_items - self.processed_items)


MatchingProgress = Callable[[MatchingProgressState], None]


class _ProgressMeter:
    def __init__(
        self,
        phase: str,
        total: int,
        unit: str,
        callback: Optional[MatchingProgress],
    ) -> None:
        self.phase = phase
        self.total = max(0, int(total))
        self.unit = unit
        self.callback = callback
        self.processed = 0
        self.started = time.monotonic()
        self.last_emit = 0.0

    def advance(self, name: str = "", amount: int = 1, force: bool = False) -> None:
        self.processed = min(self.total, self.processed + max(0, int(amount)))
        if not self.callback:
            return
        now = time.monotonic()
        if not force and now - self.last_emit < 0.20 and self.processed < self.total:
            return
        elapsed = max(0.001, now - self.started)
        speed = self.processed / elapsed
        remaining = max(0, self.total - self.processed)
        eta = remaining / speed if speed > 0 else None
        self.callback(MatchingProgressState(
            phase=self.phase,
            processed_items=self.processed,
            total_items=self.total,
            current_name=name,
            elapsed_seconds=elapsed,
            eta_seconds=eta,
            unit=self.unit,
        ))
        self.last_emit = now


def _cancelled(cancel_event: Optional[threading.Event]) -> bool:
    return bool(cancel_event and cancel_event.is_set())


def _ngrams(value: str, width: int = 2) -> Set[str]:
    if len(value) <= width:
        return {value} if value else set()
    return {value[index:index + width] for index in range(len(value) - width + 1)}


def _jaccard(left: Set[str], right: Set[str]) -> float:
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / float(len(union))


def _has_sufficient_title_identity(info: NormalizedName) -> bool:
    """Reject labels that are too ambiguous to represent a work on their own."""

    if info.identity_kind != "title":
        return True
    if not info.primary:
        return False
    tokens = tuple(token for token in info.tokens if token)
    # Values such as 015, 032 or 230 commonly mean an episode/order number.
    # Without a series name they are not a reliable work identity. Four-digit
    # numeric titles such as 1917 and 1984 remain available.
    if tokens and all(token.isdigit() for token in tokens) and len(info.primary) <= 3:
        return False
    return True


def _token_spelling_compatible(left: Sequence[str], right: Sequence[str]) -> bool:
    """Allow spelling mistakes, but reject a different word after a shared prefix."""

    if len(left) != len(right):
        return False
    unmatched = list(right)
    for token in sorted(left, key=len, reverse=True):
        if not unmatched:
            return False
        best_index = max(
            range(len(unmatched)),
            key=lambda index: SequenceMatcher(
                None, token, unmatched[index], autojunk=False,
            ).ratio(),
        )
        other = unmatched.pop(best_index)
        if token == other:
            continue
        best_score = SequenceMatcher(None, token, other, autojunk=False).ratio()
        # Very short words are not typo-tolerant: changing one character usually
        # changes the word itself. Longer words need a strong per-word match.
        required = 0.90 if min(len(token), len(other)) <= 3 else 0.84
        if best_score < required:
            return False
    return True


def compare_records(left: FileRecord, right: FileRecord) -> Tuple[float, str]:
    """Compare parsed work identities, not generic filename resemblance."""

    a = left.name_info
    b = right.name_info

    if not _has_sufficient_title_identity(a) or not _has_sufficient_title_identity(b):
        return 0.0, "作品名称信息不足，无法安全确认是同一作品"

    if a.part_marker != b.part_marker and (a.part_marker or b.part_marker):
        return 0.0, "同一作品的分段标记不同，不作为可互删的重复文件"

    structured_kinds = {"dated_episode", "series_episode"}
    if a.identity_kind in structured_kinds or b.identity_kind in structured_kinds:
        if a.identity_kind != b.identity_kind:
            return 0.0, "作品身份类型不同"
        if a.series_key != b.series_key:
            return 0.0, "系列名称不同"
        if a.identity_kind == "dated_episode":
            if a.episode_date != b.episode_date:
                return 0.0, "同一系列但作品日期不同"
            left_title = tuple(a.tokens[2:])
            right_title = tuple(b.tokens[2:])
            if left_title and right_title and left_title != right_title:
                if set(left_title) == set(right_title):
                    return 0.98, "系列、日期和单集标题相同，仅词语顺序不同"
                if not _token_spelling_compatible(left_title, right_title):
                    return 0.0, "系列与日期相同，但单集标题包含不同实质词语"
                left_value = "".join(left_title)
                right_value = "".join(right_title)
                spelling = SequenceMatcher(
                    None, left_value, right_value, autojunk=False,
                ).ratio()
                if spelling < 0.94:
                    return 0.0, "系列与日期相同，但单集标题不同"
                return 0.94, "系列、日期相同，单集标题存在轻微拼写差异"
            if left_title != right_title:
                return 0.94, "系列与日期相同，其中一侧缺少单集标题"
            return 1.0, "系列、日期和单集标题完全相同"
        if a.episode_id != b.episode_id:
            return 0.0, "同一系列但季集编号不同"
        return 1.0, "系列名称与季集编号完全相同"

    if a.catalog_key and b.catalog_key:
        if a.catalog_key == b.catalog_key:
            return 1.0, "作品编号完全相同"
        return 0.0, "作品编号不同"
    if bool(a.catalog_key) != bool(b.catalog_key):
        return 0.0, "一侧为作品编号，另一侧为普通标题"

    if a.years and b.years and set(a.years).isdisjoint(b.years):
        return 0.0, "标题年份不同，为防止合并同名翻拍作品"

    if not a.primary or not b.primary:
        return 0.0, "有效标题不足"

    alias_overlap = set(a.aliases) & set(b.aliases)
    if alias_overlap:
        if a.primary == b.primary:
            return 1.0, "清理画质/格式标签后标题相同"
        if bool(a.years) != bool(b.years):
            return 0.96, "标题相同，其中一侧包含发行年份"
        return 0.98, "标题与第一部别名相同"

    left_tokens = set(a.tokens)
    right_tokens = set(b.tokens)
    if len(left_tokens) >= 2 and left_tokens == right_tokens:
        return 0.96, "标题词语相同，仅排列顺序不同"

    left_numbers = {token.lstrip("0") or "0" for token in a.tokens if token.isdigit()}
    right_numbers = {token.lstrip("0") or "0" for token in b.tokens if token.isdigit()}
    if left_numbers != right_numbers and (left_numbers or right_numbers):
        return 0.0, "作品标题中的数字或续集编号不同"

    # Fuzzy spelling is only a final typo-tolerance rule for already parsed
    # work titles. Different token counts usually mean a different subtitle or
    # episode, so a shared franchise prefix alone can never create a match.
    if len(a.tokens) != len(b.tokens):
        return 0.0, "作品标题包含不同的实质词语"
    if len(a.tokens) >= 2 and not _token_spelling_compatible(a.tokens, b.tokens):
        return 0.0, "作品标题共享系列词，但包含不同的实质词语"

    best_left = a.primary
    best_right = b.primary
    coverage = min(len(best_left), len(best_right)) / float(max(len(best_left), len(best_right)))
    if min(len(best_left), len(best_right)) < 4 or coverage < 0.85:
        return 0.0, "作品标题有效长度或覆盖度不足"
    sequence = SequenceMatcher(None, best_left, best_right, autojunk=False).ratio()
    ngram = _jaccard(_ngrams(best_left), _ngrams(best_right))
    score = sequence * 0.75 + ngram * 0.25

    if score >= 0.96:
        reason = "解析后的作品标题几乎一致"
    elif score >= 0.86:
        reason = "解析后的作品标题存在轻微拼写差异"
    else:
        reason = "作品标题差异较大"
    return min(score, 1.0), reason


class _UnionFind:
    def __init__(self, count: int) -> None:
        self.parent = list(range(count))
        self.rank = [0] * count

    def find(self, value: int) -> int:
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = self.parent[value]
        return value

    def union(self, left: int, right: int) -> None:
        a = self.find(left)
        b = self.find(right)
        if a == b:
            return
        if self.rank[a] < self.rank[b]:
            a, b = b, a
        self.parent[b] = a
        if self.rank[a] == self.rank[b]:
            self.rank[a] += 1


def _all_pairs(indices: Sequence[int]) -> Iterable[Tuple[int, int]]:
    if len(indices) <= 300:
        return combinations(indices, 2)
    # A pathological common token should not create millions of comparisons.
    ordered = sorted(indices)
    return (
        (ordered[index], ordered[other])
        for index in range(len(ordered))
        for other in range(index + 1, min(index + 51, len(ordered)))
    )


def _limited_pair_count(count: int, width: int) -> int:
    remaining = max(0, count - 1)
    if remaining <= width:
        return count * remaining // 2
    return width * (remaining - width) + width * (width + 1) // 2


def _bucket_pair_count(count: int) -> int:
    if count <= 300:
        return count * max(0, count - 1) // 2
    return _limited_pair_count(count, 50)


def _candidate_pairs(
    files: Sequence[FileRecord],
    progress: Optional[MatchingProgress] = None,
    cancel_event: Optional[threading.Event] = None,
) -> Set[Tuple[int, int]]:
    pairs: Set[Tuple[int, int]] = set()
    exact: DefaultDict[str, List[int]] = defaultdict(list)
    blocks: DefaultDict[str, List[int]] = defaultdict(list)

    index_meter = _ProgressMeter("解析作品身份", len(files), "个文件", progress)
    index_meter.advance(amount=0, force=True)
    for index, item in enumerate(files):
        if _cancelled(cancel_event):
            return pairs
        info = item.name_info
        if not _has_sufficient_title_identity(info):
            index_meter.advance(item.path.name)
            continue
        if info.catalog_key:
            exact["catalog:" + info.catalog_key].append(index)
            index_meter.advance(item.path.name)
            continue

        for alias in info.aliases:
            exact["alias:" + alias].append(index)
        for token in set(info.tokens):
            if len(token) >= 3 and not token.isdigit():
                blocks["token:" + token].append(index)
        primary = info.primary
        if not primary:
            index_meter.advance(item.path.name)
            continue
        if len(primary) <= 3:
            blocks["short:" + primary[:1]].append(index)
        else:
            for gram in sorted(_ngrams(primary))[:12]:
                blocks["gram:" + gram].append(index)
        index_meter.advance(item.path.name)

    buckets = [
        bucket
        for bucket in list(exact.values()) + list(blocks.values())
        if len(bucket) >= 2
    ]
    ordered = sorted(
        (item.name_info.primary, index)
        for index, item in enumerate(files)
        if item.name_info.primary and not item.name_info.catalog_key
    )
    generation_total = sum(_bucket_pair_count(len(bucket)) for bucket in buckets)
    generation_total += _limited_pair_count(len(ordered), 20)
    generation_meter = _ProgressMeter(
        "生成同作品候选", generation_total, "次候选", progress,
    )
    generation_meter.advance(amount=0, force=True)

    for bucket in buckets:
        for left, right in _all_pairs(bucket):
            if _cancelled(cancel_event):
                return pairs
            pairs.add((left, right) if left < right else (right, left))
            generation_meter.advance(
                "{} ↔ {}".format(files[left].path.name, files[right].path.name)
            )

    # Nearby normalized names are inexpensive and catch cases whose first
    # useful n-gram was removed by a release prefix.
    for position, (_, left) in enumerate(ordered):
        for _, right in ordered[position + 1:position + 21]:
            if _cancelled(cancel_event):
                return pairs
            pairs.add((left, right) if left < right else (right, left))
            generation_meter.advance(
                "{} ↔ {}".format(files[left].path.name, files[right].path.name)
            )
    return pairs


def make_group_id(files: Sequence[FileRecord]) -> str:
    payload = "\n".join(sorted(str(item.path).casefold() for item in files))
    return hashlib.sha1(payload.encode("utf-8", errors="replace")).hexdigest()[:12]


def _split_component(
    indices: List[int],
    files: Sequence[FileRecord],
    threshold: float,
    progress_meter: Optional[_ProgressMeter] = None,
    cancel_event: Optional[threading.Event] = None,
) -> List[Tuple[List[int], float, str]]:
    """Split graph chains around anchors to reduce transitive false positives."""

    remaining = set(indices)
    result: List[Tuple[List[int], float, str]] = []
    while remaining:
        if _cancelled(cancel_event):
            return []
        anchor = min(
            remaining,
            key=lambda idx: (len(files[idx].name_info.primary), files[idx].path.name.casefold()),
        )
        cluster = [anchor]
        scores = []
        reasons = []
        for index in sorted(remaining - {anchor}):
            if _cancelled(cancel_event):
                return []
            score, reason = compare_records(files[anchor], files[index])
            if score >= threshold:
                cluster.append(index)
                scores.append(score)
                reasons.append(reason)
        for index in cluster:
            remaining.discard(index)
        if progress_meter:
            progress_meter.advance(files[anchor].path.name, amount=len(cluster))
        if len(cluster) >= 2:
            confidence = min(scores) if scores else 1.0
            reason = min(reasons, key=len) if reasons else "解析后的作品身份相同"
            result.append((cluster, confidence, reason))
    return result


def group_similar_files(
    files: Sequence[FileRecord],
    threshold: float = 0.90,
    cancel_event: Optional[threading.Event] = None,
    progress: Optional[MatchingProgress] = None,
) -> List[DuplicateGroup]:
    """Group candidate files at the requested similarity threshold."""

    if not 0.60 <= threshold <= 1.0:
        raise ValueError("threshold must be between 0.60 and 1.0")
    if len(files) < 2 or _cancelled(cancel_event):
        return []

    union_find = _UnionFind(len(files))
    candidate_pairs = _candidate_pairs(files, progress=progress, cancel_event=cancel_event)
    if _cancelled(cancel_event):
        return []
    comparison_meter = _ProgressMeter(
        "核对作品身份", len(candidate_pairs), "次比对", progress,
    )
    comparison_meter.advance(amount=0, force=True)
    for left, right in candidate_pairs:
        if _cancelled(cancel_event):
            return []
        score, reason = compare_records(files[left], files[right])
        if score >= threshold:
            union_find.union(left, right)
        comparison_meter.advance(
            "{} ↔ {}".format(files[left].path.name, files[right].path.name)
        )

    components: DefaultDict[int, List[int]] = defaultdict(list)
    for index in range(len(files)):
        if _cancelled(cancel_event):
            return []
        components[union_find.find(index)].append(index)

    candidate_components = [indices for indices in components.values() if len(indices) >= 2]
    group_meter = _ProgressMeter(
        "整理同作品候选组",
        sum(len(indices) for indices in candidate_components),
        "个候选文件",
        progress,
    )
    group_meter.advance(amount=0, force=True)
    groups: List[DuplicateGroup] = []
    for indices in candidate_components:
        if _cancelled(cancel_event):
            return []
        split_groups = _split_component(
            indices,
            files,
            threshold,
            progress_meter=group_meter,
            cancel_event=cancel_event,
        )
        if _cancelled(cancel_event):
            return []
        for cluster, confidence, reason in split_groups:
            records = [files[index] for index in cluster]
            records.sort(key=lambda item: item.quality_rank, reverse=True)
            groups.append(DuplicateGroup(
                group_id=make_group_id(records),
                files=records,
                confidence=confidence,
                reason=reason,
                match_kind="name",
            ))

    groups.sort(key=lambda group: (group.estimated_savings, len(group.files)), reverse=True)
    return groups
