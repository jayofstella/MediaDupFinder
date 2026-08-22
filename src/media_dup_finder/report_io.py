"""Import MediaDupFinder CSV reports without requiring a new scan."""

from __future__ import annotations

import csv
import io
import os
import re
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .models import DuplicateGroup, FileRecord
from .normalization import normalize_path


REQUIRED_REPORT_COLUMNS = frozenset({
    "候选组", "决定", "完整路径", "大小(字节)", "修改时间",
})

_IDENTITY_KIND_BY_LABEL = {
    "作品编号": "catalog",
    "系列日期单集": "dated_episode",
    "季集编号": "series_episode",
    "影视标题": "title",
    "光盘通用文件名": "generic_media",
}


class ReportImportError(ValueError):
    """Raised when a CSV is not a usable MediaDupFinder report."""


@dataclass
class ImportedReport:
    source_path: Path
    files: List[FileRecord]
    groups: List[DuplicateGroup]
    warnings: List[str]
    report_version: str = "1.7.0 或更早"

    @property
    def marked_delete_count(self) -> int:
        return sum(record.action == "删除" for record in self.files)


def _decode_report(path: Path) -> str:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ReportImportError("无法读取报告：{}".format(exc))
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ReportImportError("报告文字编码无法识别，请使用 v1.7.0 导出的原始 CSV 文件")


def _reader_for_text(text: str) -> csv.DictReader:
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return csv.DictReader(io.StringIO(text, newline=""), dialect=dialect)


def _clean_row(row: Dict[object, object]) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for key, value in row.items():
        if key is None:
            continue
        result[str(key).strip()] = "" if value is None else str(value).strip()
    return result


def _parse_integer(value: str, label: str) -> int:
    cleaned = value.strip().replace(",", "")
    try:
        number = int(cleaned)
    except ValueError:
        try:
            number = int(float(cleaned))
        except ValueError:
            raise ReportImportError("{}不是有效数字：{}".format(label, value))
    if number < 0:
        raise ReportImportError("{}不能为负数：{}".format(label, value))
    return number


def _parse_float(value: str, default: Optional[float] = None) -> Optional[float]:
    cleaned = value.strip().replace(",", "")
    if not cleaned:
        return default
    try:
        return float(cleaned)
    except ValueError:
        return default


def _parse_modified_time(row: Dict[str, str]) -> Tuple[float, str]:
    exact_value = row.get("扫描快照时间戳", "").strip()
    if exact_value:
        parsed = _parse_float(exact_value)
        if parsed is not None and parsed >= 0:
            return parsed, "exact"

    value = row.get("修改时间", "").strip()
    if not value:
        raise ReportImportError("修改时间为空")
    candidates = [value]
    if value.endswith("Z"):
        candidates.append(value[:-1] + "+00:00")
    for candidate in candidates:
        try:
            return datetime.fromisoformat(candidate).timestamp(), "seconds"
        except ValueError:
            pass
    for pattern in ("%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, pattern).timestamp(), "seconds"
        except ValueError:
            pass
    raise ReportImportError("修改时间格式无法识别：{}".format(value))


def _parse_confidence(value: str) -> float:
    cleaned = value.strip().rstrip("%")
    parsed = _parse_float(cleaned, 0.0) or 0.0
    if "%" in value or parsed > 1.0:
        parsed /= 100.0
    return min(1.0, max(0.0, parsed))


def _parse_resolution(value: str) -> Tuple[Optional[int], Optional[int]]:
    match = re.search(r"(\d{2,5})\s*[xX×]\s*(\d{2,5})", value)
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2))


def _match_kind(basis: str) -> str:
    folded = basis.casefold()
    if "md5" in folded and ("身份" in basis or "+" in basis):
        return "mixed"
    if "md5" in folded:
        return "hash"
    return "name"


def _record_from_row(row: Dict[str, str]) -> FileRecord:
    path_text = row.get("完整路径", "").strip()
    if not path_text:
        raise ReportImportError("完整路径为空")
    path = Path(path_text)
    root_text = row.get("所在目录", "").strip()
    root = Path(root_text) if root_text else path.parent
    size = _parse_integer(row.get("大小(字节)", ""), "文件大小")
    modified_time, precision = _parse_modified_time(row)

    base = normalize_path(path)
    work_key = row.get("作品身份键", "").strip() or base.work_key
    catalog = row.get("作品编号", "").strip().casefold() or base.catalog_key
    identity_kind = _IDENTITY_KIND_BY_LABEL.get(
        row.get("身份类型", "").strip(), base.identity_kind,
    )
    primary = work_key or (catalog.replace("-", "") if catalog else base.primary)
    name_info = replace(
        base,
        cleaned_display=row.get("识别作品", "").strip() or base.cleaned_display,
        catalog_key=catalog,
        primary=primary,
        aliases=(primary,) if primary else base.aliases,
        identity_kind=identity_kind,
        work_key=work_key,
        series_key=row.get("系列名称键", "").strip() or base.series_key,
        episode_date=row.get("单集日期", "").strip() or base.episode_date,
        episode_id=row.get("季集编号", "").strip() or base.episode_id,
        identity_source=row.get("身份来源", "").strip() or "报告导入",
        source_text=row.get("来源文本", "").strip() or base.source_text,
    )

    action = row.get("决定", "").strip()
    if action not in {"未决定", "保留", "删除", "忽略"}:
        action = "未决定"
    width, height = _parse_resolution(row.get("分辨率", ""))
    duration = _parse_float(row.get("时长(秒)", ""))
    record = FileRecord(
        path=path,
        root=root,
        size=size,
        modified_time=modified_time,
        name_info=name_info,
        width=width,
        height=height,
        duration_seconds=duration,
        codec=row.get("编码", "").strip() or None,
        metadata_source="扫描报告导入",
        content_md5=row.get("MD5", "").strip().casefold() or None,
        hash_source=row.get("MD5状态", "").strip() or "报告未提供",
        action=action,
        snapshot_time_precision=precision,
    )
    return record


def load_report(path: Path) -> ImportedReport:
    """Load v1.7.0 and newer CSV reports, preserving every saved decision."""

    source = Path(path)
    reader = _reader_for_text(_decode_report(source))
    fieldnames = [str(value).strip() for value in (reader.fieldnames or [])]
    missing = sorted(REQUIRED_REPORT_COLUMNS.difference(fieldnames))
    if missing:
        raise ReportImportError(
            "这不是可恢复的 MediaDupFinder 报告，缺少列：{}".format("、".join(missing))
        )

    grouped: Dict[str, List[Tuple[Dict[str, str], FileRecord]]] = {}
    group_order: List[str] = []
    warnings: List[str] = []
    seen_paths = set()
    report_version = "1.7.0 或更早"

    for row_number, raw_row in enumerate(reader, 2):
        row = _clean_row(raw_row)
        if not any(row.values()):
            continue
        if row.get("报告版本", "").strip():
            report_version = row["报告版本"].strip()
        try:
            record = _record_from_row(row)
        except ReportImportError as exc:
            warnings.append("第 {} 行已跳过：{}".format(row_number, exc))
            continue
        identity = os.path.normcase(os.path.abspath(str(record.path)))
        if identity in seen_paths:
            warnings.append("第 {} 行路径重复，已跳过：{}".format(row_number, record.path))
            continue
        seen_paths.add(identity)
        group_key = row.get("候选组", "").strip() or "未编号-{}".format(row_number)
        if group_key not in grouped:
            grouped[group_key] = []
            group_order.append(group_key)
        grouped[group_key].append((row, record))

    groups: List[DuplicateGroup] = []
    files: List[FileRecord] = []
    for index, group_key in enumerate(group_order, 1):
        entries = grouped[group_key]
        if not entries:
            continue
        first_row = entries[0][0]
        group_files = [entry[1] for entry in entries]
        group = DuplicateGroup(
            group_id="report-{:06d}".format(index),
            files=group_files,
            confidence=_parse_confidence(first_row.get("置信度", "")),
            reason=first_row.get("匹配原因", "").strip() or "从扫描报告恢复",
            match_kind=_match_kind(first_row.get("身份/依据", "")),
            metadata_note=first_row.get("辅助提示", "").strip(),
            safety_warning=first_row.get("重点复核", "").strip() in {"是", "true", "True", "1"},
            display_name_override=first_row.get("候选作品", "").strip(),
        )
        groups.append(group)
        files.extend(group_files)

    if not files:
        details = "；".join(warnings[:3])
        raise ReportImportError(
            "报告中没有可恢复的文件记录{}".format("：" + details if details else "")
        )
    return ImportedReport(
        source_path=source,
        files=files,
        groups=groups,
        warnings=warnings,
        report_version=report_version,
    )
