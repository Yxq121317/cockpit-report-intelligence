"""校园内部工具：Function Call 可调用的数据录入与报表生成。

所有写操作只落在既有 8 张业务表内；报表只生成文件，不新增数据表。
工具执行前做字段校验，执行后由调用方决定提交还是回滚。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

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

TOOL_LABELS = {
    "create_student": "新增学生",
    "create_parent": "新增家长",
    "record_attendance": "录入考勤",
    "create_leave_request": "提交请假申请",
    "create_timetable_entry": "录入课程表",
    "generate_report": "生成报表",
}

_WEEKDAY_LABELS = {1: "周一", 2: "周二", 3: "周三", 4: "周四", 5: "周五"}

_PERIOD_TIMES = {
    1: ("08:00", "08:45"),
    2: ("08:55", "09:40"),
    3: ("10:00", "10:45"),
    4: ("10:55", "11:40"),
    5: ("14:00", "14:45"),
    6: ("14:55", "15:40"),
}

_RELATION_MAP = {
    "爸爸": "父亲",
    "父亲": "父亲",
    "妈妈": "母亲",
    "母亲": "母亲",
    "爷爷": "祖父",
    "奶奶": "祖母",
    "外公": "外祖父",
    "外婆": "外祖母",
    "监护人": "其他",
}

ATTENDANCE_STATUSES = {"正常", "迟到", "早退", "缺勤", "请假"}
LEAVE_TYPES = {"病假", "事假", "其他"}
STUDENT_STATUSES = {"在读", "休学", "毕业"}
REPORT_TYPES = (
    "attendance_summary",
    "leave_summary",
    "student_roster",
    "timetable",
    "grade_overview",
    "campus_overview",
)


class ToolError(Exception):
    """工具参数或业务校验失败，可直接展示给用户。"""


@dataclass
class ToolOutcome:
    name: str
    label: str
    arguments: dict[str, Any]
    summary: str
    data: dict[str, Any]

    def public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "arguments": self.arguments,
            "status": "success",
            "summary": self.summary,
            "data": self.data,
        }


def _tool(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str],
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


def _string(description: str, *, enum: list[str] | None = None) -> dict:
    item: dict[str, Any] = {"type": "string", "description": description}
    if enum:
        item["enum"] = enum
    return item


def _integer(description: str, *, minimum: int, maximum: int) -> dict:
    return {
        "type": "integer",
        "description": description,
        "minimum": minimum,
        "maximum": maximum,
    }


TOOL_DEFINITIONS = [
    _tool(
        "create_student",
        "新增一名学生档案。必须提供学号、姓名、性别和班级名称。",
        {
            "student_no": _string("学号，必须唯一"),
            "name": _string("学生姓名"),
            "gender": _string("性别", enum=["男", "女"]),
            "class_name": _string("完整班级名，例如八年级一班"),
            "birth_date": _string("出生日期 YYYY-MM-DD，可省略"),
            "enrollment_date": _string("入学日期 YYYY-MM-DD，可省略"),
            "phone": _string("联系电话，可省略"),
            "address": _string("家庭住址，可省略"),
            "status": _string("学籍状态", enum=sorted(STUDENT_STATUSES)),
        },
        ["student_no", "name", "gender", "class_name"],
    ),
    _tool(
        "create_parent",
        "为已有学生新增一位家长或监护人联系方式。",
        {
            "student_name": _string("已有学生姓名"),
            "name": _string("家长姓名"),
            "relation": _string("与学生关系，例如父亲、母亲、祖母"),
            "phone": _string("家长联系电话"),
            "occupation": _string("职业，可省略"),
            "is_primary": {"type": "boolean", "description": "是否主要联系人"},
        },
        ["student_name", "name", "relation", "phone"],
    ),
    _tool(
        "record_attendance",
        "录入或更新某位学生某一天的考勤记录；同一学生同一天只保留一条。",
        {
            "student_name": _string("学生姓名"),
            "date": _string("日期 YYYY-MM-DD，省略时使用今天"),
            "status": _string("出勤状态", enum=sorted(ATTENDANCE_STATUSES)),
            "check_in_time": _string("到校时间 HH:MM，可省略"),
            "remark": _string("备注，可省略"),
        },
        ["student_name", "status"],
    ),
    _tool(
        "create_leave_request",
        "提交一条学生请假申请，初始状态为待审批。",
        {
            "student_name": _string("学生姓名"),
            "leave_type": _string("请假类型", enum=sorted(LEAVE_TYPES)),
            "start_date": _string("开始日期 YYYY-MM-DD"),
            "end_date": _string("结束日期 YYYY-MM-DD"),
            "reason": _string("请假事由，可省略"),
        },
        ["student_name", "leave_type", "start_date", "end_date"],
    ),
    _tool(
        "create_timetable_entry",
        "给某个班级新增一节课程安排；班级、星期、节次和学期组合必须唯一。",
        {
            "class_name": _string("完整班级名，例如八年级一班"),
            "weekday": _integer("星期，1 表示周一", minimum=1, maximum=5),
            "period": _integer("节次，1-6", minimum=1, maximum=6),
            "subject": _string("科目"),
            "teacher_name": _string("任课教师姓名"),
            "classroom": _string("教室，可省略"),
            "semester": _string("学期，可省略"),
            "start_time": _string("开始时间 HH:MM，可省略"),
            "end_time": _string("结束时间 HH:MM，可省略"),
        },
        ["class_name", "weekday", "period", "subject", "teacher_name"],
    ),
    _tool(
        "generate_report",
        "根据当前数据库生成可下载的 XLSX 报表，不写入数据库。",
        {
            "report_type": _string("报表类型", enum=list(REPORT_TYPES)),
            "target": _string("统计范围，例如八年级或八年级一班；省略为全校"),
            "start_date": _string("开始日期 YYYY-MM-DD，可省略"),
            "end_date": _string("结束日期 YYYY-MM-DD，可省略"),
        },
        ["report_type"],
    ),
]


def _require(arguments: dict[str, Any], key: str) -> Any:
    value = arguments.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ToolError(f"缺少必填参数：{key}")
    return value.strip() if isinstance(value, str) else value


def _optional_text(arguments: dict[str, Any], key: str, default: str = "") -> str:
    value = arguments.get(key)
    return str(value).strip() if value is not None else default


def _parse_date(value: Any, field: str) -> date:
    try:
        return date.fromisoformat(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ToolError(f"{field}格式应为 YYYY-MM-DD") from exc


def _find_class(session: Session, class_name: str) -> Class:
    klass = session.scalar(select(Class).where(Class.name == class_name.strip()))
    if klass is None:
        raise ToolError(f"没有找到班级：{class_name}")
    return klass


def _find_teacher(session: Session, teacher_name: str) -> Teacher:
    teachers = session.scalars(
        select(Teacher).where(Teacher.name == teacher_name.strip())
    ).all()
    if not teachers:
        raise ToolError(f"没有找到教师：{teacher_name}")
    if len(teachers) > 1:
        raise ToolError(f"存在多名同名教师：{teacher_name}")
    return teachers[0]


def _find_student(
    session: Session, student_name: str, class_name: str | None = None
) -> Student:
    statement = select(Student).where(Student.name == student_name.strip())
    if class_name:
        statement = statement.join(
            Class, Student.class_id == Class.id
        ).where(Class.name == class_name.strip())
    students = session.scalars(statement).all()
    if not students:
        raise ToolError(f"没有找到学生：{student_name}")
    if len(students) > 1:
        raise ToolError(
            f"存在 {len(students)} 名同名学生，请在问题中补充班级"
        )
    return students[0]


def _current_semester(session: Session) -> str:
    semester = session.scalar(
        select(Timetable.semester).order_by(Timetable.semester.desc()).limit(1)
    )
    return semester or "2026-2027学年第一学期"


def _business_date(session: Session) -> date:
    """与问数服务保持一致：优先使用最新考勤日期，避免示例数据错位。"""
    latest = session.scalar(select(func.max(Attendance.date)))
    if isinstance(latest, datetime):
        latest = latest.date()
    return latest or date.today()


def _create_student(session: Session, arguments: dict[str, Any]) -> ToolOutcome:
    student_no = str(_require(arguments, "student_no"))
    name = str(_require(arguments, "name"))
    gender = str(_require(arguments, "gender"))
    class_name = str(_require(arguments, "class_name"))
    if gender not in {"男", "女"}:
        raise ToolError("性别只能是男或女")
    status = _optional_text(arguments, "status", "在读")
    if status not in STUDENT_STATUSES:
        raise ToolError("学籍状态应为在读、休学或毕业")
    if session.scalar(
        select(Student.id).where(Student.student_no == student_no)
    ):
        raise ToolError(f"学号已存在：{student_no}")
    klass = _find_class(session, class_name)
    student = Student(
        student_no=student_no,
        name=name,
        gender=gender,
        birth_date=_parse_date(
            _optional_text(arguments, "birth_date", "2012-01-01"),
            "出生日期",
        ),
        grade_id=klass.grade_id,
        class_id=klass.id,
        enrollment_date=_parse_date(
            _optional_text(arguments, "enrollment_date", date.today().isoformat()),
            "入学日期",
        ),
        status=status,
        phone=_optional_text(arguments, "phone"),
        address=_optional_text(arguments, "address"),
    )
    session.add(student)
    session.flush()
    return ToolOutcome(
        name="create_student",
        label=TOOL_LABELS["create_student"],
        arguments=arguments,
        summary=f"新增学生 {name}（学号 {student_no}，{klass.name}）",
        data={
            "id": student.id,
            "student_no": student_no,
            "name": name,
            "gender": gender,
            "class_name": klass.name,
            "status": status,
        },
    )


def _create_parent(session: Session, arguments: dict[str, Any]) -> ToolOutcome:
    student_name = str(_require(arguments, "student_name"))
    name = str(_require(arguments, "name"))
    relation = str(_require(arguments, "relation"))
    phone = str(_require(arguments, "phone"))
    relation = _RELATION_MAP.get(relation, relation)
    if not re.fullmatch(r"1[3-9]\d{9}", phone):
        raise ToolError("家长联系电话应为 11 位手机号")
    student = _find_student(session, student_name)
    if session.scalar(
        select(Parent.id).where(
            Parent.student_id == student.id, Parent.phone == phone
        )
    ):
        raise ToolError(f"{student.name} 已存在该联系电话的家长")
    parent = Parent(
        student_id=student.id,
        name=name,
        relation=relation,
        phone=phone,
        occupation=_optional_text(arguments, "occupation"),
        is_primary=bool(arguments.get("is_primary", False)),
    )
    session.add(parent)
    session.flush()
    return ToolOutcome(
        name="create_parent",
        label=TOOL_LABELS["create_parent"],
        arguments=arguments,
        summary=f"为 {student.name} 新增家长 {name}（{relation}，{phone}）",
        data={
            "id": parent.id,
            "student_name": student.name,
            "name": name,
            "relation": relation,
            "phone": phone,
        },
    )


def _record_attendance(
    session: Session, arguments: dict[str, Any]
) -> ToolOutcome:
    student_name = str(_require(arguments, "student_name"))
    status = str(_require(arguments, "status"))
    if status not in ATTENDANCE_STATUSES:
        raise ToolError("出勤状态应为正常、迟到、早退、缺勤或请假")
    student = _find_student(session, student_name)
    attendance_date = _parse_date(
        _optional_text(arguments, "date", _business_date(session).isoformat()),
        "考勤日期",
    )
    check_in_time = _optional_text(arguments, "check_in_time")
    if check_in_time and not re.fullmatch(r"\d{2}:\d{2}", check_in_time):
        raise ToolError("到校时间格式应为 HH:MM")
    if not check_in_time:
        check_in_time = "08:00" if status == "正常" else "08:30" if status == "迟到" else ""

    existing = session.scalar(
        select(Attendance).where(
            Attendance.student_id == student.id,
            Attendance.date == attendance_date,
        )
    )
    if existing is not None:
        existing.status = status
        existing.check_in_time = check_in_time or None
        existing.remark = _optional_text(arguments, "remark")
        session.flush()
        created = False
        record_id = existing.id
        action = "更新"
    else:
        record = Attendance(
            student_id=student.id,
            class_id=student.class_id,
            date=attendance_date,
            status=status,
            check_in_time=check_in_time or None,
            remark=_optional_text(arguments, "remark"),
        )
        session.add(record)
        session.flush()
        created = True
        record_id = record.id
        action = "新增"
    return ToolOutcome(
        name="record_attendance",
        label=TOOL_LABELS["record_attendance"],
        arguments=arguments,
        summary=(
            f"{action} {student.name} 的考勤：{attendance_date.isoformat()} "
            f"{status}"
        ),
        data={
            "id": record_id,
            "student_name": student.name,
            "date": attendance_date.isoformat(),
            "status": status,
            "created": created,
        },
    )


def _create_leave_request(
    session: Session, arguments: dict[str, Any]
) -> ToolOutcome:
    student_name = str(_require(arguments, "student_name"))
    leave_type = str(_require(arguments, "leave_type"))
    if leave_type not in LEAVE_TYPES:
        raise ToolError("请假类型应为病假、事假或其他")
    start = _parse_date(_require(arguments, "start_date"), "开始日期")
    end = _parse_date(_require(arguments, "end_date"), "结束日期")
    if end < start:
        raise ToolError("结束日期不能早于开始日期")
    student = _find_student(session, student_name)
    days = (end - start).days + 1
    leave = LeaveRequest(
        student_id=student.id,
        class_id=student.class_id,
        leave_type=leave_type,
        start_date=start,
        end_date=end,
        days=days,
        reason=_optional_text(arguments, "reason"),
        status="待审批",
        applied_at=datetime.now(),
    )
    session.add(leave)
    session.flush()
    return ToolOutcome(
        name="create_leave_request",
        label=TOOL_LABELS["create_leave_request"],
        arguments=arguments,
        summary=(
            f"为 {student.name} 提交{leave_type}申请："
            f"{start.isoformat()} 至 {end.isoformat()}，共 {days} 天"
        ),
        data={
            "id": leave.id,
            "student_name": student.name,
            "leave_type": leave_type,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "days": days,
            "status": "待审批",
        },
    )


def _create_timetable_entry(
    session: Session, arguments: dict[str, Any]
) -> ToolOutcome:
    class_name = str(_require(arguments, "class_name"))
    weekday = int(_require(arguments, "weekday"))
    period = int(_require(arguments, "period"))
    subject = str(_require(arguments, "subject"))
    teacher_name = str(_require(arguments, "teacher_name"))
    if not 1 <= weekday <= 5:
        raise ToolError("星期应为 1-5")
    if not 1 <= period <= 6:
        raise ToolError("节次应为 1-6")
    klass = _find_class(session, class_name)
    teacher = _find_teacher(session, teacher_name)
    semester = _optional_text(
        arguments, "semester", _current_semester(session)
    )
    if session.scalar(
        select(Timetable.id).where(
            Timetable.class_id == klass.id,
            Timetable.weekday == weekday,
            Timetable.period == period,
            Timetable.semester == semester,
        )
    ):
        raise ToolError(
            f"{klass.name} {_WEEKDAY_LABELS[weekday]}第 {period} 节已有课程"
        )
    default_start, default_end = _PERIOD_TIMES[period]
    start_time = _optional_text(arguments, "start_time", default_start)
    end_time = _optional_text(arguments, "end_time", default_end)
    for value, field in ((start_time, "开始时间"), (end_time, "结束时间")):
        if not re.fullmatch(r"\d{2}:\d{2}", value):
            raise ToolError(f"{field}格式应为 HH:MM")
    entry = Timetable(
        class_id=klass.id,
        weekday=weekday,
        period=period,
        subject=subject,
        teacher_id=teacher.id,
        classroom=_optional_text(arguments, "classroom", klass.classroom),
        semester=semester,
        start_time=start_time,
        end_time=end_time,
    )
    session.add(entry)
    session.flush()
    return ToolOutcome(
        name="create_timetable_entry",
        label=TOOL_LABELS["create_timetable_entry"],
        arguments=arguments,
        summary=(
            f"为 {klass.name} 新增 {_WEEKDAY_LABELS[weekday]}第 {period} 节"
            f"{subject}，任课教师 {teacher.name}"
        ),
        data={
            "id": entry.id,
            "class_name": klass.name,
            "weekday": weekday,
            "period": period,
            "subject": subject,
            "teacher_name": teacher.name,
            "semester": semester,
        },
    )


def _generate_report(session: Session, arguments: dict[str, Any]) -> ToolOutcome:
    from app.services.report import ReportError, generate_report

    report_type = str(_require(arguments, "report_type"))
    try:
        report = generate_report(
            session,
            report_type=report_type,
            target=_optional_text(arguments, "target") or None,
            start_date=_optional_text(arguments, "start_date") or None,
            end_date=_optional_text(arguments, "end_date") or None,
        )
    except ReportError as exc:
        raise ToolError(str(exc)) from exc
    summary = f"生成{report['title']}，共 {report['row_count']} 行"
    return ToolOutcome(
        name="generate_report",
        label=TOOL_LABELS["generate_report"],
        arguments=arguments,
        summary=summary,
        data={
            "report_id": report["report_id"],
            "title": report["title"],
            "filename": report["filename"],
            "download_url": report["download_url"],
            "row_count": report["row_count"],
            "columns": report["columns"],
            "rows": report["rows"],
            "sheets": report.get("sheets", []),
            "file_path": report["file_path"],
        },
    )


_HANDLERS: dict[str, Callable[[Session, dict[str, Any]], ToolOutcome]] = {
    "create_student": _create_student,
    "create_parent": _create_parent,
    "record_attendance": _record_attendance,
    "create_leave_request": _create_leave_request,
    "create_timetable_entry": _create_timetable_entry,
    "generate_report": _generate_report,
}


def parse_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ToolError("工具参数不是合法 JSON") from exc
        if isinstance(parsed, dict):
            return parsed
    raise ToolError("工具参数必须是对象")


def execute_tool(
    session: Session,
    name: str,
    arguments: dict[str, Any] | str,
    *,
    commit: bool = True,
) -> ToolOutcome:
    handler = _HANDLERS.get(name)
    if handler is None:
        raise ToolError(f"不支持的工具：{name}")
    parsed = parse_arguments(arguments)
    try:
        outcome = handler(session, parsed)
        if commit:
            session.commit()
        return outcome
    except Exception:
        if commit:
            session.rollback()
        raise


def tool_catalog() -> list[dict[str, Any]]:
    return [
        {
            "name": definition["function"]["name"],
            "label": TOOL_LABELS[definition["function"]["name"]],
            "description": definition["function"]["description"],
        }
        for definition in TOOL_DEFINITIONS
    ]
