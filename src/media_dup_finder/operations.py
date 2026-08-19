"""Validated deletion operations with Windows Recycle Bin support."""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

from .utils import user_data_dir


@dataclass
class OperationItem:
    path: Path
    success: bool
    message: str


@dataclass
class DeletionResult:
    items: List[OperationItem]

    @property
    def succeeded(self) -> List[OperationItem]:
        return [item for item in self.items if item.success]

    @property
    def failed(self) -> List[OperationItem]:
        return [item for item in self.items if not item.success]


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
                {"path": str(item.path), "success": item.success, "message": item.message}
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
