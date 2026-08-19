"""Tkinter desktop interface for MediaDupFinder."""

from __future__ import annotations

import csv
import os
import queue
import subprocess
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import __app_name__, __version__
from .hashing import (
    HASH_MODE_DEEP,
    HASH_MODE_OFF,
    HASH_MODE_SMART,
    HashProgressState,
    HashScanStats,
    estimate_hash_workload,
    find_exact_duplicate_groups,
    merge_duplicate_groups,
)
from .locations import (
    DriveLocation,
    directory_identity,
    list_drive_locations,
    merge_unique_directory_paths,
    parse_pasted_directory_paths,
)
from .matching import MatchingProgressState, group_similar_files
from .metadata import assess_group_metadata, find_ffprobe, probe_records
from .models import DuplicateGroup, FileRecord, ScanResult, VIDEO_EXTENSIONS
from .operations import DeletionResult, send_to_recycle_bin
from .scanner import scan_directories
from .settings import load_settings, save_settings
from .utils import format_bytes, format_duration


SIMILARITY_MODES: Dict[str, float] = {
    "严格作品识别（推荐）": 0.92,
    "标准作品识别": 0.89,
    "兼容轻微拼写差异": 0.86,
}
FILE_MODES = ("视频文件（推荐）", "全部文件")
HASH_MODES: Dict[str, str] = {
    "智能扫描（推荐）": HASH_MODE_SMART,
    "完整扫描（较慢）": HASH_MODE_DEEP,
    "关闭 MD5 扫描": HASH_MODE_OFF,
}
HASH_MODE_LABELS = {value: key for key, value in HASH_MODES.items()}


def format_matching_progress_text(state: MatchingProgressState) -> str:
    """Build a compact progress line whose important counts remain visible."""

    labels = {
        "个文件": ("已解析", "个文件", "个"),
        "次候选": ("已生成", "次候选", "次"),
        "次比对": ("已比对", "次比对", "次"),
        "个候选文件": ("已整理", "个候选文件", "个"),
    }
    action, remaining_unit, rate_unit = labels.get(
        state.unit, ("已处理", state.unit, "项")
    )
    speed = state.processed_items / max(0.001, state.elapsed_seconds)
    eta_seconds = 0.0 if state.remaining_items == 0 else state.eta_seconds
    eta = format_duration(eta_seconds) if eta_seconds is not None else "计算中"
    current = " · 当前：{}".format(state.current_name) if state.current_name else ""
    return (
        "{} {:,}/{:,} · 剩余 {:,} {} · {:,.1f} {}/秒 · 已用 {} · "
        "预计剩余 {}{}"
    ).format(
        action,
        state.processed_items,
        state.total_items,
        state.remaining_items,
        remaining_unit,
        speed,
        rate_unit,
        format_duration(state.elapsed_seconds),
        eta,
        current,
    )


IDENTITY_KIND_LABELS = {
    "catalog": "作品编号",
    "dated_episode": "系列日期单集",
    "series_episode": "季集编号",
    "title": "影视标题",
}


def format_part_marker(marker: Optional[str]) -> str:
    if not marker:
        return "未标分段"
    if marker.startswith("part") and marker[4:].isdigit():
        return "第 {} 段".format(int(marker[4:]))
    if marker.startswith("segment:"):
        value = marker.split(":", 1)[1]
        return "{} 段".format(value) if value in {"A", "B"} else value
    return marker


def group_identity_basis_label(group: DuplicateGroup) -> str:
    if group.match_kind == "hash":
        return "完整 MD5"
    if group.match_kind == "mixed":
        return "身份 + MD5"
    labels = {
        IDENTITY_KIND_LABELS.get(record.name_info.identity_kind, "其他身份")
        for record in group.files
    }
    return next(iter(labels)) if len(labels) == 1 else "多种身份"


def sort_records_for_display(
    records: Sequence[FileRecord],
    column: str,
    reverse: bool,
) -> List[FileRecord]:
    """Sort known values while always leaving unknown metadata at the end."""

    action_order = {"未决定": 0, "保留": 1, "删除": 2}

    def value(record: FileRecord) -> object:
        height = record.height or record.guessed_height
        values = {
            "action": action_order.get(record.action, 0),
            "name": record.path.name.casefold(),
            "work": record.name_info.cleaned_display.casefold(),
            "identity": IDENTITY_KIND_LABELS.get(
                record.name_info.identity_kind, record.name_info.identity_kind,
            ),
            "segment": record.name_info.part_marker or "",
            "size": record.size,
            "resolution": (
                (record.width or (height * 16 // 9)) * height if height else None
            ),
            "duration": record.duration_seconds,
            "format": record.extension,
            "codec": record.codec.casefold() if record.codec else None,
            "md5": record.content_md5 or None,
            "hash_status": record.hash_source,
            "modified": record.modified_time,
            "folder": str(record.path.parent).casefold(),
        }
        return values.get(column, record.path.name.casefold())

    known = []
    unknown = []
    for record in records:
        item_value = value(record)
        target = unknown if item_value is None else known
        target.append((item_value, str(record.path).casefold(), record))
    known.sort(key=lambda item: (item[0], item[1]), reverse=reverse)
    unknown.sort(key=lambda item: item[1])
    return [item[2] for item in known + unknown]


def sort_groups_for_display(
    groups: Sequence[DuplicateGroup],
    column: str,
    reverse: bool,
) -> List[DuplicateGroup]:
    def value(group: DuplicateGroup) -> object:
        values = {
            "name": group.display_name.casefold(),
            "kind": group_identity_basis_label(group),
            "count": len(group.files),
            "saving": group.estimated_savings,
            "duration_span": group.duration_span_seconds,
            "confidence": group.confidence,
        }
        return values.get(column, group.display_name.casefold())

    known = []
    unknown = []
    for group in groups:
        item_value = value(group)
        target = unknown if item_value is None else known
        target.append((item_value, group.display_name.casefold(), group))
    known.sort(key=lambda item: (item[0], item[1]), reverse=reverse)
    unknown.sort(key=lambda item: item[1])
    return [item[2] for item in known + unknown]


def _timestamp_text(value: float) -> str:
    return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S")


def build_file_information(record: FileRecord, group: DuplicateGroup) -> str:
    """Build the complete, copyable text shown by the file-information window."""

    try:
        current_stat = record.path.stat()
        unchanged = (
            current_stat.st_size == record.size
            and current_stat.st_mtime == record.modified_time
        )
        current_state = "正常（与扫描时一致）" if unchanged else "扫描后已发生变化"
        created = _timestamp_text(current_stat.st_ctime)
        accessed = _timestamp_text(current_stat.st_atime)
        current_modified = _timestamp_text(current_stat.st_mtime)
    except OSError as exc:
        current_state = "当前无法访问：{}".format(exc)
        created = accessed = current_modified = "未知"

    duration = format_duration(record.duration_seconds)
    if record.duration_seconds is not None:
        duration += "（{:.2f} 秒）".format(record.duration_seconds)
    aliases = "、".join(record.name_info.aliases) or "无"
    warning = "是：请重点人工复核" if group.safety_warning else "否"
    note = group.metadata_note or "无"
    md5 = record.content_md5 or "未计算"
    codec = record.codec or "未知"
    catalog = record.name_info.catalog_key or "未识别"
    part_marker = format_part_marker(record.name_info.part_marker)
    identity_kind = IDENTITY_KIND_LABELS.get(
        record.name_info.identity_kind, record.name_info.identity_kind,
    )

    return "\n".join([
        "【文件】",
        "文件名：{}".format(record.path.name),
        "完整路径：{}".format(record.path),
        "所在目录：{}".format(record.path.parent),
        "扩展名：{}".format(record.extension.lstrip(".").upper() or "无"),
        "文件大小：{}（{:,} 字节）".format(format_bytes(record.size), record.size),
        "当前状态：{}".format(current_state),
        "扫描时修改时间：{}".format(_timestamp_text(record.modified_time)),
        "当前修改时间：{}".format(current_modified),
        "创建时间：{}".format(created),
        "最后访问时间：{}".format(accessed),
        "",
        "【媒体信息】",
        "分辨率：{}".format(record.resolution),
        "视频时长：{}".format(duration),
        "视频编码：{}".format(codec),
        "媒体信息来源：{}".format(record.metadata_source),
        "",
        "【哈希信息】",
        "完整 MD5：{}".format(md5),
        "MD5 状态：{}".format(record.hash_source),
        "",
        "【名称解析】",
        "身份类型：{}".format(identity_kind),
        "识别作品：{}".format(record.name_info.cleaned_display),
        "作品身份键：{}".format(record.name_info.work_key or "无"),
        "作品编号：{}".format(catalog),
        "归一化标题：{}".format(record.name_info.primary or "无"),
        "匹配别名：{}".format(aliases),
        "分段标记：{}".format(part_marker),
        "系列名称键：{}".format(record.name_info.series_key or "无"),
        "单集日期：{}".format(record.name_info.episode_date or "无"),
        "季集编号：{}".format(record.name_info.episode_id or "无"),
        "识别年份：{}".format("、".join(map(str, record.name_info.years)) or "无"),
        "",
        "【候选组】",
        "候选作品：{}".format(group.display_name),
        "身份/依据：{}".format(group_identity_basis_label(group)),
        "置信度：{:.1f}%".format(group.confidence * 100),
        "匹配原因：{}".format(group.reason),
        "组内时长差：{}".format(
            format_duration(group.duration_span_seconds)
            if group.duration_span_seconds is not None else "未知"
        ),
        "辅助提示：{}".format(note),
        "重点复核：{}".format(warning),
        "当前决定：{}".format(record.action),
    ])


REPORT_COLUMNS = (
    "候选组", "候选作品", "身份/依据", "匹配原因", "辅助提示", "重点复核",
    "置信度", "决定", "文件名", "识别作品", "身份类型", "作品身份键",
    "作品编号", "分段标记", "系列名称键", "单集日期", "季集编号",
    "完整路径", "所在目录", "大小(字节)", "MD5", "MD5状态", "分辨率",
    "时长(秒)", "编码", "格式", "修改时间",
)


def build_report_row(
    group_number: int,
    group: DuplicateGroup,
    record: FileRecord,
) -> List[object]:
    info = record.name_info
    return [
        group_number,
        group.display_name,
        group_identity_basis_label(group),
        group.reason,
        group.metadata_note,
        "是" if group.safety_warning else "否",
        "{:.1f}%".format(group.confidence * 100),
        record.action,
        record.path.name,
        info.cleaned_display,
        IDENTITY_KIND_LABELS.get(info.identity_kind, info.identity_kind),
        info.work_key,
        info.catalog_key or "",
        format_part_marker(info.part_marker) if info.part_marker else "",
        info.series_key or "",
        info.episode_date or "",
        info.episode_id or "",
        str(record.path),
        str(record.path.parent),
        record.size,
        record.content_md5 or "",
        record.hash_source,
        record.resolution,
        "" if record.duration_seconds is None else round(record.duration_seconds, 2),
        record.codec or "",
        record.extension.lstrip(".").upper(),
        datetime.fromtimestamp(record.modified_time).isoformat(timespec="seconds"),
    ]


class BatchDirectoryDialog:
    """Windows-friendly multi-drive and multi-folder selection dialog."""

    def __init__(self, parent: tk.Misc, existing: Sequence[str]) -> None:
        self.parent = parent
        self.existing = list(existing)
        self.existing_identities = {
            directory_identity(value) for value in self.existing if str(value).strip()
        }
        self.drive_locations: List[DriveLocation] = list_drive_locations()
        self.result: Optional[List[str]] = None

        self.window = tk.Toplevel(parent)
        self.window.title("批量添加扫描目录")
        self.window.geometry("720x610")
        self.window.minsize(620, 540)
        self.window.transient(parent)
        self.window.protocol("WM_DELETE_WINDOW", self._cancel)
        self._build_ui()

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.window, padding=12)
        outer.pack(fill="both", expand=True)

        ttk.Label(
            outer,
            text="可一次选择多个磁盘；列表为多选模式，直接依次点击盘符即可，无需按 Ctrl。",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(0, 8))

        drive_box = ttk.LabelFrame(outer, text=" 选择磁盘盘符 ", padding=8)
        drive_box.pack(fill="both")
        drive_frame = ttk.Frame(drive_box)
        drive_frame.pack(fill="both", expand=True)
        self.drive_list = tk.Listbox(
            drive_frame,
            height=7,
            selectmode=tk.MULTIPLE,
            exportselection=False,
            activestyle="dotbox",
        )
        self.drive_list.pack(side="left", fill="both", expand=True)
        drive_scroll = ttk.Scrollbar(drive_frame, orient="vertical", command=self.drive_list.yview)
        drive_scroll.pack(side="right", fill="y")
        self.drive_list.configure(yscrollcommand=drive_scroll.set)
        for location in self.drive_locations:
            suffix = "  （已在扫描列表）" if directory_identity(location.path) in self.existing_identities else ""
            self.drive_list.insert(
                tk.END,
                "{}    {}{}".format(location.path, location.kind, suffix),
            )

        drive_buttons = ttk.Frame(drive_box)
        drive_buttons.pack(fill="x", pady=(7, 0))
        ttk.Button(
            drive_buttons, text="选择全部固定磁盘", command=self._select_fixed_drives,
        ).pack(side="left")
        ttk.Button(
            drive_buttons, text="清除盘符选择", command=lambda: self.drive_list.selection_clear(0, tk.END),
        ).pack(side="left", padx=(6, 0))

        other_box = ttk.LabelFrame(outer, text=" 其他文件夹（可选） ", padding=8)
        other_box.pack(fill="both", pady=(9, 0))
        other_frame = ttk.Frame(other_box)
        other_frame.pack(fill="both", expand=True)
        self.other_list = tk.Listbox(
            other_frame,
            height=4,
            selectmode=tk.EXTENDED,
            exportselection=False,
            activestyle="none",
        )
        self.other_list.pack(side="left", fill="both", expand=True)
        other_buttons = ttk.Frame(other_frame)
        other_buttons.pack(side="right", fill="y", padx=(7, 0))
        ttk.Button(other_buttons, text="选择文件夹…", command=self._add_other_directory).pack(fill="x")
        ttk.Button(other_buttons, text="移除所选", command=self._remove_other_directories).pack(
            fill="x", pady=(6, 0)
        )

        paste_box = ttk.LabelFrame(outer, text=" 批量粘贴路径（可选，每行一个） ", padding=8)
        paste_box.pack(fill="both", expand=True, pady=(9, 0))
        ttk.Label(
            paste_box,
            text="支持从资源管理器“复制为路径”后直接粘贴，带英文引号也能识别。",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(0, 5))
        self.paste_text = tk.Text(paste_box, height=5, wrap="none")
        self.paste_text.pack(fill="both", expand=True)

        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(10, 0))
        ttk.Button(actions, text="取消", command=self._cancel).pack(side="right")
        ttk.Button(
            actions, text="确定添加", style="Accent.TButton", command=self._accept,
        ).pack(side="right", padx=(0, 7))

    def _select_fixed_drives(self) -> None:
        self.drive_list.selection_clear(0, tk.END)
        for index, location in enumerate(self.drive_locations):
            if location.kind == "固定磁盘":
                self.drive_list.selection_set(index)

    def _add_other_directory(self) -> None:
        selected = filedialog.askdirectory(
            parent=self.window,
            title="选择其他扫描目录",
            mustexist=True,
        )
        if not selected:
            return
        current = list(self.other_list.get(0, tk.END))
        for value in merge_unique_directory_paths([selected], current):
            self.other_list.insert(tk.END, value)

    def _remove_other_directories(self) -> None:
        for index in reversed(self.other_list.curselection()):
            self.other_list.delete(index)

    def _accept(self) -> None:
        selected_drives = [
            self.drive_locations[index].path for index in self.drive_list.curselection()
        ]
        other_directories = list(self.other_list.get(0, tk.END))
        pasted = parse_pasted_directory_paths(self.paste_text.get("1.0", "end"))
        candidates = merge_unique_directory_paths(
            selected_drives + other_directories + pasted,
            existing=self.existing,
        )
        if not candidates:
            messagebox.showinfo(
                "没有新目录",
                "请选择至少一个尚未添加的磁盘或文件夹。",
                parent=self.window,
            )
            return

        valid: List[str] = []
        invalid: List[str] = []
        for value in candidates:
            try:
                accessible = Path(value).is_dir()
            except OSError:
                accessible = False
            (valid if accessible else invalid).append(value)

        if invalid:
            preview = "\n".join(invalid[:8])
            if not valid:
                messagebox.showerror(
                    "目录不可访问",
                    "以下磁盘或目录当前无法访问：\n\n{}".format(preview),
                    parent=self.window,
                )
                return
            if not messagebox.askyesno(
                "部分目录不可访问",
                "以下位置将被跳过：\n\n{}\n\n是否继续添加其余 {} 个目录？".format(
                    preview, len(valid)
                ),
                parent=self.window,
            ):
                return

        self.result = valid
        self.window.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.window.destroy()

    def show(self) -> Optional[List[str]]:
        self.window.wait_visibility()
        self.window.grab_set()
        self.window.focus_set()
        self.window.wait_window()
        return self.result


class MediaDupFinderApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.settings = load_settings()
        self.files: List[FileRecord] = []
        self.groups: List[DuplicateGroup] = []
        self.group_by_id: Dict[str, DuplicateGroup] = {}
        self.file_by_id: Dict[str, FileRecord] = {}
        self.current_group_id: Optional[str] = None
        self.events: "queue.Queue[Tuple[str, object]]" = queue.Queue()
        self.cancel_event = threading.Event()
        self.busy = False
        self.busy_kind = ""
        self.group_sort_column = "saving"
        self.group_sort_reverse = True
        self.detail_sort_column = "size"
        self.detail_sort_reverse = True

        self.root.title("{} v{}".format(__app_name__, __version__))
        self._set_window_icon()
        self.root.geometry(str(self.settings.get("window_geometry") or "1180x760"))
        self.root.minsize(940, 620)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._configure_style()
        self._build_menu()
        self._build_ui()
        self._restore_settings_to_ui()
        self.root.after(100, self._poll_events)

    def _set_window_icon(self) -> None:
        candidates = []
        bundle_dir = getattr(sys, "_MEIPASS", None)
        if bundle_dir:
            candidates.append(Path(bundle_dir) / "icon.ico")
        candidates.append(Path(__file__).resolve().parents[2] / "resources" / "icon.ico")
        for candidate in candidates:
            if candidate.is_file():
                try:
                    self.root.iconbitmap(default=str(candidate))
                    return
                except tk.TclError:
                    pass

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        available = style.theme_names()
        if "vista" in available:
            style.theme_use("vista")
        elif "clam" in available:
            style.theme_use("clam")
        default_font = ("Microsoft YaHei UI", 9)
        self.root.option_add("*Font", default_font)
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 17, "bold"))
        style.configure("Subtitle.TLabel", foreground="#5a6573")
        style.configure("Status.TLabel", foreground="#45515f")
        style.configure("Accent.TButton", font=("Microsoft YaHei UI", 9, "bold"))
        style.configure("Treeview", rowheight=27)
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 9, "bold"))

    def _build_menu(self) -> None:
        menu = tk.Menu(self.root)
        file_menu = tk.Menu(menu, tearoff=False)
        file_menu.add_command(label="批量添加扫描目录…", command=self.add_directory)
        file_menu.add_command(label="导出当前报告…", command=self.export_report)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self._on_close)
        menu.add_cascade(label="文件", menu=file_menu)

        action_menu = tk.Menu(menu, tearoff=False)
        action_menu.add_command(label="扫描", command=self.start_scan)
        action_menu.add_command(label="当前组智能选择", command=self.smart_select_current)
        action_menu.add_command(label="全部组智能选择", command=self.smart_select_all)
        action_menu.add_command(label="清除全部选择", command=self.clear_all_actions)
        menu.add_cascade(label="操作", menu=action_menu)

        help_menu = tk.Menu(menu, tearoff=False)
        help_menu.add_command(label="使用说明", command=self.show_help)
        help_menu.add_command(label="关于", command=self.show_about)
        menu.add_cascade(label="帮助", menu=help_menu)
        self.root.configure(menu=menu)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=(14, 12, 14, 10))
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 10))
        ttk.Label(header, text=__app_name__, style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="先解析作品编号、系列单集或影视标题，再结合 MD5 与视频属性筛选候选。",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        source_box = ttk.LabelFrame(outer, text=" 1. 选择扫描目录 ", padding=8)
        source_box.pack(fill="x", pady=(0, 8))
        source_box.columnconfigure(0, weight=1)

        list_frame = ttk.Frame(source_box)
        list_frame.grid(row=0, column=0, rowspan=2, sticky="nsew", padx=(0, 8))
        list_frame.columnconfigure(0, weight=1)
        self.root_list = tk.Listbox(
            list_frame,
            height=4,
            selectmode=tk.EXTENDED,
            activestyle="none",
            exportselection=False,
        )
        self.root_list.grid(row=0, column=0, sticky="nsew")
        list_scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.root_list.yview)
        list_scroll.grid(row=0, column=1, sticky="ns")
        list_scroll_x = ttk.Scrollbar(list_frame, orient="horizontal", command=self.root_list.xview)
        list_scroll_x.grid(row=1, column=0, sticky="ew")
        self.root_list.configure(
            yscrollcommand=list_scroll.set,
            xscrollcommand=list_scroll_x.set,
        )

        self.add_button = ttk.Button(source_box, text="批量添加目录…", command=self.add_directory)
        self.add_button.grid(row=0, column=1, sticky="ew", pady=(0, 4))
        self.remove_button = ttk.Button(source_box, text="移除所选", command=self.remove_directories)
        self.remove_button.grid(row=1, column=1, sticky="new")

        controls = ttk.Frame(outer)
        controls.pack(fill="x", pady=(0, 8))
        options = ttk.LabelFrame(controls, text=" 2. 扫描设置 ", padding=(8, 7))
        options.pack(side="left", fill="x", expand=True, padx=(0, 8))

        self.recursive_var = tk.BooleanVar(value=True)
        self.metadata_var = tk.BooleanVar(value=True)
        self.hash_mode_var = tk.StringVar(value="智能扫描（推荐）")
        self.mode_var = tk.StringVar(value="严格作品识别（推荐）")
        self.file_mode_var = tk.StringVar(value=FILE_MODES[0])
        self.recursive_check = ttk.Checkbutton(options, text="包含子目录", variable=self.recursive_var)
        self.recursive_check.grid(
            row=0, column=0, sticky="w", padx=(0, 14)
        )
        self.metadata_check = ttk.Checkbutton(
            options,
            text="读取视频属性（分辨率/时长/编码）",
            variable=self.metadata_var,
        )
        self.metadata_check.grid(
            row=0, column=1, sticky="w", padx=(0, 14)
        )
        ttk.Label(options, text="作品识别：").grid(row=0, column=2, sticky="e")
        self.mode_combo = ttk.Combobox(
            options, textvariable=self.mode_var, values=tuple(SIMILARITY_MODES),
            state="readonly", width=20,
        )
        self.mode_combo.grid(row=0, column=3, sticky="w", padx=(0, 14))
        ttk.Label(options, text="类型：").grid(row=0, column=4, sticky="e")
        self.file_mode_combo = ttk.Combobox(
            options, textvariable=self.file_mode_var, values=FILE_MODES,
            state="readonly", width=16,
        )
        self.file_mode_combo.grid(row=0, column=5, sticky="w")
        ttk.Label(options, text="MD5：").grid(row=1, column=0, sticky="e", pady=(6, 0))
        self.hash_mode_combo = ttk.Combobox(
            options,
            textvariable=self.hash_mode_var,
            values=tuple(HASH_MODES),
            state="readonly",
            width=20,
        )
        self.hash_mode_combo.grid(row=1, column=1, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Label(
            options, text="MD5 模式在开始前确定；扫描过程中不会再弹窗询问",
            style="Subtitle.TLabel",
        ).grid(row=1, column=3, columnspan=3, sticky="e", pady=(6, 0))

        scan_actions = ttk.LabelFrame(controls, text=" 3. 开始 ", padding=(8, 7))
        scan_actions.pack(side="right", fill="y")
        self.scan_button = ttk.Button(
            scan_actions, text="开始扫描", style="Accent.TButton", command=self.start_scan,
        )
        self.scan_button.pack(side="left", padx=(0, 6))
        self.stop_button = ttk.Button(scan_actions, text="停止", command=self.stop_scan, state="disabled")
        self.stop_button.pack(side="left")

        result_box = ttk.LabelFrame(outer, text=" 4. 检查候选组并选择保留/删除 ", padding=7)
        result_box.pack(fill="both", expand=True)
        paned = ttk.Panedwindow(result_box, orient="horizontal")
        paned.pack(fill="both", expand=True)

        left = ttk.Frame(paned)
        right = ttk.Frame(paned)
        paned.add(left, weight=2)
        paned.add(right, weight=5)

        ttk.Label(left, text="同作品候选组（点击列标题排序）").pack(anchor="w", pady=(0, 4))
        group_frame = ttk.Frame(left)
        group_frame.pack(fill="both", expand=True, padx=(0, 5))
        group_frame.rowconfigure(0, weight=1)
        group_frame.columnconfigure(0, weight=1)
        self.group_tree = ttk.Treeview(
            group_frame,
            columns=("name", "kind", "count", "saving", "duration_span", "confidence"),
            show="headings",
            selectmode="browse",
        )
        self.group_heading_labels = {
            "name": "识别作品",
            "kind": "身份/依据",
            "count": "数量",
            "saving": "可释放",
            "duration_span": "时长差",
            "confidence": "置信度",
        }
        for column, label in self.group_heading_labels.items():
            self.group_tree.heading(
                column,
                text=label,
                command=lambda selected=column: self._sort_group_tree(selected),
            )
        self.group_tree.column("name", width=150, minwidth=100, anchor="w")
        self.group_tree.column("kind", width=82, minwidth=72, anchor="center", stretch=False)
        self.group_tree.column("count", width=48, minwidth=44, anchor="center", stretch=False)
        self.group_tree.column("saving", width=78, minwidth=66, anchor="e", stretch=False)
        self.group_tree.column("duration_span", width=68, minwidth=60, anchor="center", stretch=False)
        self.group_tree.column("confidence", width=62, minwidth=56, anchor="center", stretch=False)
        self.group_tree.grid(row=0, column=0, sticky="nsew")
        group_scroll = ttk.Scrollbar(group_frame, orient="vertical", command=self.group_tree.yview)
        group_scroll.grid(row=0, column=1, sticky="ns")
        group_scroll_x = ttk.Scrollbar(
            group_frame, orient="horizontal", command=self.group_tree.xview,
        )
        group_scroll_x.grid(row=1, column=0, sticky="ew")
        self.group_tree.configure(
            yscrollcommand=group_scroll.set, xscrollcommand=group_scroll_x.set,
        )
        self.group_tree.tag_configure("warning", foreground="#b54708")
        self.group_tree.bind("<<TreeviewSelect>>", self._on_group_select)

        detail_title = ttk.Frame(right)
        detail_title.pack(fill="x", pady=(0, 4))
        ttk.Label(detail_title, text="组内文件（可多选；点击任意列排序）").pack(side="left")
        self.reason_label = ttk.Label(detail_title, text="", style="Subtitle.TLabel")
        self.reason_label.pack(side="right")

        detail_frame = ttk.Frame(right)
        detail_frame.pack(fill="both", expand=True)
        detail_frame.rowconfigure(0, weight=1)
        detail_frame.columnconfigure(0, weight=1)
        columns = (
            "action", "name", "work", "identity", "segment", "size", "resolution",
            "duration", "format", "codec", "md5", "hash_status", "modified", "folder",
        )
        self.detail_tree = ttk.Treeview(
            detail_frame, columns=columns, show="headings", selectmode="extended",
        )
        headings = {
            "action": "决定", "name": "文件名", "work": "识别作品",
            "identity": "身份类型", "segment": "分段",
            "size": "大小", "resolution": "分辨率",
            "duration": "时长", "format": "格式", "codec": "编码", "md5": "MD5",
            "hash_status": "MD5状态", "modified": "修改时间", "folder": "所在目录",
        }
        self.detail_heading_labels = headings
        widths = {
            "action": 66, "name": 230, "work": 170, "identity": 92, "segment": 76,
            "size": 88, "resolution": 115,
            "duration": 70, "format": 56, "codec": 70, "md5": 92,
            "hash_status": 150, "modified": 130, "folder": 260,
        }
        anchors = {
            "action": "center", "size": "e", "resolution": "center",
            "identity": "center", "segment": "center", "duration": "center", "format": "center",
            "codec": "center", "md5": "center",
        }
        for column in columns:
            self.detail_tree.heading(
                column,
                text=headings[column],
                command=lambda selected=column: self._sort_detail_tree(selected),
            )
            self.detail_tree.column(
                column, width=widths[column], minwidth=50,
                anchor=anchors.get(column, "w"), stretch=column in {"name", "folder"},
            )
        self.detail_tree.grid(row=0, column=0, sticky="nsew")
        detail_y = ttk.Scrollbar(detail_frame, orient="vertical", command=self.detail_tree.yview)
        detail_y.grid(row=0, column=1, sticky="ns")
        detail_x = ttk.Scrollbar(detail_frame, orient="horizontal", command=self.detail_tree.xview)
        detail_x.grid(row=1, column=0, sticky="ew")
        self.detail_tree.configure(yscrollcommand=detail_y.set, xscrollcommand=detail_x.set)
        self.detail_tree.tag_configure("keep", foreground="#176c35")
        self.detail_tree.tag_configure("delete", foreground="#b42318")
        self.detail_tree.tag_configure("pending", foreground="#3e4c59")
        self.detail_tree.bind("<Double-1>", lambda _event: self.open_selected_file())
        self.detail_tree.bind("<Button-3>", self._show_detail_context_menu)

        self.detail_context_menu = tk.Menu(self.root, tearoff=False)
        self.detail_context_menu.add_command(label="查看完整文件信息", command=self.show_selected_file_info)
        self.detail_context_menu.add_separator()
        self.detail_context_menu.add_command(label="打开文件", command=self.launch_selected_file)
        self.detail_context_menu.add_command(label="在资源管理器中定位", command=self.open_selected_file)
        self.detail_context_menu.add_command(label="打开文件属性", command=self.show_selected_file_properties)
        self.detail_context_menu.add_separator()
        self.detail_context_menu.add_command(label="复制完整路径", command=self.copy_selected_paths)
        self.detail_context_menu.add_command(label="复制文件名", command=self.copy_selected_names)
        self.detail_context_menu.add_separator()
        self.detail_context_menu.add_command(label="设为保留", command=self.mark_keep)
        self.detail_context_menu.add_command(label="标记删除", command=self.mark_delete)
        self.detail_context_menu.add_command(label="撤销标记", command=self.mark_pending)

        action_bar = ttk.Frame(right)
        action_bar.pack(fill="x", pady=(7, 0))
        self.keep_button = ttk.Button(action_bar, text="设为保留", command=self.mark_keep)
        self.keep_button.pack(side="left", padx=(0, 5))
        self.delete_mark_button = ttk.Button(action_bar, text="标记删除", command=self.mark_delete)
        self.delete_mark_button.pack(side="left", padx=(0, 5))
        self.pending_button = ttk.Button(action_bar, text="撤销标记", command=self.mark_pending)
        self.pending_button.pack(side="left", padx=(0, 5))
        self.smart_button = ttk.Button(action_bar, text="当前组智能选择", command=self.smart_select_current)
        self.smart_button.pack(side="left", padx=(5, 5))
        self.open_button = ttk.Button(action_bar, text="在资源管理器中查看", command=self.open_selected_file)
        self.open_button.pack(side="left", padx=(0, 5))
        self.execute_button = ttk.Button(
            action_bar, text="执行已标记删除", style="Accent.TButton", command=self.execute_deletions,
        )
        self.execute_button.pack(side="right")

        bottom_actions = ttk.Frame(outer)
        bottom_actions.pack(fill="x", pady=(8, 0))
        self.summary_label = ttk.Label(bottom_actions, text="尚未扫描", style="Status.TLabel")
        self.summary_label.pack(side="left")
        ttk.Button(bottom_actions, text="导出报告…", command=self.export_report).pack(side="right", padx=(6, 0))
        ttk.Button(bottom_actions, text="清除选择", command=self.clear_all_actions).pack(side="right")

        status = ttk.Frame(outer)
        status.pack(fill="x", pady=(7, 0))
        self.progress = ttk.Progressbar(status, mode="indeterminate", length=180)
        self.progress.pack(side="left", padx=(0, 9))
        self.status_var = tk.StringVar(value="就绪。添加一个或多个目录后开始扫描。")
        ttk.Label(status, textvariable=self.status_var, style="Status.TLabel").pack(
            side="left", fill="x", expand=True
        )

        self.busy_widgets = [
            self.add_button, self.remove_button, self.scan_button, self.mode_combo,
            self.file_mode_combo, self.keep_button, self.delete_mark_button,
            self.pending_button, self.smart_button, self.open_button, self.execute_button,
            self.recursive_check, self.metadata_check, self.hash_mode_combo,
        ]

    def _restore_settings_to_ui(self) -> None:
        self.recursive_var.set(bool(self.settings.get("recursive", True)))
        self.metadata_var.set(bool(self.settings.get("read_metadata", True)))
        hash_mode = str(self.settings.get("hash_mode") or HASH_MODE_SMART)
        self.hash_mode_var.set(HASH_MODE_LABELS.get(hash_mode, "智能扫描（推荐）"))
        mode = str(self.settings.get("similarity_mode") or "严格作品识别（推荐）")
        self.mode_var.set(mode if mode in SIMILARITY_MODES else "严格作品识别（推荐）")
        for value in self.settings.get("last_roots", []):
            path = Path(str(value))
            if path.is_dir():
                self.root_list.insert(tk.END, str(path))

    def _save_ui_settings(self) -> None:
        payload = {
            "recursive": self.recursive_var.get(),
            "similarity_mode": self.mode_var.get(),
            "read_metadata": self.metadata_var.get(),
            "hash_mode": HASH_MODES.get(self.hash_mode_var.get(), HASH_MODE_SMART),
            "window_geometry": self.root.geometry(),
            "last_roots": list(self.root_list.get(0, tk.END)),
        }
        try:
            save_settings(payload)
        except OSError:
            pass

    def add_directory(self) -> None:
        existing = list(self.root_list.get(0, tk.END))
        selected = BatchDirectoryDialog(self.root, existing).show()
        if not selected:
            return
        for value in selected:
            self.root_list.insert(tk.END, value)
        self.status_var.set("已批量添加 {} 个扫描目录。".format(len(selected)))

    def remove_directories(self) -> None:
        for index in reversed(self.root_list.curselection()):
            self.root_list.delete(index)

    def _set_busy(self, busy: bool, kind: str = "") -> None:
        self.busy = busy
        self.busy_kind = kind if busy else ""
        for widget in self.busy_widgets:
            try:
                if busy:
                    widget.configure(state="disabled")
                elif isinstance(widget, ttk.Combobox):
                    widget.configure(state="readonly")
                else:
                    widget.configure(state="normal")
            except tk.TclError:
                pass
        self.stop_button.configure(state="normal" if busy and kind == "scan" else "disabled")
        if busy:
            self.progress.stop()
            self.progress.configure(mode="indeterminate", value=0)
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.configure(mode="indeterminate", value=0)

    def _set_progress_indeterminate(self) -> None:
        if not self.busy:
            return
        self.progress.stop()
        self.progress.configure(mode="indeterminate", value=0)
        self.progress.start(12)

    def _handle_hash_progress(self, state: HashProgressState) -> None:
        self.progress.stop()
        self.progress.configure(mode="determinate", maximum=100, value=state.percent)
        speed = state.processed_bytes / max(0.001, state.elapsed_seconds)
        eta = format_duration(state.eta_seconds) if state.eta_seconds is not None else "计算中"
        self.status_var.set(
            "{} {:.1f}% · 已读 {} / {} · {}/秒 · 剩余 {} · {}".format(
                state.phase,
                state.percent,
                format_bytes(state.processed_bytes),
                format_bytes(state.total_bytes),
                format_bytes(int(speed)),
                eta,
                state.current_name,
            )
        )

    def _handle_matching_progress(self, state: MatchingProgressState) -> None:
        self.progress.stop()
        self.progress.configure(mode="determinate", maximum=100, value=state.percent)
        self.summary_label.configure(text="{}：{:.1f}%".format(state.phase, state.percent))
        self.status_var.set(format_matching_progress_text(state))

    def start_scan(self) -> None:
        if self.busy:
            return
        roots = [Path(value) for value in self.root_list.get(0, tk.END)]
        if not roots:
            messagebox.showwarning("尚未选择目录", "请先点击“批量添加目录”，选择至少一个扫描位置。")
            return
        invalid = [str(path) for path in roots if not path.is_dir()]
        if invalid:
            messagebox.showerror("目录不可用", "以下目录不存在或无法访问：\n\n" + "\n".join(invalid))
            return

        threshold = SIMILARITY_MODES.get(self.mode_var.get(), 0.92)
        extensions: Optional[Iterable[str]] = None
        if self.file_mode_var.get() == "全部文件":
            extensions = []
        recursive = self.recursive_var.get()
        read_metadata = self.metadata_var.get()
        requested_hash_mode = HASH_MODES.get(self.hash_mode_var.get(), HASH_MODE_SMART)
        self.cancel_event.clear()
        self.files = []
        self.groups = []
        self._refresh_group_tree()
        self.summary_label.configure(text="正在扫描…")
        self.status_var.set("正在读取目录，此阶段不会修改任何文件…")
        self._set_busy(True, "scan")

        def worker() -> None:
            try:
                def scan_progress(count: int, location: str) -> None:
                    self.events.put(("status", "已发现 {} 个文件，正在扫描：{}".format(count, location)))

                files, warnings, cancelled = scan_directories(
                    roots=roots,
                    recursive=recursive,
                    extensions=extensions,
                    cancel_event=self.cancel_event,
                    progress=scan_progress,
                )
                if cancelled:
                    self.events.put(("scan_done", ScanResult(files, [], warnings, cancelled=True)))
                    return

                self.events.put(("status", "已扫描 {} 个文件，正在解析作品身份…".format(len(files))))

                def matching_progress(state: MatchingProgressState) -> None:
                    self.events.put(("matching_progress", state))

                name_groups = group_similar_files(
                    files,
                    threshold=threshold,
                    cancel_event=self.cancel_event,
                    progress=matching_progress,
                )
                if self.cancel_event.is_set():
                    self.events.put(("scan_done", ScanResult(files, [], warnings, cancelled=True)))
                    return
                exact_groups = []
                effective_hash_mode = requested_hash_mode
                hash_stats = HashScanStats(mode=effective_hash_mode)
                workload = estimate_hash_workload(files, effective_hash_mode)

                if effective_hash_mode != HASH_MODE_OFF:
                    self.events.put((
                        "status",
                        "发现 {} 个同大小候选，准备执行{}…".format(
                            workload.candidate_files,
                            HASH_MODE_LABELS.get(effective_hash_mode, "MD5 扫描"),
                        ),
                    ))

                    def hash_progress(state: HashProgressState) -> None:
                        self.events.put(("hash_progress", state))

                    exact_groups, hash_warnings, hash_cancelled, hash_stats = (
                        find_exact_duplicate_groups(
                            files,
                            mode=effective_hash_mode,
                            cancel_event=self.cancel_event,
                            progress=hash_progress,
                        )
                    )
                    warnings.extend(hash_warnings)
                    if hash_cancelled:
                        self.events.put(("scan_done", ScanResult(files, [], warnings, cancelled=True)))
                        return
                groups = merge_duplicate_groups(name_groups, exact_groups)

                self.events.put(("progress_indeterminate", None))
                ffprobe = find_ffprobe() if read_metadata else None
                candidate_records = [
                    record for group in groups for record in group.files
                    if record.extension in VIDEO_EXTENSIONS
                ]
                if read_metadata and candidate_records and ffprobe:
                    def metadata_progress(index: int, total: int, name: str) -> None:
                        self.events.put((
                            "status",
                            "正在读取候选视频信息 {}/{}：{}".format(index, total, name),
                        ))

                    probe_records(
                        candidate_records,
                        ffprobe,
                        progress=metadata_progress,
                        cancel_event=self.cancel_event,
                    )
                    for group in groups:
                        group.files.sort(key=lambda item: item.quality_rank, reverse=True)
                    groups.sort(key=lambda group: group.estimated_savings, reverse=True)
                elif read_metadata and candidate_records and not ffprobe:
                    self.events.put(("metadata_missing", None))

                for group in groups:
                    assess_group_metadata(group)
                    group.files.sort(key=lambda item: item.quality_rank, reverse=True)
                groups.sort(key=lambda group: group.estimated_savings, reverse=True)

                self.events.put((
                    "scan_done",
                    ScanResult(
                        files,
                        groups,
                        warnings,
                        cancelled=self.cancel_event.is_set(),
                        hash_mode=hash_stats.mode,
                        hash_candidate_files=hash_stats.candidate_files,
                        hash_bytes_read=hash_stats.total_bytes_read,
                        hash_cache_hits=hash_stats.cache_hits,
                    ),
                ))
            except Exception as exc:  # UI boundary: preserve a useful traceback.
                self.events.put(("error", (str(exc), traceback.format_exc())))

        threading.Thread(target=worker, name="media-scan", daemon=True).start()

    def stop_scan(self) -> None:
        if self.busy_kind == "scan":
            self.cancel_event.set()
            self.stop_button.configure(state="disabled")
            self.status_var.set("正在停止，请稍候…")

    def _poll_events(self) -> None:
        try:
            while True:
                event, payload = self.events.get_nowait()
                if event == "status":
                    self.status_var.set(str(payload))
                elif event == "matching_progress":
                    self._handle_matching_progress(payload)  # type: ignore[arg-type]
                elif event == "hash_progress":
                    self._handle_hash_progress(payload)  # type: ignore[arg-type]
                elif event == "progress_indeterminate":
                    self._set_progress_indeterminate()
                elif event == "metadata_missing":
                    self.status_var.set("未找到 ffprobe；将使用文件名中的 4K/1080P 等标签推测画质。")
                elif event == "scan_done":
                    self._handle_scan_done(payload)  # type: ignore[arg-type]
                elif event == "delete_done":
                    self._handle_delete_done(payload)  # type: ignore[arg-type]
                elif event == "error":
                    self._handle_worker_error(payload)  # type: ignore[arg-type]
        except queue.Empty:
            pass
        finally:
            self.root.after(100, self._poll_events)

    def _handle_scan_done(self, result: ScanResult) -> None:
        self._set_busy(False)
        if result.cancelled:
            self.status_var.set("扫描已停止；未对任何文件执行操作。")
            self.summary_label.configure(text="扫描已停止")
            return
        self.files = result.files
        self.groups = result.groups
        self.file_by_id = {item.file_id: item for item in self.files}
        self._refresh_group_tree()
        candidate_count = sum(len(group.files) for group in self.groups)
        savings = sum(group.estimated_savings for group in self.groups)
        hash_group_count = sum(group.match_kind in {"hash", "mixed"} for group in self.groups)
        hash_detail = ""
        if result.hash_mode in {HASH_MODE_SMART, HASH_MODE_DEEP}:
            hash_detail = " · MD5候选 {} 个 / 读取 {} / 缓存命中 {} 个".format(
                result.hash_candidate_files,
                format_bytes(result.hash_bytes_read),
                result.hash_cache_hits,
            )
        self.summary_label.configure(text=(
            "扫描 {} 个文件 · 找到 {} 组 / {} 个候选 · 含 {} 个 MD5 组 · 预计可释放 {}{}"
            .format(
                len(self.files), len(self.groups), candidate_count, hash_group_count,
                format_bytes(savings), hash_detail,
            )
        ))
        warning_text = "；{} 条路径警告".format(len(result.warnings)) if result.warnings else ""
        if self.groups:
            self.status_var.set("扫描完成{}。请逐组检查，软件不会自动删除。".format(warning_text))
        else:
            self.status_var.set("扫描完成{}，未找到符合当前规则的同作品候选。".format(warning_text))

    def _handle_worker_error(self, payload: Tuple[str, str]) -> None:
        self._set_busy(False)
        message, details = payload
        self.status_var.set("操作失败：{}".format(message))
        messagebox.showerror("操作失败", "{}\n\n详细信息：\n{}".format(message, details[-1800:]))

    def _sort_group_tree(self, column: str) -> None:
        if column == self.group_sort_column:
            self.group_sort_reverse = not self.group_sort_reverse
        else:
            self.group_sort_column = column
            self.group_sort_reverse = column in {
                "count", "saving", "duration_span", "confidence",
            }
        self._refresh_group_tree(self.current_group_id)

    def _sort_detail_tree(self, column: str) -> None:
        if column == self.detail_sort_column:
            self.detail_sort_reverse = not self.detail_sort_reverse
        else:
            self.detail_sort_column = column
            self.detail_sort_reverse = column in {
                "size", "resolution", "duration", "modified",
            }
        group = self._current_group()
        if group:
            self._show_group(group.group_id, list(self.detail_tree.selection()))

    def _update_sort_headings(self) -> None:
        for column, label in self.group_heading_labels.items():
            suffix = " ▼" if self.group_sort_reverse else " ▲"
            self.group_tree.heading(
                column,
                text=label + suffix if column == self.group_sort_column else label,
            )
        for column, label in self.detail_heading_labels.items():
            suffix = " ▼" if self.detail_sort_reverse else " ▲"
            self.detail_tree.heading(
                column,
                text=label + suffix if column == self.detail_sort_column else label,
            )

    def _refresh_group_tree(self, preferred_group_id: Optional[str] = None) -> None:
        for item in self.group_tree.get_children():
            self.group_tree.delete(item)
        self.group_by_id = {group.group_id: group for group in self.groups}
        self.current_group_id = None
        self._clear_detail_tree()
        self._update_sort_headings()
        ordered_groups = sort_groups_for_display(
            self.groups, self.group_sort_column, self.group_sort_reverse,
        )
        for group in ordered_groups:
            duration_span = group.duration_span_seconds
            self.group_tree.insert(
                "", tk.END, iid=group.group_id,
                values=(
                    group.display_name,
                    group_identity_basis_label(group),
                    len(group.files),
                    format_bytes(group.estimated_savings),
                    format_duration(duration_span) if duration_span is not None else "—",
                    "{:.0f}%".format(group.confidence * 100),
                ), tags=("warning",) if group.safety_warning else (),
            )
        target = preferred_group_id if preferred_group_id in self.group_by_id else None
        if target is None and ordered_groups:
            target = ordered_groups[0].group_id
        if target:
            self.group_tree.selection_set(target)
            self.group_tree.focus(target)
            self.group_tree.see(target)
            self._show_group(target)

    def _clear_detail_tree(self) -> None:
        for item in self.detail_tree.get_children():
            self.detail_tree.delete(item)
        self.reason_label.configure(text="")

    def _on_group_select(self, _event: object = None) -> None:
        selection = self.group_tree.selection()
        if selection:
            self._show_group(selection[0])

    def _show_group(self, group_id: str, preserve_selection: Optional[Sequence[str]] = None) -> None:
        group = self.group_by_id.get(group_id)
        if not group:
            return
        self.current_group_id = group_id
        self._clear_detail_tree()
        detail_reason = group.reason
        if group.metadata_note:
            detail_reason += "；" + group.metadata_note
        if group.safety_warning:
            detail_reason = "【重点复核】" + detail_reason
        self.reason_label.configure(text="判定：{}".format(detail_reason))
        preserve = set(preserve_selection or [])
        self._update_sort_headings()
        ordered_records = sort_records_for_display(
            group.files, self.detail_sort_column, self.detail_sort_reverse,
        )
        for record in ordered_records:
            modified = datetime.fromtimestamp(record.modified_time).strftime("%Y-%m-%d %H:%M")
            tag = {"保留": "keep", "删除": "delete"}.get(record.action, "pending")
            self.detail_tree.insert(
                "", tk.END, iid=record.file_id,
                values=(
                    record.action,
                    record.path.name,
                    record.name_info.cleaned_display,
                    IDENTITY_KIND_LABELS.get(
                        record.name_info.identity_kind, record.name_info.identity_kind,
                    ),
                    format_part_marker(record.name_info.part_marker),
                    format_bytes(record.size),
                    record.resolution,
                    format_duration(record.duration_seconds),
                    record.extension.lstrip(".").upper() or "—",
                    record.codec or "—",
                    (record.content_md5[:10] + "…") if record.content_md5 else "—",
                    record.hash_source,
                    modified,
                    str(record.path.parent),
                ),
                tags=(tag,),
            )
            if record.file_id in preserve:
                self.detail_tree.selection_add(record.file_id)
        if not preserve and ordered_records:
            first = ordered_records[0].file_id
            self.detail_tree.selection_set(first)
            self.detail_tree.focus(first)

    def _selected_records(self) -> List[FileRecord]:
        return [
            self.file_by_id[file_id]
            for file_id in self.detail_tree.selection()
            if file_id in self.file_by_id
        ]

    def _current_group(self) -> Optional[DuplicateGroup]:
        return self.group_by_id.get(self.current_group_id or "")

    def mark_keep(self) -> None:
        if self.busy:
            return
        group = self._current_group()
        selected = self._selected_records()
        if not group or len(selected) != 1:
            messagebox.showinfo("请选择一个文件", "“设为保留”每次需要且只能选择一个文件。")
            return
        chosen = selected[0]
        for record in group.files:
            if record.action == "保留":
                record.action = "未决定"
        chosen.action = "保留"
        self._show_group(group.group_id, [chosen.file_id])
        self._update_action_summary()

    def mark_delete(self) -> None:
        if self.busy:
            return
        group = self._current_group()
        selected = self._selected_records()
        if not group or not selected:
            messagebox.showinfo("请选择文件", "请先在右侧选中一个或多个文件。")
            return
        selected_ids = {record.file_id for record in selected}
        survivors = [record for record in group.files if record.file_id not in selected_ids and record.action != "删除"]
        if not survivors:
            messagebox.showwarning("已阻止危险操作", "同一候选组不能全部标记删除，请至少保留一个文件。")
            return
        for record in selected:
            record.action = "删除"
        self._show_group(group.group_id, list(selected_ids))
        self._update_action_summary()

    def mark_pending(self) -> None:
        if self.busy:
            return
        group = self._current_group()
        selected = self._selected_records()
        if not group or not selected:
            return
        for record in selected:
            record.action = "未决定"
        self._show_group(group.group_id, [record.file_id for record in selected])
        self._update_action_summary()

    @staticmethod
    def _apply_smart_choice(group: DuplicateGroup) -> bool:
        keep = max(group.files, key=lambda item: item.quality_rank)
        if group.safety_warning and group.match_kind != "hash":
            for record in group.files:
                record.action = "保留" if record is keep else "未决定"
            return False
        for record in group.files:
            record.action = "保留" if record is keep else "删除"
        return True

    def smart_select_current(self) -> None:
        if self.busy:
            return
        group = self._current_group()
        if not group:
            messagebox.showinfo("没有候选组", "请先完成扫描并选择一个候选组。")
            return
        selected_all = self._apply_smart_choice(group)
        keep = next(record for record in group.files if record.action == "保留")
        self._show_group(group.group_id, [keep.file_id])
        self._update_action_summary()
        if selected_all:
            self.status_var.set("已按“分辨率 → 格式 → 文件大小”给出建议，请人工确认。")
        else:
            self.status_var.set("检测到片长差异：仅建议了保留项，其余文件未自动标记删除。")

    def smart_select_all(self) -> None:
        if self.busy:
            return
        if not self.groups:
            messagebox.showinfo("没有候选组", "请先完成扫描。")
            return
        if not messagebox.askyesno(
            "全部智能选择",
            "将为每一组建议保留画质较高的文件，并把其余文件标记为删除。\n\n"
            "这一步只做标记，不会立刻删除。是否继续？",
        ):
            return
        protected = 0
        for group in self.groups:
            if not self._apply_smart_choice(group):
                protected += 1
        if self.current_group_id:
            self._show_group(self.current_group_id)
        self._update_action_summary()
        self.status_var.set(
            "已完成智能标记；{} 个片长差异组受到保护，未自动标记删除。".format(protected)
        )

    def clear_all_actions(self) -> None:
        if self.busy:
            return
        for record in self.files:
            record.action = "未决定"
        if self.current_group_id:
            self._show_group(self.current_group_id)
        self._update_action_summary()

    def _update_action_summary(self) -> None:
        marked = [record for record in self.files if record.action == "删除"]
        if marked:
            self.status_var.set(
                "已标记删除 {} 个文件，共 {}；尚未执行。".format(
                    len(marked), format_bytes(sum(record.size for record in marked))
                )
            )
        else:
            self.status_var.set("当前没有已标记删除的文件。")

    def execute_deletions(self) -> None:
        if self.busy:
            return
        marked = [record for record in self.files if record.action == "删除"]
        if not marked:
            messagebox.showinfo("没有删除标记", "请先选择文件并点击“标记删除”。")
            return
        for group in self.groups:
            if group.files and all(record.action == "删除" for record in group.files):
                messagebox.showwarning(
                    "已阻止危险操作",
                    "候选组“{}”中的文件全部被标记删除。请至少取消一个。".format(group.display_name),
                )
                return
        changed = []
        for record in marked:
            try:
                stat = record.path.stat()
                if stat.st_size != record.size or stat.st_mtime != record.modified_time:
                    changed.append(record)
            except OSError:
                changed.append(record)
        if changed:
            messagebox.showerror(
                "文件状态已变化",
                "有 {} 个文件在扫描后被移动、修改或删除，请重新扫描后再操作。".format(len(changed)),
            )
            return

        total = sum(record.size for record in marked)
        warning_groups = sum(
            group.safety_warning and any(record.action == "删除" for record in group.files)
            for group in self.groups
        )
        preview_names = "\n".join("• {}".format(record.path.name) for record in marked[:8])
        if len(marked) > 8:
            preview_names += "\n• ……另有 {} 个文件".format(len(marked) - 8)
        confirmed = messagebox.askyesno(
            "确认移入回收站",
            "即将把 {} 个文件（{}）移入 Windows 回收站：\n\n{}\n\n"
            "{}请确认已逐组检查。是否继续？".format(
                len(marked), format_bytes(total), preview_names,
                "其中 {} 个候选组存在片长差异，请重点确认。\n\n".format(warning_groups)
                if warning_groups else "",
            ),
        )
        if not confirmed:
            return

        paths = [record.path for record in marked]
        self._set_busy(True, "delete")
        self.status_var.set("正在移入回收站，请勿关闭程序…")

        def worker() -> None:
            try:
                result = send_to_recycle_bin(paths)
                self.events.put(("delete_done", result))
            except Exception as exc:
                self.events.put(("error", (str(exc), traceback.format_exc())))

        threading.Thread(target=worker, name="safe-delete", daemon=True).start()

    def _handle_delete_done(self, result: DeletionResult) -> None:
        self._set_busy(False)
        succeeded_paths = {
            os.path.normcase(os.path.abspath(str(item.path)))
            for item in result.succeeded
        }
        failed_paths = {
            os.path.normcase(os.path.abspath(str(item.path)))
            for item in result.failed
        }
        self.files = [
            record for record in self.files
            if os.path.normcase(os.path.abspath(str(record.path))) not in succeeded_paths
        ]
        for record in self.files:
            if os.path.normcase(os.path.abspath(str(record.path))) in failed_paths:
                record.action = "未决定"

        retained_groups = []
        for group in self.groups:
            group.files = [
                record for record in group.files
                if os.path.normcase(os.path.abspath(str(record.path))) not in succeeded_paths
            ]
            if len(group.files) >= 2:
                retained_groups.append(group)
        self.groups = retained_groups
        self.file_by_id = {record.file_id: record for record in self.files}
        self._refresh_group_tree()

        if result.failed:
            details = "\n".join(
                "{}：{}".format(item.path.name, item.message) for item in result.failed[:8]
            )
            messagebox.showwarning(
                "部分文件未处理",
                "成功移入回收站 {} 个，失败 {} 个。\n\n{}".format(
                    len(result.succeeded), len(result.failed), details
                ),
            )
        else:
            messagebox.showinfo(
                "操作完成",
                "{} 个文件已移入回收站。如需恢复，请打开 Windows 回收站。".format(
                    len(result.succeeded)
                ),
            )
        self.status_var.set(
            "回收站操作完成：成功 {} 个，失败 {} 个。".format(
                len(result.succeeded), len(result.failed)
            )
        )

    def _show_detail_context_menu(self, event: tk.Event) -> None:
        row_id = self.detail_tree.identify_row(event.y)
        if not row_id:
            return
        current_selection = set(self.detail_tree.selection())
        if row_id not in current_selection:
            self.detail_tree.selection_set(row_id)
        self.detail_tree.focus(row_id)
        selected_count = len(self._selected_records())
        single_state = "normal" if selected_count == 1 else "disabled"
        any_state = "normal" if selected_count else "disabled"
        for label in (
            "查看完整文件信息", "打开文件", "在资源管理器中定位",
            "打开文件属性", "设为保留",
        ):
            self.detail_context_menu.entryconfigure(label, state=single_state)
        for label in ("复制完整路径", "复制文件名", "标记删除", "撤销标记"):
            self.detail_context_menu.entryconfigure(label, state=any_state)
        try:
            self.detail_context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.detail_context_menu.grab_release()

    def _copy_text(self, value: str, status: str) -> None:
        if not value:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(value)
        self.root.update_idletasks()
        self.status_var.set(status)

    def copy_selected_paths(self) -> None:
        records = self._selected_records()
        self._copy_text(
            "\n".join(str(record.path) for record in records),
            "已复制 {} 个文件的完整路径。".format(len(records)),
        )

    def copy_selected_names(self) -> None:
        records = self._selected_records()
        self._copy_text(
            "\n".join(record.path.name for record in records),
            "已复制 {} 个文件名。".format(len(records)),
        )

    def show_selected_file_info(self) -> None:
        records = self._selected_records()
        group = self._current_group()
        if len(records) != 1 or not group:
            messagebox.showinfo("请选择一个文件", "查看完整信息时需要且只能选择一个文件。")
            return
        record = records[0]
        content = build_file_information(record, group)

        window = tk.Toplevel(self.root)
        window.title("完整文件信息 - {}".format(record.path.name))
        window.geometry("780x620")
        window.minsize(620, 460)
        window.transient(self.root)

        outer = ttk.Frame(window, padding=10)
        outer.pack(fill="both", expand=True)
        text_frame = ttk.Frame(outer)
        text_frame.pack(fill="both", expand=True)
        text = tk.Text(text_frame, wrap="word", padx=10, pady=8, undo=False)
        scroll = ttk.Scrollbar(text_frame, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        text.insert("1.0", content)
        text.configure(state="disabled")

        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(8, 0))
        ttk.Button(
            buttons,
            text="复制全部信息",
            command=lambda: self._copy_text(content, "已复制完整文件信息。"),
        ).pack(side="left")
        ttk.Button(buttons, text="打开文件", command=self.launch_selected_file).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(buttons, text="定位文件", command=self.open_selected_file).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(buttons, text="关闭", command=window.destroy).pack(side="right")
        window.grab_set()
        window.focus_set()

    def launch_selected_file(self) -> None:
        selected = self._selected_records()
        if len(selected) != 1:
            messagebox.showinfo("请选择一个文件", "打开文件时需要且只能选择一个文件。")
            return
        path = selected[0].path
        try:
            if os.name == "nt":
                os.startfile(str(path))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except OSError as exc:
            messagebox.showerror("无法打开", "无法打开文件：{}".format(exc))

    def show_selected_file_properties(self) -> None:
        selected = self._selected_records()
        if len(selected) != 1:
            messagebox.showinfo("请选择一个文件", "查看文件属性时需要且只能选择一个文件。")
            return
        if os.name != "nt":
            self.show_selected_file_info()
            return
        try:
            os.startfile(str(selected[0].path), "properties")  # type: ignore[attr-defined]
        except OSError as exc:
            messagebox.showerror("无法打开属性", "无法打开 Windows 文件属性：{}".format(exc))

    def open_selected_file(self) -> None:
        selected = self._selected_records()
        if not selected:
            return
        path = selected[0].path
        try:
            if os.name == "nt":
                subprocess.Popen(["explorer.exe", "/select,", os.path.normpath(str(path))])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path.parent)])
        except OSError as exc:
            messagebox.showerror("无法打开", "无法打开文件位置：{}".format(exc))

    def export_report(self) -> None:
        if not self.groups:
            messagebox.showinfo("没有可导出的结果", "请先完成扫描并找到候选组。")
            return
        target = filedialog.asksaveasfilename(
            title="导出扫描报告",
            defaultextension=".csv",
            filetypes=(("CSV 表格", "*.csv"), ("所有文件", "*.*")),
            initialfile="MediaDupFinder_{}.csv".format(datetime.now().strftime("%Y%m%d_%H%M%S")),
        )
        if not target:
            return
        try:
            with open(target, "w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.writer(handle)
                writer.writerow(REPORT_COLUMNS)
                for number, group in enumerate(self.groups, 1):
                    for record in group.files:
                        writer.writerow(build_report_row(number, group, record))
            self.status_var.set("报告已导出：{}".format(target))
        except OSError as exc:
            messagebox.showerror("导出失败", "无法保存报告：{}".format(exc))

    def show_help(self) -> None:
        messagebox.showinfo(
            "使用说明",
            "1. 点击“批量添加目录”，可一次勾选多个磁盘，也可添加文件夹或粘贴多行路径。\n"
            "2. 左侧选择同作品候选组，右侧比较大小、时长、分辨率和格式；点击任意列标题即可排序。\n"
            "3. 在文件行上右键，可查看完整信息、打开文件、定位目录或复制路径。\n"
            "4. 手工设置保留/删除，或使用智能选择后再复核。\n"
            "5. 点击“执行已标记删除”，文件默认进入 Windows 回收站。\n\n"
            "识别示例：\n"
            "MIDA-630、MIDA-630-C、MIDA-630-4K 会归为同组；\n"
            "寒战、寒战1、经典剧情《寒战1》会作为候选同组。\n\n"
            "软件先识别作品编号、日期单集、季集编号或影视标题，再比较作品身份；不会仅因文件名前缀相同就合并。\n"
            "同一系列的不同日期、不同季集，以及 KAVR-253-2 / KAVR-253-8 这类不同分段都会隔离。\n"
            "还支持网站前缀、繁简体、年份、罗马数字、发布组标签与 MD5 完全重复。\n"
            "MD5 可选关闭、智能、完整三档；智能模式先读三段快速指纹，只有相同时才完整读取。\n"
            "MD5 模式在开始扫描前确定，运行中不会再弹窗等待确认；已验证缓存可减少重复读取。\n\n"
            "安全提示：不同年份、日期、季集或分段不会自动合并；片长差异超过安全范围时禁止智能批量删除。",
        )

    def show_about(self) -> None:
        messagebox.showinfo(
            "关于",
            "{} v{}\n\n"
            "本地、离线、可解释的同作品与完全重复文件筛选工具。\n"
            "扫描过程不会上传文件；MD5 模式只在本机读取同大小文件。\n"
            "项目采用 MIT License，可发布到 GitHub。".format(__app_name__, __version__),
        )

    def _on_close(self) -> None:
        if self.busy_kind == "delete":
            messagebox.showwarning("正在处理文件", "回收站操作正在进行，为保证文件状态完整，请等待操作结束后再关闭。")
            return
        if self.busy_kind == "scan":
            if not messagebox.askyesno("操作尚未完成", "后台任务仍在运行。是否停止并退出？"):
                return
            self.cancel_event.set()
        self._save_ui_settings()
        self.root.destroy()


def _enable_dpi_awareness() -> None:
    if os.name != "nt":
        return
    try:
        ctypes = __import__("ctypes")
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


def main() -> None:
    _enable_dpi_awareness()
    root = tk.Tk()

    def report_callback_exception(exc_type: type, exc: BaseException, tb: object) -> None:
        details = "".join(traceback.format_exception(exc_type, exc, tb))
        messagebox.showerror("程序错误", "{}\n\n{}".format(exc, details[-1600:]))

    root.report_callback_exception = report_callback_exception  # type: ignore[assignment]
    MediaDupFinderApp(root)
    root.mainloop()
