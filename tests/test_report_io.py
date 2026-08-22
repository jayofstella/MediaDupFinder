from __future__ import annotations

import csv
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from media_dup_finder.operations import DeletionCandidate, classify_deletion_candidates
from media_dup_finder.report_io import ReportImportError, load_report


V17_COLUMNS = (
    "候选组", "候选作品", "身份/依据", "匹配原因", "辅助提示", "重点复核",
    "置信度", "决定", "文件名", "识别作品", "身份类型", "身份来源", "来源文本",
    "作品身份键", "作品编号", "分段标记", "系列名称键", "单集日期", "季集编号",
    "完整路径", "所在目录", "大小(字节)", "MD5", "MD5状态", "内容关系", "分辨率",
    "时长(秒)", "编码", "格式", "修改时间",
)


def report_row(path: Path, action: str, group: str = "1") -> dict:
    stat = path.stat()
    return {
        "候选组": group,
        "候选作品": "MIDA-630",
        "身份/依据": "作品编号",
        "匹配原因": "作品编号完全相同",
        "辅助提示": "",
        "重点复核": "否",
        "置信度": "100.0%",
        "决定": action,
        "文件名": path.name,
        "识别作品": "MIDA-630",
        "身份类型": "作品编号",
        "身份来源": "文件名",
        "来源文本": path.stem,
        "作品身份键": "mida630",
        "作品编号": "mida-630",
        "分段标记": "",
        "系列名称键": "",
        "单集日期": "",
        "季集编号": "",
        "完整路径": str(path),
        "所在目录": str(path.parent),
        "大小(字节)": str(stat.st_size),
        "MD5": "",
        "MD5状态": "未计算",
        "内容关系": "作品身份相似（内容未确认）",
        "分辨率": "1920×1080",
        "时长(秒)": "123.5",
        "编码": "h264",
        "格式": path.suffix.lstrip(".").upper(),
        "修改时间": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
    }


class ReportImportTests(unittest.TestCase):
    def test_v17_report_restores_saved_delete_marks_without_rescan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            keep = folder / "MIDA-630-4K.mp4"
            delete = folder / "MIDA-630-rm.mp4"
            keep.write_bytes(b"keep")
            delete.write_bytes(b"delete")
            report = folder / "MediaDupFinder_v17.csv"
            with report.open("w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.DictWriter(handle, fieldnames=V17_COLUMNS)
                writer.writeheader()
                writer.writerow(report_row(keep, "保留"))
                writer.writerow(report_row(delete, "删除"))

            imported = load_report(report)

            self.assertEqual(len(imported.groups), 1)
            self.assertEqual(len(imported.files), 2)
            self.assertEqual(imported.marked_delete_count, 1)
            self.assertEqual(imported.groups[0].display_name, "MIDA-630")
            self.assertEqual(
                {record.action for record in imported.files}, {"保留", "删除"},
            )
            self.assertTrue(all(
                record.snapshot_time_precision == "seconds"
                for record in imported.files
            ))
            restored_delete = next(
                record for record in imported.files if record.action == "删除"
            )
            self.assertEqual(restored_delete.width, 1920)
            self.assertEqual(restored_delete.height, 1080)
            self.assertEqual(restored_delete.duration_seconds, 123.5)
            ready, ignored = classify_deletion_candidates([
                DeletionCandidate(
                    restored_delete.path,
                    restored_delete.size,
                    restored_delete.modified_time,
                    restored_delete.snapshot_time_precision,
                ),
            ])
            self.assertEqual(len(ready), 1)
            self.assertEqual(ignored, [])

    def test_new_report_uses_exact_snapshot_timestamp_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            source = folder / "movie.mp4"
            source.write_bytes(b"content")
            row = report_row(source, "删除")
            row["扫描快照时间戳"] = repr(float(source.stat().st_mtime))
            row["报告版本"] = "1.8.0"
            columns = V17_COLUMNS + ("扫描快照时间戳", "报告版本")
            report = folder / "MediaDupFinder_v18.csv"
            with report.open("w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.DictWriter(handle, fieldnames=columns)
                writer.writeheader()
                writer.writerow(row)

            imported = load_report(report)

            self.assertEqual(imported.report_version, "1.8.0")
            self.assertEqual(imported.files[0].snapshot_time_precision, "exact")
            self.assertAlmostEqual(
                imported.files[0].modified_time, source.stat().st_mtime, places=6,
            )

    def test_unrelated_csv_is_rejected_with_missing_columns(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report = Path(temporary) / "other.csv"
            report.write_text("name,value\nfoo,1\n", encoding="utf-8")
            with self.assertRaises(ReportImportError):
                load_report(report)


if __name__ == "__main__":
    unittest.main()
