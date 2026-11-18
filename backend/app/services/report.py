"""校园报表生成：只读统计当前 8 张表，输出可下载的 XLSX。

报表文件存放在 ``data/reports/``，不新增数据库表；每个报表由随机
report_id 标识，并写一个 sidecar JSON 记录标题等元数据。
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from sqlalchemy import Numeric, case, cast, func, select
from sqlalchemy.orm import Session

from app.core.db import DATA_DIR
from app.models import (
    Attendance,
    Class,
    Grade,
    LeaveRequest,
    Parent,
    Student,
    Teacher,
    Timetable,
)
from app.schemas.query import ExportColumn
from app.services.query_export import build_multi_sheet_xlsx, build_xlsx

REPORTS_DIR = DATA_DIR / "reports"
REPORT_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")

REPORT_LABELS = {
    "attendance_summary": "出勤报表",
    "leave_summary": "请假报表",
    "student_roster": "学生花名册",
    "timetable": "课程表",
    "grade_overview": "年级概况报表",
    "campus_overview": "校园数据总览报表",
}

WEEKDAY_LABELS = {
    1: "周一",
    2: "周二",
    3: "周三",
    4: "周四",
    5: "周五",
    6: "周六",
    7: "周日",
}


class ReportError(Exception):
    """可展示给用户的报表生成错误。"""


def _as_of(session: Session) -> date:
    latest = session.scalar(select(func.max(Attendance.date)))
    if isinstance(latest, datetime):
        latest = latest.date()
    return latest or date.today()


def _parse_date(value: str | date | None, *, field: str) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise ReportError(f"{field}格式应为 YYYY-MM-DD") from exc


def _date_range(
    session: Session,
    start_date: str | date | None,
    end_date: str | date | None,
) -> tuple[date, date]:
    as_of = _as_of(session)
    start = _parse_date(start_date, field="开始日期")
    end = _parse_date(end_date, field="结束日期")
    if start is None and end is None:
        return as_of - timedelta(days=29), as_of
    if start is None:
        start = (end or as_of) - timedelta(days=29)
    if end is None:
        end = as_of
    if end < start:
        raise ReportError("结束日期不能早于开始日期")
    return start, end


def _target_scope(
    session: Session, target: str | None
) -> tuple[str, int | None, int | None]:
    """返回 (范围标签, class_id, grade_id)。"""
    target = (target or "").strip()
    if not target or target in {"全校", "全体", "学校"}:
        return "全校", None, None
    if target.endswith("班"):
        class_row = session.scalar(select(Class).where(Class.name == target))
        if class_row is None:
            raise ReportError(f"没有找到班级：{target}")
        return target, class_row.id, None
    if target.endswith("年级"):
        grade_row = session.scalar(select(Grade).where(Grade.name == target))
        if grade_row is None:
            raise ReportError(f"没有找到年级：{target}")
        return target, None, grade_row.id
    class_row = session.scalar(select(Class).where(Class.name == target))
    if class_row is not None:
        return target, class_row.id, None
    grade_row = session.scalar(select(Grade).where(Grade.name == target))
    if grade_row is not None:
        return target, None, grade_row.id
    raise ReportError(f"没有找到报表范围：{target}")


def _cell(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, bool):
        return "是" if value else "否"
    if hasattr(value, "to_integral_value"):
        number = float(value)
        return int(number) if number.is_integer() else round(number, 1)
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _round_ratio(expression):
    """统一保留 1 位小数；显式转 NUMERIC，兼容 PostgreSQL 的 round。"""
    return func.round(cast(expression, Numeric(12, 4)), 1)


def _rows(
    session: Session, statement, keys: list[str]
) -> list[dict[str, Any]]:
    result = []
    for row in session.execute(statement).all():
        result.append({key: _cell(value) for key, value in zip(keys, row)})
    return result


def _attendance_summary(
    session: Session, target: str | None, start_date, end_date
) -> tuple[str, list[dict], list[dict]]:
    scope, class_id, grade_id = _target_scope(session, target)
    start, end = _date_range(session, start_date, end_date)
    filters = [Attendance.date.between(start, end)]
    if class_id is not None:
        filters.append(Attendance.class_id == class_id)
    if grade_id is not None:
        filters.append(Grade.id == grade_id)

    statement = (
        select(
            Class.name,
            func.count().label("应出勤"),
            func.sum(
                case((Attendance.status.in_(["正常", "迟到"]), 1), else_=0)
            ).label("出勤人次"),
            func.sum(case((Attendance.status == "迟到", 1), else_=0)).label(
                "迟到人次"
            ),
            func.sum(case((Attendance.status == "早退", 1), else_=0)).label(
                "早退人次"
            ),
            func.sum(case((Attendance.status == "缺勤", 1), else_=0)).label(
                "缺勤人次"
            ),
            func.sum(case((Attendance.status == "请假", 1), else_=0)).label(
                "请假人次"
            ),
            _round_ratio(
                func.sum(
                    case((Attendance.status.in_(["正常", "迟到"]), 1), else_=0)
                )
                * 100.0
                / func.count()
            ).label("出勤率"),
        )
        .join(Student, Attendance.student_id == Student.id)
        .join(Class, Attendance.class_id == Class.id)
        .join(Grade, Class.grade_id == Grade.id)
        .where(*filters)
        .group_by(Class.id, Class.name, Grade.sort)
        .order_by(Grade.sort, Class.name)
    )
    keys = [
        "班级",
        "应出勤",
        "出勤人次",
        "迟到人次",
        "早退人次",
        "缺勤人次",
        "请假人次",
        "出勤率",
    ]
    rows = _rows(session, statement, keys)
    columns = [
        ExportColumn(key=key, label=key)
        for key in keys
    ]
    title = f"{scope}出勤报表（{start.isoformat()} 至 {end.isoformat()}）"
    return title, columns, rows


def _leave_summary(
    session: Session, target: str | None, start_date, end_date
) -> tuple[str, list[dict], list[dict]]:
    scope, class_id, grade_id = _target_scope(session, target)
    start, end = _date_range(session, start_date, end_date)
    filters = [LeaveRequest.start_date.between(start, end)]
    if class_id is not None:
        filters.append(LeaveRequest.class_id == class_id)
    if grade_id is not None:
        filters.append(Grade.id == grade_id)

    statement = (
        select(
            Student.name,
            Class.name,
            LeaveRequest.leave_type,
            LeaveRequest.start_date,
            LeaveRequest.end_date,
            LeaveRequest.days,
            LeaveRequest.status,
            LeaveRequest.reason,
        )
        .join(Student, LeaveRequest.student_id == Student.id)
        .join(Class, LeaveRequest.class_id == Class.id)
        .join(Grade, Class.grade_id == Grade.id)
        .where(*filters)
        .order_by(LeaveRequest.start_date.desc(), LeaveRequest.id.desc())
    )
    keys = ["学生", "班级", "请假类型", "开始日期", "结束日期", "天数", "状态", "事由"]
    rows = _rows(session, statement, keys)
    columns = [ExportColumn(key=key, label=key) for key in keys]
    title = f"{scope}请假报表（{start.isoformat()} 至 {end.isoformat()}）"
    return title, columns, rows


def _student_roster(
    session: Session, target: str | None, start_date, end_date
) -> tuple[str, list[dict], list[dict]]:
    del start_date, end_date
    scope, class_id, grade_id = _target_scope(session, target)
    filters = []
    if class_id is not None:
        filters.append(Student.class_id == class_id)
    if grade_id is not None:
        filters.append(Grade.id == grade_id)
    statement = (
        select(
            Student.student_no,
            Student.name,
            Student.gender,
            Grade.name,
            Class.name,
            Student.status,
            Student.phone,
        )
        .join(Class, Student.class_id == Class.id)
        .join(Grade, Class.grade_id == Grade.id)
        .where(*filters)
        .order_by(Grade.sort, Class.name, Student.student_no)
    )
    keys = ["学号", "姓名", "性别", "年级", "班级", "状态", "联系电话"]
    rows = _rows(session, statement, keys)
    columns = [ExportColumn(key=key, label=key) for key in keys]
    return f"{scope}学生花名册", columns, rows


def _timetable(
    session: Session, target: str | None, start_date, end_date
) -> tuple[str, list[dict], list[dict]]:
    del start_date, end_date
    scope, class_id, grade_id = _target_scope(session, target)
    filters = []
    if class_id is not None:
        filters.append(Timetable.class_id == class_id)
    if grade_id is not None:
        filters.append(Grade.id == grade_id)
    statement = (
        select(
            Class.name,
            Timetable.weekday,
            Timetable.period,
            Timetable.subject,
            Teacher.name,
            Timetable.classroom,
            Timetable.semester,
            Timetable.start_time,
            Timetable.end_time,
        )
        .join(Class, Timetable.class_id == Class.id)
        .join(Grade, Class.grade_id == Grade.id)
        .join(Teacher, Timetable.teacher_id == Teacher.id)
        .where(*filters)
        .order_by(Class.name, Timetable.weekday, Timetable.period)
    )
    raw = _rows(
        session,
        statement,
        [
            "班级",
            "星期",
            "节次",
            "科目",
            "教师",
            "教室",
            "学期",
            "开始时间",
            "结束时间",
        ],
    )
    for row in raw:
        row["星期"] = WEEKDAY_LABELS.get(int(row["星期"]), str(row["星期"]))
    columns = [
        ExportColumn(key=key, label=key)
        for key in ["班级", "星期", "节次", "科目", "教师", "教室", "学期", "开始时间", "结束时间"]
    ]
    return f"{scope}课程表", columns, raw


def _grade_overview(
    session: Session, target: str | None, start_date, end_date
) -> tuple[str, list[dict], list[dict]]:
    scope, class_id, grade_id = _target_scope(session, target)
    start, end = _date_range(session, start_date, end_date)
    filters = [Attendance.date.between(start, end)]
    if class_id is not None:
        filters.append(Attendance.class_id == class_id)
    if grade_id is not None:
        filters.append(Grade.id == grade_id)
    attendance_subquery = (
        select(
            Grade.id.label("grade_id"),
            func.count(func.distinct(Class.id)).label("班级数"),
            func.count(func.distinct(Student.id)).label("学生人数"),
            func.sum(
                case((Attendance.status.in_(["正常", "迟到"]), 1), else_=0)
            ).label("出勤人次"),
            func.count().label("应出勤"),
        )
        .join(Class, Attendance.class_id == Class.id)
        .join(Grade, Class.grade_id == Grade.id)
        .join(Student, Attendance.student_id == Student.id)
        .where(*filters)
        .group_by(Grade.id, Grade.name, Grade.sort)
        .subquery()
    )
    statement = (
        select(
            Grade.name,
            attendance_subquery.c.班级数,
            attendance_subquery.c.学生人数,
            attendance_subquery.c.应出勤,
            attendance_subquery.c.出勤人次,
            _round_ratio(
                attendance_subquery.c.出勤人次
                * 100.0
                / attendance_subquery.c.应出勤
            ).label("出勤率"),
        )
        .join(attendance_subquery, attendance_subquery.c.grade_id == Grade.id)
        .order_by(Grade.sort)
    )
    keys = ["年级", "班级数", "学生人数", "应出勤", "出勤人次", "出勤率"]
    rows = _rows(session, statement, keys)
    columns = [ExportColumn(key=key, label=key) for key in keys]
    title = f"{scope}年级概况报表（{start.isoformat()} 至 {end.isoformat()}）"
    return title, columns, rows


def _columns(keys: list[str]) -> list[ExportColumn]:
    return [ExportColumn(key=key, label=key) for key in keys]


def _grade_overview_sheet(
    session: Session, grade_id: int | None = None
) -> tuple[list[ExportColumn], list[dict[str, Any]]]:
    """年级字典与班级 / 学生数量。"""
    statement = (
        select(
            Grade.name,
            Grade.level,
            Grade.sort,
            func.count(func.distinct(Class.id)).label("班级数"),
            func.count(func.distinct(Student.id)).label("学生人数"),
        )
        .select_from(Grade)
        .outerjoin(Class, Class.grade_id == Grade.id)
        .outerjoin(Student, Student.grade_id == Grade.id)
    )
    if grade_id is not None:
        statement = statement.where(Grade.id == grade_id)
    statement = statement.group_by(
        Grade.id, Grade.name, Grade.level, Grade.sort
    ).order_by(Grade.sort, Grade.name)
    keys = ["年级", "层级", "排序", "班级数", "学生人数"]
    return _columns(keys), _rows(session, statement, keys)


def _teacher_roster_sheet(
    session: Session,
) -> tuple[list[ExportColumn], list[dict[str, Any]]]:
    statement = (
        select(
            Teacher.teacher_no,
            Teacher.name,
            Teacher.gender,
            Teacher.subject,
            Teacher.title,
            Teacher.phone,
            Teacher.hire_date,
        )
        .select_from(Teacher)
        .order_by(Teacher.teacher_no)
    )
    keys = ["工号", "姓名", "性别", "学科", "职称", "电话", "入职日期"]
    return _columns(keys), _rows(session, statement, keys)


def _class_overview_sheet(
    session: Session,
    class_id: int | None = None,
    grade_id: int | None = None,
) -> tuple[list[ExportColumn], list[dict[str, Any]]]:
    """班级、班主任与在班学生人数。"""
    statement = (
        select(
            Grade.name,
            Class.name,
            Teacher.name,
            Class.classroom,
            func.count(Student.id).label("学生人数"),
        )
        .select_from(Class)
        .join(Grade, Class.grade_id == Grade.id)
        .outerjoin(Teacher, Class.head_teacher_id == Teacher.id)
        .outerjoin(Student, Student.class_id == Class.id)
    )
    if class_id is not None:
        statement = statement.where(Class.id == class_id)
    if grade_id is not None:
        statement = statement.where(Class.grade_id == grade_id)
    statement = statement.group_by(
        Class.id,
        Grade.name,
        Grade.sort,
        Class.name,
        Teacher.name,
        Class.classroom,
    ).order_by(Grade.sort, Class.name)
    keys = ["年级", "班级", "班主任", "教室", "学生人数"]
    return _columns(keys), _rows(session, statement, keys)


def _student_details_sheet(
    session: Session,
    class_id: int | None = None,
    grade_id: int | None = None,
) -> tuple[list[ExportColumn], list[dict[str, Any]]]:
    statement = (
        select(
            Student.student_no,
            Student.name,
            Student.gender,
            Student.birth_date,
            Grade.name,
            Class.name,
            Student.enrollment_date,
            Student.status,
            Student.phone,
            Student.address,
        )
        .select_from(Student)
        .join(Class, Student.class_id == Class.id)
        .join(Grade, Class.grade_id == Grade.id)
    )
    if class_id is not None:
        statement = statement.where(Student.class_id == class_id)
    if grade_id is not None:
        statement = statement.where(Student.grade_id == grade_id)
    statement = statement.order_by(
        Grade.sort, Class.name, Student.student_no
    )
    keys = [
        "学号",
        "姓名",
        "性别",
        "出生日期",
        "年级",
        "班级",
        "入学日期",
        "学籍状态",
        "联系电话",
        "家庭住址",
    ]
    return _columns(keys), _rows(session, statement, keys)


def _parent_contacts_sheet(
    session: Session,
    class_id: int | None = None,
    grade_id: int | None = None,
) -> tuple[list[ExportColumn], list[dict[str, Any]]]:
    statement = (
        select(
            Student.name,
            Class.name,
            Parent.name,
            Parent.relation,
            Parent.phone,
            Parent.occupation,
            Parent.is_primary,
        )
        .select_from(Parent)
        .join(Student, Parent.student_id == Student.id)
        .join(Class, Student.class_id == Class.id)
    )
    if class_id is not None:
        statement = statement.where(Student.class_id == class_id)
    if grade_id is not None:
        statement = statement.where(Student.grade_id == grade_id)
    statement = statement.order_by(
        Class.name, Student.student_no, Parent.id
    )
    keys = [
        "学生姓名",
        "班级",
        "家长姓名",
        "关系",
        "联系电话",
        "职业",
        "主要联系人",
    ]
    return _columns(keys), _rows(session, statement, keys)


def _campus_overview(
    session: Session,
    target: str | None,
    start_date: str | date | None,
    end_date: str | date | None,
) -> tuple[str, list[tuple[str, list[ExportColumn], list[dict[str, Any]]]]]:
    """8 张业务表各一个 Sheet 的校园数据总览模板报表。"""
    scope, class_id, grade_id = _target_scope(session, target)
    if class_id is not None and grade_id is None:
        grade_id = session.scalar(
            select(Class.grade_id).where(Class.id == class_id)
        )
    start, end = _date_range(session, start_date, end_date)

    grade_columns, grade_rows = _grade_overview_sheet(session, grade_id)
    teacher_columns, teacher_rows = _teacher_roster_sheet(session)
    class_columns, class_rows = _class_overview_sheet(
        session, class_id, grade_id
    )
    student_columns, student_rows = _student_details_sheet(
        session, class_id, grade_id
    )
    parent_columns, parent_rows = _parent_contacts_sheet(
        session, class_id, grade_id
    )
    _, attendance_columns, attendance_rows = _attendance_summary(
        session, target, start, end
    )
    _, leave_columns, leave_rows = _leave_summary(
        session, target, start, end
    )
    _, timetable_columns, timetable_rows = _timetable(
        session, target, None, None
    )

    sheets = [
        ("年级概况", grade_columns, grade_rows),
        ("教师名录", teacher_columns, teacher_rows),
        ("班级概况", class_columns, class_rows),
        ("学生花名册", student_columns, student_rows),
        ("家长联系表", parent_columns, parent_rows),
        ("出勤汇总", attendance_columns, attendance_rows),
        ("请假记录", leave_columns, leave_rows),
        ("课程表", timetable_columns, timetable_rows),
    ]
    title = f"{scope}校园数据总览报表（{start.isoformat()} 至 {end.isoformat()}）"
    return title, sheets


_BUILDERS = {
    "attendance_summary": _attendance_summary,
    "leave_summary": _leave_summary,
    "student_roster": _student_roster,
    "timetable": _timetable,
    "grade_overview": _grade_overview,
}


def generate_report(
    session: Session,
    *,
    report_type: str,
    target: str | None = None,
    start_date: str | date | None = None,
    end_date: str | date | None = None,
    output_dir: Path | None = None,
    title_suffix: str = "",
) -> dict[str, Any]:
    report_type = (report_type or "").strip()
    sheet_metadata: list[dict[str, Any]] = []
    if report_type == "campus_overview":
        title, sheets = _campus_overview(
            session, target, start_date, end_date
        )
        columns, rows = sheets[0][1], sheets[0][2]
        row_count = sum(len(sheet_rows) for _, _, sheet_rows in sheets)
        content = build_multi_sheet_xlsx(sheets)
        sheet_metadata = [
            {"name": name, "row_count": len(sheet_rows)}
            for name, _, sheet_rows in sheets
        ]
    else:
        builder = _BUILDERS.get(report_type)
        if builder is None:
            raise ReportError(
                "报表类型应为：attendance_summary、leave_summary、"
                "student_roster、timetable、grade_overview 或 campus_overview"
            )
        title, columns, rows = builder(
            session, target, start_date, end_date
        )
        row_count = len(rows)
        content = build_xlsx(columns, rows)
    if title_suffix:
        title = f"{title}{title_suffix}"

    report_id = uuid4().hex
    directory = Path(output_dir) if output_dir else REPORTS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{report_id}.xlsx"
    path.write_bytes(content)
    metadata = {
        "report_id": report_id,
        "title": title,
        "filename": f"{title}.xlsx",
        "report_type": report_type,
        "row_count": row_count,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    if sheet_metadata:
        metadata["sheets"] = sheet_metadata
    path.with_suffix(".json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        **metadata,
        "columns": [column.model_dump() for column in columns],
        "rows": rows,
        "file_path": str(path),
        "download_url": f"/api/reports/{report_id}/download",
    }


def resolve_report(report_id: str) -> tuple[Path, dict[str, Any]]:
    if not REPORT_ID_PATTERN.fullmatch(report_id or ""):
        raise ReportError("报表编号不合法")
    path = REPORTS_DIR / f"{report_id}.xlsx"
    if not path.exists():
        raise ReportError("报表不存在或已过期")
    metadata_path = path.with_suffix(".json")
    metadata: dict[str, Any] = {"report_id": report_id, "title": "校园报表"}
    if metadata_path.exists():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return path, metadata


# 保留一个显式的 Excel 样式构建入口，便于独立测试报表格式。
def build_report_workbook(columns: list[ExportColumn], rows: list[dict]) -> bytes:
    return build_xlsx(columns, rows)
