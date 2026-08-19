"""Optional video metadata extraction through an adjacent ffprobe executable."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, Iterable, List, Optional

from .models import DuplicateGroup, FileRecord


MetadataProgress = Callable[[int, int, str], None]


def find_ffprobe(explicit: Optional[Path] = None) -> Optional[Path]:
    """Locate ffprobe without downloading or executing anything unexpected."""

    candidates: List[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    if getattr(sys, "frozen", False):
        executable_dir = Path(sys.executable).resolve().parent
        candidates.extend([
            executable_dir / "ffprobe.exe",
            executable_dir / "tools" / "ffprobe.exe",
        ])
        bundle_dir = getattr(sys, "_MEIPASS", None)
        if bundle_dir:
            candidates.append(Path(bundle_dir) / "ffprobe.exe")
    else:
        project_root = Path(__file__).resolve().parents[2]
        candidates.extend([
            project_root / "tools" / "ffprobe.exe",
            project_root / "tools" / "ffprobe",
        ])

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    from_path = shutil.which("ffprobe")
    return Path(from_path) if from_path else None


def probe_file(record: FileRecord, ffprobe: Path, timeout: int = 20) -> None:
    """Populate one record. Failures remain non-fatal and visible in source."""

    command = [
        str(ffprobe), "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,codec_name:format=duration",
        "-of", "json", str(record.path),
    ]
    startupinfo = None
    creationflags = 0
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        creationflags = 0x08000000  # CREATE_NO_WINDOW
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=timeout,
            startupinfo=startupinfo,
            creationflags=creationflags,
            check=False,
        )
        if completed.returncode != 0:
            record.metadata_source = "读取失败"
            return
        payload = json.loads(completed.stdout or "{}")
        streams = payload.get("streams") or []
        if streams:
            stream = streams[0]
            record.width = int(stream["width"]) if stream.get("width") else None
            record.height = int(stream["height"]) if stream.get("height") else None
            record.codec = str(stream.get("codec_name") or "") or None
        duration = (payload.get("format") or {}).get("duration")
        record.duration_seconds = float(duration) if duration else None
        record.metadata_source = "ffprobe"
    except (OSError, ValueError, subprocess.TimeoutExpired, json.JSONDecodeError):
        record.metadata_source = "读取失败"


def probe_records(
    records: Iterable[FileRecord],
    ffprobe: Path,
    progress: Optional[MetadataProgress] = None,
    cancel_event: Optional[threading.Event] = None,
) -> None:
    unique = []
    seen = set()
    for record in records:
        if record.file_id not in seen:
            seen.add(record.file_id)
            unique.append(record)
    total = len(unique)
    shared_metadata = {}
    for index, record in enumerate(unique, 1):
        if cancel_event and cancel_event.is_set():
            break
        if progress:
            progress(index, total, record.path.name)
        cache_key = record.content_md5
        if cache_key and cache_key in shared_metadata:
            width, height, duration, codec = shared_metadata[cache_key]
            record.width = width
            record.height = height
            record.duration_seconds = duration
            record.codec = codec
            record.metadata_source = "同 MD5 文件共享"
            continue
        probe_file(record, ffprobe)
        if cache_key and record.metadata_source == "ffprobe":
            shared_metadata[cache_key] = (
                record.width, record.height, record.duration_seconds, record.codec,
            )


def assess_group_metadata(group: DuplicateGroup) -> None:
    """Add an auxiliary review note; metadata never proves title identity."""

    group.metadata_note = ""
    group.safety_warning = False
    if group.match_kind == "hash" and group.files:
        hashes = {record.content_md5 for record in group.files if record.content_md5}
        if len(hashes) == 1:
            group.metadata_note = "同尺寸且完整 MD5 相同"
            return

    durations = sorted(
        record.duration_seconds for record in group.files
        if record.duration_seconds is not None and record.duration_seconds > 0
    )
    notes = []
    if len(durations) >= 2:
        shortest, longest = durations[0], durations[-1]
        difference = longest - shortest
        if difference <= max(8.0, shortest * 0.01):
            notes.append("视频时长接近")
        elif difference > max(30.0, shortest * 0.03):
            group.safety_warning = True
            if difference >= 60.0:
                gap = "{:.1f} 分钟".format(difference / 60.0)
            else:
                gap = "{:.0f} 秒".format(difference)
            notes.append("片长相差约 {}，可能不是同一内容或存在缺失".format(gap))
        else:
            notes.append("片长存在小幅差异，建议播放复核")

    heights = [record.height for record in group.files if record.height]
    if len(heights) >= 2 and max(heights) >= min(heights) * 2:
        notes.append("分辨率差异较大")
    group.metadata_note = "；".join(notes)
