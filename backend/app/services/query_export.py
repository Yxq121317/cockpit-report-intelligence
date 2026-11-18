"""查数结果导出：XLSX / CSV，中文表头不乱码。"""

from __future__ import annotations

import csv
import io
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from app.schemas.query import ExportColumn

XLSX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


def _safe_filename(filename: str, suffix: str) -> str:
    cleaned = filename.strip().replace("\\", "_").replace("/", "_")
    cleaned = "".join(ch for ch in cleaned if ch not in '\r\n"')
    cleaned = cleaned[:80] or "导出数据"
    if not cleaned.lower().endswith(suffix):
        cleaned += suffix
    return cleaned


def _labels(columns: list[ExportColumn]) -> list[str]:
    return [column.label or column.key for column in columns]


def _write_sheet(
    sheet,
    columns: list[ExportColumn],
    rows: list[dict[str, Any]],
) -> None:
    """写入一个工作表：深蓝表头、冻结首行、列宽自适应。"""
    labels = _labels(columns)
    sheet.append(labels)
    for row in rows:
        sheet.append([row.get(column.key) for column in columns])

    header_fill = PatternFill("solid", fgColor="1F4E79")
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")
    for index, label in enumerate(labels, start=1):
        longest = max(
            [len(str(label))] + [len(str(row.get(columns[index - 1].key, ""))) for row in rows]
        )
        sheet.column_dimensions[sheet.cell(row=1, column=index).column_letter].width = min(
            max(10, longest + 2), 40
        )
    sheet.freeze_panes = "A2"


def build_xlsx(columns: list[ExportColumn], rows: list[dict[str, Any]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "查询结果"
    _write_sheet(sheet, columns, rows)

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def build_multi_sheet_xlsx(
    sheets: list[tuple[str, list[ExportColumn], list[dict[str, Any]]]],
) -> bytes:
    """生成多工作表 XLSX；每项为 (工作表名, 列定义, 行数据)。"""
    workbook = Workbook()
    workbook.remove(workbook.active)
    used: set[str] = set()
    for raw_title, columns, rows in sheets:
        title = (raw_title or "工作表").strip()[:31] or "工作表"
        candidate = title
        index = 2
        while candidate in used:
            suffix = f"_{index}"
            candidate = f"{title[: 31 - len(suffix)]}{suffix}"
            index += 1
        used.add(candidate)
        _write_sheet(workbook.create_sheet(title=candidate), columns, rows)
    if not workbook.sheetnames:
        _write_sheet(workbook.create_sheet(title="报表"), [], [])

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def build_csv(columns: list[ExportColumn], rows: list[dict[str, Any]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(_labels(columns))
    for row in rows:
        writer.writerow([row.get(column.key, "") for column in columns])
    return buffer.getvalue().encode("utf-8-sig")


def build_export(
    fmt: str,
    columns: list[ExportColumn],
    rows: list[dict[str, Any]],
    filename: str,
) -> tuple[bytes, str, str]:
    if fmt == "csv":
        safe_name = _safe_filename(filename, ".csv")
        return build_csv(columns, rows), "text/csv; charset=utf-8", safe_name
    safe_name = _safe_filename(filename, ".xlsx")
    return build_xlsx(columns, rows), XLSX_MEDIA_TYPE, safe_name
