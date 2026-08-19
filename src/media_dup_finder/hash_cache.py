"""Small, conservative cache for validated content hashes."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import FileRecord
from .utils import user_data_dir


CACHE_VERSION = 1
MAX_CACHE_ENTRIES = 20_000


def default_hash_cache_path() -> Path:
    return user_data_dir() / "hash_cache.json"


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


@dataclass(frozen=True)
class CachedHash:
    quick_fingerprint: str
    content_md5: Optional[str]


class HashCache:
    """Cache entries are reusable only after a fresh quick-fingerprint check."""

    def __init__(self, path: Optional[Path] = None, enabled: bool = True) -> None:
        self.path = Path(path) if path is not None else default_hash_cache_path()
        self.enabled = enabled
        self.entries: Dict[str, Dict[str, Any]] = {}
        self.warnings: List[str] = []
        self.dirty = False
        if enabled:
            self._load()

    def _load(self) -> None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("version") != CACHE_VERSION:
                return
            entries = payload.get("entries")
            if isinstance(entries, dict):
                self.entries = {
                    str(key): value
                    for key, value in entries.items()
                    if isinstance(value, dict)
                }
        except FileNotFoundError:
            return
        except (OSError, ValueError, TypeError) as exc:
            self.warnings.append("MD5 缓存无法读取，将重新计算：{}".format(exc))

    @staticmethod
    def _stat_identity(stat: os.stat_result) -> tuple[int, int, int]:
        return (
            int(stat.st_size),
            int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000))),
            int(getattr(stat, "st_ctime_ns", int(stat.st_ctime * 1_000_000_000))),
        )

    def lookup(self, record: FileRecord, stat: os.stat_result) -> Optional[CachedHash]:
        if not self.enabled:
            return None
        key = _path_key(record.path)
        entry = self.entries.get(key)
        if not entry:
            return None
        try:
            identity = self._stat_identity(stat)
            cached_identity = (
                int(entry["size"]), int(entry["mtime_ns"]), int(entry["ctime_ns"]),
            )
            quick = str(entry["quick"])
            content_md5 = entry.get("md5")
            if content_md5 is not None:
                content_md5 = str(content_md5)
        except (KeyError, TypeError, ValueError):
            self.entries.pop(key, None)
            self.dirty = True
            return None
        if identity != cached_identity:
            self.entries.pop(key, None)
            self.dirty = True
            return None
        return CachedHash(quick_fingerprint=quick, content_md5=content_md5)

    def update(
        self,
        record: FileRecord,
        stat: os.stat_result,
        quick_fingerprint: str,
        content_md5: Optional[str],
    ) -> None:
        if not self.enabled:
            return
        size, mtime_ns, ctime_ns = self._stat_identity(stat)
        self.entries[_path_key(record.path)] = {
            "size": size,
            "mtime_ns": mtime_ns,
            "ctime_ns": ctime_ns,
            "quick": quick_fingerprint,
            "md5": content_md5,
            "updated": int(time.time()),
        }
        self.dirty = True

    def save(self) -> None:
        if not self.enabled or not self.dirty:
            return
        try:
            if len(self.entries) > MAX_CACHE_ENTRIES:
                newest = sorted(
                    self.entries.items(),
                    key=lambda item: int(item[1].get("updated", 0)),
                    reverse=True,
                )[:MAX_CACHE_ENTRIES]
                self.entries = dict(newest)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(
                    {"version": CACHE_VERSION, "entries": self.entries},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            temporary.replace(self.path)
            self.dirty = False
        except OSError as exc:
            self.warnings.append("MD5 缓存无法保存：{}".format(exc))
