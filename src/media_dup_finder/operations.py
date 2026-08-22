"""Validated deletion operations with Windows Recycle Bin support."""

from __future__ import annotations

import ctypes
import csv
import json
import os
import shutil
import stat as stat_module
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from .utils import user_data_dir


@dataclass
class OperationItem:
    path: Path
    success: bool
    message: str
    ignored: bool = False
    group_name: str = ""
    expected_size: Optional[int] = None
    current_size: Optional[int] = None
    expected_modified_time: Optional[float] = None
    current_modified_time: Optional[float] = None
    preserve_action: bool = False


@dataclass(frozen=True)
class DeletionCandidate:
    path: Path
    expected_size: int
    expected_modified_time: float
    time_precision: str = "exact"
    group_name: str = ""


@dataclass
class DeletionResult:
    items: List[OperationItem]
    ignore_report_path: Optional[Path] = None
    ignore_report_error: str = ""

    @property
    def succeeded(self) -> List[OperationItem]:
        return [item for item in self.items if item.success]

    @property
    def failed(self) -> List[OperationItem]:
        return [item for item in self.items if not item.success and not item.ignored]

    @property
    def ignored(self) -> List[OperationItem]:
        return [item for item in self.items if item.ignored]


def validate_delete_paths(paths: Iterable[Path]) -> List[Path]:
    validated = []
    seen = set()
    for raw in paths:
        path = Path(raw)
        identity = os.path.normcase(os.path.abspath(str(path)))
        if identity in seen:
            continue
        if not path.exists():
            raise FileNotFoundError("文件不存在：{}".format(path))
        if not path.is_file() or path.is_symlink():
            raise ValueError("只允许处理普通文件：{}".format(path))
        seen.add(identity)
        validated.append(path.resolve())
    if not validated:
        raise ValueError("没有已标记删除的文件")
    return validated


def _mtime_matches(candidate: DeletionCandidate, current: float) -> bool:
    if candidate.time_precision == "seconds":
        return int(candidate.expected_modified_time) == int(current)
    return candidate.expected_modified_time == current


def ignored_candidate(
    candidate: DeletionCandidate,
    message: str,
    current_size: Optional[int] = None,
    current_modified_time: Optional[float] = None,
    preserve_action: bool = False,
) -> OperationItem:
    """Create an auditable ignored result for one deletion candidate."""

    if current_size is None or current_modified_time is None:
        try:
            current_stat = candidate.path.lstat()
            if current_size is None:
                current_size = current_stat.st_size
            if current_modified_time is None:
                current_modified_time = current_stat.st_mtime
        except OSError:
            pass
    return OperationItem(
        path=candidate.path,
        success=False,
        message=message,
        ignored=True,
        group_name=candidate.group_name,
        expected_size=candidate.expected_size,
        current_size=current_size,
        expected_modified_time=candidate.expected_modified_time,
        current_modified_time=current_modified_time,
        preserve_action=preserve_action,
    )


def inspect_deletion_candidate(candidate: DeletionCandidate) -> Optional[OperationItem]:
    """Return an ignored result when a file no longer matches its scan snapshot."""

    path = candidate.path
    try:
        current_stat = path.lstat()
    except FileNotFoundError:
        return ignored_candidate(candidate, "文件不存在（可能已移动或已经删除）")
    except OSError as exc:
        return ignored_candidate(candidate, "无法读取当前文件状态：{}".format(exc))

    if stat_module.S_ISLNK(current_stat.st_mode):
        return ignored_candidate(
            candidate, "路径已变为符号链接，已按安全规则忽略",
            current_stat.st_size, current_stat.st_mtime,
        )
    if not stat_module.S_ISREG(current_stat.st_mode):
        return ignored_candidate(
            candidate, "路径当前不是普通文件，已按安全规则忽略",
            current_stat.st_size, current_stat.st_mtime,
        )
    if current_stat.st_size != candidate.expected_size:
        return ignored_candidate(
            candidate,
            "文件大小已变化（扫描时 {} 字节，当前 {} 字节）".format(
                candidate.expected_size, current_stat.st_size,
            ),
            current_stat.st_size,
            current_stat.st_mtime,
        )
    if not _mtime_matches(candidate, current_stat.st_mtime):
        return ignored_candidate(
            candidate,
            "文件修改时间已变化",
            current_stat.st_size,
            current_stat.st_mtime,
        )
    return None


def classify_deletion_candidates(
    candidates: Sequence[DeletionCandidate],
) -> Tuple[List[DeletionCandidate], List[OperationItem]]:
    ready: List[DeletionCandidate] = []
    ignored: List[OperationItem] = []
    for candidate in candidates:
        issue = inspect_deletion_candidate(candidate)
        if issue is None:
            ready.append(candidate)
        else:
            ignored.append(issue)
    return ready, ignored


def _is_unc_path(path: Path) -> bool:
    value = str(path)
    return value.startswith("\\\\") or value.startswith("//")


def _windows_recycle_one(path: Path) -> OperationItem:
    if _is_unc_path(path):
        return OperationItem(path, False, "网络路径不能保证进入回收站，已拒绝操作")
    if len(str(path)) >= 248:
        return OperationItem(path, False, "路径过长，Windows 回收站接口无法安全处理")

    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", wintypes.WORD),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    FO_DELETE = 3
    FOF_ALLOWUNDO = 0x0040
    FOF_NOERRORUI = 0x0400
    payload = str(path) + "\0\0"
    operation = SHFILEOPSTRUCTW(
        None, FO_DELETE, payload, None,
        FOF_ALLOWUNDO | FOF_NOERRORUI,
        False, None, None,
    )
    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))
    if result == 0 and not operation.fAnyOperationsAborted:
        return OperationItem(path, True, "已移入 Windows 回收站")
    return OperationItem(path, False, "Windows 回收站返回错误代码 {}".format(result))


def _portable_quarantine_one(path: Path) -> OperationItem:
    """Non-Windows fallback used for development and automated tests."""

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target_dir = path.parent / ".MediaDupFinder_Recycle" / stamp
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / path.name
        suffix = 1
        while target.exists():
            target = target_dir / "{}_{}{}".format(path.stem, suffix, path.suffix)
            suffix += 1
        shutil.move(str(path), str(target))
        return OperationItem(path, True, "已移入安全暂存目录：{}".format(target))
    except OSError as exc:
        return OperationItem(path, False, "移动失败：{}".format(exc))


def _write_history(result: DeletionResult, history_path: Optional[Path] = None) -> None:
    target = history_path or (user_data_dir() / "operation_history.jsonl")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "operation": "recycle",
            "items": [
                {
                    "path": str(item.path),
                    "success": item.success,
                    "ignored": item.ignored,
                    "message": item.message,
                }
                for item in result.items
            ],
        }
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def send_to_recycle_bin(
    paths: Sequence[Path],
    history_path: Optional[Path] = None,
) -> DeletionResult:
    """Move validated files to a recoverable location, never permanent-delete."""

    validated = validate_delete_paths(paths)
    results = []
    for path in validated:
        if os.name == "nt":
            results.append(_windows_recycle_one(path))
        else:
            results.append(_portable_quarantine_one(path))
        time.sleep(0.01)
    outcome = DeletionResult(results)
    _write_history(outcome, history_path)
    return outcome


def send_checked_deletion_candidates(
    candidates: Sequence[DeletionCandidate],
    history_path: Optional[Path] = None,
    initial_ignored: Optional[Sequence[OperationItem]] = None,
) -> DeletionResult:
    """Process candidates independently so one stale path never blocks the batch."""

    results: List[OperationItem] = list(initial_ignored or [])
    for candidate in candidates:
        issue = inspect_deletion_candidate(candidate)
        if issue is not None:
            results.append(issue)
            continue
        path = candidate.path.resolve()
        if os.name == "nt":
            item = _windows_recycle_one(path)
        else:
            item = _portable_quarantine_one(path)
        item.group_name = candidate.group_name
        item.expected_size = candidate.expected_size
        item.expected_modified_time = candidate.expected_modified_time
        results.append(item)
        time.sleep(0.01)
    outcome = DeletionResult(results)
    _write_history(outcome, history_path)
    return outcome


IGNORED_REPORT_COLUMNS = (
    "忽略时间", "来源报告", "记录类型", "候选作品", "文件名", "完整路径", "忽略原因",
    "扫描时大小(字节)", "当前大小(字节)", "扫描时修改时间", "当前修改时间",
)


def _report_time(value: Optional[float]) -> str:
    if value is None:
        return ""
    return datetime.fromtimestamp(value).isoformat(timespec="seconds")


def _unused_report_path(directory: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = directory / "MediaDupFinder_忽略清单_{}.csv".format(stamp)
    if not base.exists():
        return base
    for number in range(2, 1000):
        candidate = directory / "MediaDupFinder_忽略清单_{}_{}.csv".format(
            stamp, number,
        )
        if not candidate.exists():
            return candidate
    raise OSError("无法创建不重名的忽略清单")


def write_ignored_report(
    items: Sequence[OperationItem],
    preferred_directory: Optional[Path] = None,
    source_report: Optional[Path] = None,
) -> Optional[Path]:
    """Write an Excel-friendly audit list for automatically ignored files."""

    ignored = [item for item in items if item.ignored]
    if not ignored:
        return None
    directories: List[Path] = []
    if preferred_directory is not None:
        directories.append(Path(preferred_directory))
    documents = Path.home() / "Documents"
    if documents not in directories:
        directories.append(documents)
    fallback = user_data_dir() / "reports"
    if fallback not in directories:
        directories.append(fallback)

    last_error: Optional[OSError] = None
    for directory in directories:
        try:
            directory.mkdir(parents=True, exist_ok=True)
            target = _unused_report_path(directory)
            now_text = datetime.now().isoformat(timespec="seconds")
            with target.open("w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.writer(handle)
                writer.writerow(IGNORED_REPORT_COLUMNS)
                for item in ignored:
                    writer.writerow([
                        now_text,
                        str(source_report) if source_report else "当前扫描结果",
                        "原保留/非删除文件异常" if item.preserve_action else "删除标记已忽略",
                        item.group_name,
                        item.path.name,
                        str(item.path),
                        item.message,
                        "" if item.expected_size is None else item.expected_size,
                        "" if item.current_size is None else item.current_size,
                        _report_time(item.expected_modified_time),
                        _report_time(item.current_modified_time),
                    ])
            return target
        except OSError as exc:
            last_error = exc
    raise OSError("无法保存忽略清单：{}".format(last_error or "未知错误"))
