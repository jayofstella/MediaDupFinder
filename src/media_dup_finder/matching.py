"""Explainable fuzzy grouping for files that may represent the same work."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from difflib import SequenceMatcher
from itertools import combinations
from typing import DefaultDict, Iterable, List, Sequence, Set, Tuple

from .models import DuplicateGroup, FileRecord


def _ngrams(value: str, width: int = 2) -> Set[str]:
    if len(value) <= width:
        return {value} if value else set()
    return {value[index:index + width] for index in range(len(value) - width + 1)}


def _jaccard(left: Set[str], right: Set[str]) -> float:
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / float(len(union))


def compare_records(left: FileRecord, right: FileRecord) -> Tuple[float, str]:
    """Return confidence and a short, user-facing reason."""

    a = left.name_info
    b = right.name_info

    if a.part_marker or b.part_marker:
        if a.part_marker != b.part_marker:
            return 0.0, "分段标记不同，为防误删不合并"

    if a.catalog_key and b.catalog_key:
        if a.catalog_key == b.catalog_key:
            return 1.0, "作品编号完全相同"
        return 0.0, "作品编号不同"

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

    left_tokens = {token for token in a.tokens if not token.isdigit()}
    right_tokens = {token for token in b.tokens if not token.isdigit()}
    if len(left_tokens) >= 2 and left_tokens == right_tokens:
        return 0.96, "标题词语相同，仅排列顺序不同"

    best_left = a.primary
    best_right = b.primary
    sequence = SequenceMatcher(None, best_left, best_right, autojunk=False).ratio()
    ngram = _jaccard(_ngrams(best_left), _ngrams(best_right))
    score = sequence * 0.68 + ngram * 0.32

    if len(left_tokens) >= 2 and len(right_tokens) >= 2:
        token_score = _jaccard(left_tokens, right_tokens)
        if len(left_tokens & right_tokens) >= 2 and token_score >= 0.75:
            score = max(score, 0.84 + token_score * 0.10)

    shorter, longer = sorted((best_left, best_right), key=len)
    if len(shorter) >= 3 and shorter in longer:
        containment = 0.88 + min(0.08, len(shorter) / float(len(longer)) * 0.08)
        score = max(score, containment)

    if score >= 0.96:
        reason = "文件名几乎一致"
    elif score >= 0.88:
        reason = "一个标题包含另一个标题"
    else:
        reason = "文件名高度相似"
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


def _candidate_pairs(files: Sequence[FileRecord]) -> Set[Tuple[int, int]]:
    pairs: Set[Tuple[int, int]] = set()
    exact: DefaultDict[str, List[int]] = defaultdict(list)
    blocks: DefaultDict[str, List[int]] = defaultdict(list)

    for index, item in enumerate(files):
        info = item.name_info
        if info.catalog_key:
            exact["catalog:" + info.catalog_key].append(index)
            continue

        for alias in info.aliases:
            exact["alias:" + alias].append(index)
        for token in set(info.tokens):
            if len(token) >= 3 and not token.isdigit():
                blocks["token:" + token].append(index)
        primary = info.primary
        if not primary:
            continue
        if len(primary) <= 3:
            blocks["short:" + primary[:1]].append(index)
        else:
            for gram in sorted(_ngrams(primary))[:12]:
                blocks["gram:" + gram].append(index)

    for bucket in list(exact.values()) + list(blocks.values()):
        if len(bucket) < 2:
            continue
        for left, right in _all_pairs(bucket):
            pairs.add((left, right) if left < right else (right, left))

    # Nearby normalized names are inexpensive and catch cases whose first
    # useful n-gram was removed by a release prefix.
    ordered = sorted(
        (item.name_info.primary, index)
        for index, item in enumerate(files)
        if item.name_info.primary and not item.name_info.catalog_key
    )
    for position, (_, left) in enumerate(ordered):
        for _, right in ordered[position + 1:position + 21]:
            pairs.add((left, right) if left < right else (right, left))
    return pairs


def make_group_id(files: Sequence[FileRecord]) -> str:
    payload = "\n".join(sorted(str(item.path).casefold() for item in files))
    return hashlib.sha1(payload.encode("utf-8", errors="replace")).hexdigest()[:12]


def _split_component(
    indices: List[int],
    files: Sequence[FileRecord],
    threshold: float,
) -> List[Tuple[List[int], float, str]]:
    """Split graph chains around anchors to reduce transitive false positives."""

    remaining = set(indices)
    result: List[Tuple[List[int], float, str]] = []
    while remaining:
        anchor = min(
            remaining,
            key=lambda idx: (len(files[idx].name_info.primary), files[idx].path.name.casefold()),
        )
        cluster = [anchor]
        scores = []
        reasons = []
        for index in sorted(remaining - {anchor}):
            score, reason = compare_records(files[anchor], files[index])
            if score >= threshold:
                cluster.append(index)
                scores.append(score)
                reasons.append(reason)
        for index in cluster:
            remaining.discard(index)
        if len(cluster) >= 2:
            confidence = min(scores) if scores else 1.0
            reason = min(reasons, key=len) if reasons else "文件名相似"
            result.append((cluster, confidence, reason))
    return result


def group_similar_files(files: Sequence[FileRecord], threshold: float = 0.84) -> List[DuplicateGroup]:
    """Group candidate files at the requested similarity threshold."""

    if not 0.60 <= threshold <= 1.0:
        raise ValueError("threshold must be between 0.60 and 1.0")
    if len(files) < 2:
        return []

    union_find = _UnionFind(len(files))
    for left, right in _candidate_pairs(files):
        score, reason = compare_records(files[left], files[right])
        if score >= threshold:
            union_find.union(left, right)

    components: DefaultDict[int, List[int]] = defaultdict(list)
    for index in range(len(files)):
        components[union_find.find(index)].append(index)

    groups: List[DuplicateGroup] = []
    for indices in components.values():
        if len(indices) < 2:
            continue
        for cluster, confidence, reason in _split_component(indices, files, threshold):
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
