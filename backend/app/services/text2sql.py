"""自然语言查数：mock 模板、LLM 生成、只读执行与中文回答。

业务范围严格限定为 8 张校园表：
grades / teachers / classes / students / parents / attendance /
leave_requests / timetable。
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from datetime import date, datetime, timedelta
from datetime import time as dt_time
from decimal import Decimal
from typing import Callable, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import observability
from app.core.config import settings
from app.core.db import engine
from app.core.llm import LLMGateway, get_gateway
from app.core.prompts import render_prompt
from app.core.schema_retrieval import retrieve_tables
from app.core.sql_guard import (
    MAX_ROWS,
    TABLE_COLUMNS,
    SqlExecutionError,
    SqlValidationError,
    validate_sql,
)
from app.models import Attendance, Class, Student, Teacher
from app.services import evaluation

PROMPT_FILE_VERSION = "v2"
QUERY_TIMEOUT_SECONDS = 3.0

CLARIFICATION = (
    "我还没有理解你要查哪类数据。可以试试：上周八年级各班出勤率、"
    "最近两周迟到次数最多的班级、本周待审批请假有多少、"
    "八年级二班本周的课程表、李雷的家长联系方式、各年级学生人数、"
    "八年级一班班主任是谁、本学期老师课时排名。"
)

EXAMPLES = [
    {"question": "上周八年级各班出勤率", "category": "出勤"},
    {"question": "生成校园数据总览模板报表", "category": "报表"},
    {"question": "最近两周迟到次数最多的班级", "category": "出勤"},
    {"question": "张小明最近出勤情况", "category": "出勤"},
    {"question": "本周待审批请假有多少", "category": "请假"},
    {"question": "本周请假名单", "category": "请假"},
    {"question": "八年级二班本周的课程表", "category": "课程"},
    {"question": "李雷的家长联系方式", "category": "家长"},
    {"question": "各年级学生人数", "category": "学生"},
    {"question": "八年级一班有多少学生", "category": "学生"},
    {"question": "八年级一班班主任是谁", "category": "教师"},
    {"question": "本学期老师课时排名", "category": "教师"},
]

TABLE_META = [
    {
        "name": "grades",
        "label": "年级",
        "description": "学校的年级字典，例如七年级、八年级、九年级。",
        "columns": [
            {"name": "id", "label": "年级编号"},
            {"name": "name", "label": "年级名称"},
            {"name": "level", "label": "年级层级"},
            {"name": "sort", "label": "排序"},
        ],
    },
    {
        "name": "teachers",
        "label": "教师",
        "description": "教师档案，包含任教学科、职称与联系方式。",
        "columns": [
            {"name": "id", "label": "教师编号"},
            {"name": "teacher_no", "label": "工号"},
            {"name": "name", "label": "姓名"},
            {"name": "gender", "label": "性别"},
            {"name": "subject", "label": "任教学科"},
            {"name": "title", "label": "职称"},
            {"name": "phone", "label": "联系电话"},
            {"name": "hire_date", "label": "入职日期"},
        ],
    },
    {
        "name": "classes",
        "label": "班级",
        "description": "班级档案，关联年级和班主任。",
        "columns": [
            {"name": "id", "label": "班级编号"},
            {"name": "grade_id", "label": "年级编号"},
            {"name": "name", "label": "班级名称"},
            {"name": "head_teacher_id", "label": "班主任编号"},
            {"name": "classroom", "label": "教室"},
        ],
    },
    {
        "name": "students",
        "label": "学生",
        "description": "学生档案，含学号、性别、班级与在读状态。",
        "columns": [
            {"name": "id", "label": "学生编号"},
            {"name": "student_no", "label": "学号"},
            {"name": "name", "label": "姓名"},
            {"name": "gender", "label": "性别"},
            {"name": "birth_date", "label": "出生日期"},
            {"name": "grade_id", "label": "年级编号"},
            {"name": "class_id", "label": "班级编号"},
            {"name": "enrollment_date", "label": "入学日期"},
            {"name": "status", "label": "状态"},
            {"name": "phone", "label": "联系电话"},
            {"name": "address", "label": "家庭住址"},
        ],
    },
    {
        "name": "parents",
        "label": "家长",
        "description": "家长或监护人，一名学生可以有多位家长。",
        "columns": [
            {"name": "id", "label": "家长编号"},
            {"name": "student_id", "label": "学生编号"},
            {"name": "name", "label": "姓名"},
            {"name": "relation", "label": "与学生关系"},
            {"name": "phone", "label": "联系电话"},
            {"name": "occupation", "label": "职业"},
            {"name": "is_primary", "label": "是否主要联系人"},
        ],
    },
    {
        "name": "attendance",
        "label": "出勤",
        "description": "每日出勤记录，一名学生一天一条；状态含正常、迟到、早退、缺勤、请假。",
        "columns": [
            {"name": "id", "label": "记录编号"},
            {"name": "student_id", "label": "学生编号"},
            {"name": "class_id", "label": "班级编号"},
            {"name": "date", "label": "日期"},
            {"name": "status", "label": "出勤状态"},
            {"name": "check_in_time", "label": "到校时间"},
            {"name": "remark", "label": "备注"},
        ],
    },
    {
        "name": "leave_requests",
        "label": "请假",
        "description": "请假申请与审批结果；状态含待审批、已批准、已驳回。",
        "columns": [
            {"name": "id", "label": "申请编号"},
            {"name": "student_id", "label": "学生编号"},
            {"name": "class_id", "label": "班级编号"},
            {"name": "leave_type", "label": "请假类型"},
            {"name": "start_date", "label": "开始日期"},
            {"name": "end_date", "label": "结束日期"},
            {"name": "days", "label": "请假天数"},
            {"name": "reason", "label": "请假事由"},
            {"name": "status", "label": "审批状态"},
            {"name": "applied_at", "label": "申请时间"},
            {"name": "approver_teacher_id", "label": "审批教师编号"},
            {"name": "approved_at", "label": "审批时间"},
            {"name": "remark", "label": "备注"},
        ],
    },
    {
        "name": "timetable",
        "label": "课程表",
        "description": "班级课程表，班级 + 星期 + 节次唯一。",
        "columns": [
            {"name": "id", "label": "课程编号"},
            {"name": "class_id", "label": "班级编号"},
            {"name": "weekday", "label": "星期（1-5）"},
            {"name": "period", "label": "节次"},
            {"name": "subject", "label": "科目"},
            {"name": "teacher_id", "label": "任课教师编号"},
            {"name": "classroom", "label": "教室"},
            {"name": "semester", "label": "学期"},
            {"name": "start_time", "label": "开始时间"},
            {"name": "end_time", "label": "结束时间"},
        ],
    },
]

_CLASS_RE = re.compile(r"([一二三四五六七八九])年级([一二三四五六七八九])班")
_GRADE_RE = re.compile(r"([一二三四五六七八九])年级")
_WEEKDAY_LABELS = {
    "周一": 1,
    "星期一": 1,
    "周二": 2,
    "星期二": 2,
    "周三": 3,
    "星期三": 3,
    "周四": 4,
    "星期四": 4,
    "周五": 5,
    "星期五": 5,
}
_LEAVE_MARKERS = ("请假", "病假", "事假", "休假")
_COUNT_MARKERS = ("多少", "几", "数量", "总数", "统计", "人数")
_LIST_MARKERS = ("名单", "哪些", "谁", "记录", "明细", "列表")


def _lit(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _fmt(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float):
        if value == int(value):
            return str(int(value))
        return f"{value:.1f}"
    return str(value)


def _column(key: str, label: str | None = None, kind: str | None = None) -> dict:
    item = {"key": key, "label": label or key}
    if kind:
        item["type"] = kind
    return item


def get_meta() -> dict:
    """前端表结构面板与示例问题。"""
    gateway = get_gateway()
    return {
        "database": (
            "PostgreSQL" if engine.dialect.name == "postgresql" else "SQLite"
        ),
        "table_count": len(TABLE_META),
        "max_rows": MAX_ROWS,
        "llm_mode": gateway.effective_mode,
        "llm_degraded": gateway.degraded,
        "tables": TABLE_META,
        "examples": EXAMPLES,
    }


def resolve_as_of(session: Session) -> date:
    """以库内最新考勤日期作为“今天”，保证演示数据与问题口径一致。"""
    latest = session.scalar(select(func.max(Attendance.date)))
    if isinstance(latest, datetime):
        latest = latest.date()
    return latest or date.today()


def _find_student(session: Session, question: str) -> str | None:
    names = session.scalars(select(Student.name).distinct()).all()
    for name in sorted(names, key=len, reverse=True):
        if name and name in question:
            return name
    return None


def _find_teacher(session: Session, question: str) -> str | None:
    names = session.scalars(select(Teacher.name).distinct()).all()
    for name in sorted(names, key=len, reverse=True):
        if name and name in question:
            return name
    return None


def _scope(question: str) -> tuple[str | None, str | None]:
    match = _CLASS_RE.search(question)
    if match:
        grade = f"{match.group(1)}年级"
        return grade, f"{grade}{match.group(2)}班"
    match = _GRADE_RE.search(question)
    if match:
        return f"{match.group(1)}年级", None
    return None, None


def _period_bounds(question: str, as_of: date) -> tuple[date, date, str]:
    monday = as_of - timedelta(days=as_of.weekday())
    if "上周" in question:
        return monday - timedelta(days=7), monday - timedelta(days=1), "上周"
    if "本周" in question:
        return monday, as_of, "本周"
    if "本月" in question:
        return as_of.replace(day=1), as_of, "本月"
    if "今天" in question:
        return as_of, as_of, "今天"
    if "昨天" in question:
        yesterday = as_of - timedelta(days=1)
        return yesterday, yesterday, "昨天"
    if "最近两周" in question or "近两周" in question:
        return as_of - timedelta(days=13), as_of, "最近两周"
    if "最近7天" in question or "近7天" in question:
        return as_of - timedelta(days=6), as_of, "最近 7 天"
    return as_of - timedelta(days=29), as_of, "近 30 天"


def _weekday(question: str) -> int | None:
    for label, value in _WEEKDAY_LABELS.items():
        if label in question:
            return value
    return None


def _class_id_by_name(session: Session, class_name: str) -> int | None:
    return session.scalar(select(Class.id).where(Class.name == class_name))


def _class_name_by_student(session: Session, student_name: str) -> str | None:
    return session.scalar(
        select(Class.name)
        .join(Student, Student.class_id == Class.id)
        .where(Student.name == student_name)
    )


def _attendance_answer(rows: list[dict], period_label: str, entity: str) -> str:
    if not rows:
        return f"{period_label}没有查询到符合条件的出勤记录。"
    if len(rows) == 1:
        row = rows[0]
        who = row.get("学生") or row.get("班级") or row.get("年级") or "查询范围"
        return (
            f"{period_label}{who}的出勤率为 {_fmt(row.get('出勤率'))}%："
            f"应出勤 {_fmt(row.get('应出勤'))} 人次，"
            f"实际出勤 {_fmt(row.get('出勤人次'))} 人次，"
            f"其中迟到 {_fmt(row.get('迟到人次'))} 人次、"
            f"缺勤 {_fmt(row.get('缺勤人次'))} 人次、"
            f"请假 {_fmt(row.get('请假人次'))} 人次。"
        )
    best = max(rows, key=lambda item: item.get("出勤率") or 0)
    worst = min(rows, key=lambda item: item.get("出勤率") or 0)
    return (
        f"{period_label}共统计 {len(rows)} 个{entity}。"
        f"出勤率最高的是 {best.get(entity) or best.get('班级') or best.get('年级')}"
        f"（{_fmt(best.get('出勤率'))}%），"
        f"最低的是 {worst.get(entity) or worst.get('班级') or worst.get('年级')}"
        f"（{_fmt(worst.get('出勤率'))}%）。"
    )


def _late_answer(rows: list[dict], period_label: str) -> str:
    if not rows:
        return f"{period_label}没有查询到迟到记录。"
    top = rows[0]
    return (
        f"{period_label}迟到次数最多的是 {top.get('班级') or top.get('年级')}，"
        f"共 {_fmt(top.get('迟到次数'))} 次；"
        f"本次共统计 {len(rows)} 个班级。"
    )


def _student_status_answer(rows: list[dict], period_label: str, student: str) -> str:
    if not rows:
        return f"{period_label}没有查询到 {student} 的考勤记录。"
    parts = [f"{row.get('状态')} {_fmt(row.get('次数'))} 次" for row in rows]
    return f"{period_label}{student}的考勤情况：" + "，".join(parts) + "。"


def _timetable_answer(rows: list[dict], class_name: str | None) -> str:
    if not rows:
        return "没有查询到符合条件的课程安排。"
    scope = class_name or rows[0].get("班级") or "查询范围"
    first = rows[0]
    return (
        f"{scope}本学期共安排 {len(rows)} 节课。"
        f"第一节是{first.get('星期')}第 {_fmt(first.get('节次'))} 节"
        f"{first.get('科目')}，任课教师 {first.get('教师')}，"
        f"教室 {first.get('教室')}。"
    )


def _parent_answer(rows: list[dict], student: str | None) -> str:
    if not rows:
        return "没有查询到符合条件的家长信息。"
    if student and len(rows) <= 3:
        parts = [
            f"{row.get('家长')}（{row.get('关系')}，{row.get('联系电话')}）"
            for row in rows
        ]
        return f"{student}的家长联系方式：" + "；".join(parts) + "。"
    first = rows[0]
    return (
        f"共查询到 {len(rows)} 位家长信息。"
        f"第一条是 {first.get('学生')} 的{first.get('关系')}"
        f"{first.get('家长')}，联系电话 {first.get('联系电话')}。"
    )


def _leave_count_answer(rows: list[dict], status_label: str, period_label: str) -> str:
    if not rows:
        return "没有查询到符合条件的请假申请。"
    row = rows[0]
    count = next(
        (value for key, value in row.items() if key not in {"状态", "请假类型", "班级"}),
        0,
    )
    return f"{period_label}共有 {_fmt(count)} 条{status_label}请假申请。"


def _leave_list_answer(rows: list[dict], period_label: str) -> str:
    if not rows:
        return f"{period_label}没有查询到请假记录。"
    first = rows[0]
    return (
        f"{period_label}共查询到 {len(rows)} 条请假记录。"
        f"最近一条是 {first.get('学生')}（{first.get('班级')}）的"
        f"{first.get('请假类型')}，{first.get('开始日期')} 至 "
        f"{first.get('结束日期')}，状态为{first.get('状态')}。"
    )


def _leave_type_answer(rows: list[dict]) -> str:
    if not rows:
        return "没有查询到请假类型统计。"
    top = rows[0]
    return (
        f"请假申请最多的是{top.get('请假类型')}，共 {_fmt(top.get('申请数'))} 条；"
        f"共统计 {len(rows)} 种类型。"
    )


def _leave_class_answer(rows: list[dict]) -> str:
    if not rows:
        return "没有查询到班级请假统计。"
    top = rows[0]
    return (
        f"请假次数最多的班级是 {top.get('班级')}，"
        f"共 {_fmt(top.get('请假次数'))} 次。"
    )


def _workload_answer(rows: list[dict]) -> str:
    if not rows:
        return "没有查询到教师课时数据。"
    top = rows[0]
    return (
        f"课时最多的是 {top.get('教师')}（{top.get('学科')}），"
        f"共 {_fmt(top.get('课时数'))} 课时，"
        f"任教 {_fmt(top.get('任教班级数'))} 个班级。"
    )


def _head_teacher_answer(rows: list[dict]) -> str:
    if not rows:
        return "没有查询到班主任信息。"
    row = rows[0]
    return (
        f"{row.get('班级')}的班主任是 {row.get('班主任')}，"
        f"任教{row.get('学科')}，联系电话 {row.get('联系电话')}。"
    )


def _student_info_answer(rows: list[dict]) -> str:
    if not rows:
        return "没有查询到学生档案。"
    row = rows[0]
    return (
        f"{row.get('学生')}，学号 {row.get('学号')}，"
        f"{row.get('年级')}{row.get('班级')}，性别{row.get('性别')}，"
        f"出生日期 {row.get('出生日期')}，当前状态为{row.get('状态')}。"
    )


def _teacher_info_answer(rows: list[dict]) -> str:
    if not rows:
        return "没有查询到教师档案。"
    row = rows[0]
    return (
        f"{row.get('教师')}，工号 {row.get('工号')}，"
        f"任教{row.get('学科')}，职称为{row.get('职称')}，"
        f"联系电话 {row.get('联系电话')}。"
    )


def _count_total_answer(rows: list[dict], label: str) -> str:
    if not rows:
        return "没有查询到统计数据。"
    value = next(iter(rows[0].values()), 0)
    return f"当前共有 {_fmt(value)} {label}。"


def _group_count_answer(
    rows: list[dict], dimension: str, measure: str, unit: str
) -> str:
    if not rows:
        return "没有查询到统计数据。"
    top = rows[0]
    return (
        f"共统计 {len(rows)} 个{dimension}。"
        f"{measure}最多的是 {top.get(dimension)}，"
        f"共 {_fmt(top.get(measure))} {unit}。"
    )


def _gender_answer(rows: list[dict]) -> str:
    if not rows:
        return "没有查询到性别统计。"
    parts = [f"{row.get('性别')} {_fmt(row.get('人数'))} 人" for row in rows]
    return "学生性别分布：" + "，".join(parts) + "。"


def _attendance_filters(
    grade_name: str | None, class_name: str | None, student: str | None
) -> str:
    filters = ""
    if grade_name:
        filters += f" AND g.name = {_lit(grade_name)}"
    if class_name:
        filters += f" AND c.name = {_lit(class_name)}"
    if student:
        filters += f" AND s.name = {_lit(student)}"
    return filters


def _attendance_template(
    session: Session,
    question: str,
    as_of: date,
    *,
    student: str | None,
    grade_name: str | None,
    class_name: str | None,
) -> dict:
    start, end, period_label = _period_bounds(question, as_of)
    if student and "出勤率" not in question and any(
        marker in question for marker in ("迟到", "缺勤", "早退", "考勤")
    ) and "情况" not in question and "怎么样" not in question:
        sql = (
            "SELECT a.status AS 状态, COUNT(*) AS 次数 "
            "FROM attendance AS a "
            "JOIN students AS s ON a.student_id = s.id "
            "JOIN classes AS c ON a.class_id = c.id "
            "JOIN grades AS g ON c.grade_id = g.id "
            f"WHERE a.date BETWEEN {_lit(start.isoformat())} AND "
            f"{_lit(end.isoformat())}{_attendance_filters(grade_name, class_name, student)} "
            "GROUP BY a.status ORDER BY 次数 DESC"
        )
        return {
            "sql": sql,
            "explanation": (
                f"统计 {start.isoformat()} 至 {end.isoformat()} 期间"
                f"{student}各考勤状态的出现次数。"
            ),
            "title": f"{period_label}{student}考勤状态",
            "chart": "table",
            "answer": lambda rows: _student_status_answer(rows, period_label, student),
        }

    group_by_grade = (
        "各年级" in question
        or "全校" in question
        or (
            bool(grade_name)
            and not class_name
            and "各班" not in question
            and "班级" not in question
        )
    )
    select_parts: list[str] = []
    group_parts: list[str] = []
    order_parts: list[str] = []
    if student:
        select_parts = ["s.name AS 学生", "c.name AS 班级", "g.name AS 年级"]
        group_parts = ["s.id", "s.name", "c.id", "c.name", "g.id", "g.name", "g.sort"]
        order_parts = ["s.name"]
        entity = "学生"
    elif group_by_grade:
        select_parts = ["g.name AS 年级"]
        group_parts = ["g.id", "g.name", "g.sort"]
        order_parts = ["g.sort"]
        entity = "年级"
    else:
        select_parts = ["g.name AS 年级", "c.name AS 班级"]
        group_parts = ["g.id", "g.name", "g.sort", "c.id", "c.name"]
        order_parts = ["g.sort", "c.name"]
        entity = "班级"
    select_parts.extend(
        [
            "COUNT(*) AS 应出勤",
            "SUM(CASE WHEN a.status IN ('正常','迟到') THEN 1 ELSE 0 END) AS 出勤人次",
            "SUM(CASE WHEN a.status = '迟到' THEN 1 ELSE 0 END) AS 迟到人次",
            "SUM(CASE WHEN a.status = '早退' THEN 1 ELSE 0 END) AS 早退人次",
            "SUM(CASE WHEN a.status = '缺勤' THEN 1 ELSE 0 END) AS 缺勤人次",
            "SUM(CASE WHEN a.status = '请假' THEN 1 ELSE 0 END) AS 请假人次",
            "ROUND(SUM(CASE WHEN a.status IN ('正常','迟到') THEN 1 ELSE 0 END) "
            "* 100.0 / COUNT(*), 1) AS 出勤率",
        ]
    )
    student_filter = f" AND s.name = {_lit(student)}" if student else ""
    sql = (
        "SELECT " + ", ".join(select_parts) + " "
        "FROM attendance AS a "
        "JOIN students AS s ON a.student_id = s.id "
        "JOIN classes AS c ON a.class_id = c.id "
        "JOIN grades AS g ON c.grade_id = g.id "
        f"WHERE a.date BETWEEN {_lit(start.isoformat())} AND "
        f"{_lit(end.isoformat())}"
        f"{_attendance_filters(grade_name, class_name, None)}"
        f"{student_filter} "
        f"GROUP BY {', '.join(group_parts)} "
        f"ORDER BY {', '.join(order_parts)}"
    )
    who = student or class_name or grade_name or "各班级"
    return {
        "sql": sql,
        "explanation": (
            f"按 {start.isoformat()} 至 {end.isoformat()} 的日常考勤统计"
            f"{period_label}出勤情况，出勤率口径为（正常 + 迟到）/ 应出勤。"
        ),
        "title": f"{period_label}{who}出勤率",
        "chart": "bar",
        "answer": lambda rows: _attendance_answer(rows, period_label, entity),
    }


def _late_template(
    session: Session,
    question: str,
    as_of: date,
    *,
    grade_name: str | None,
    class_name: str | None,
    student: str | None,
) -> dict:
    start, end, period_label = _period_bounds(question, as_of)
    if student:
        return _attendance_template(
            session,
            question,
            as_of,
            student=student,
            grade_name=grade_name,
            class_name=class_name,
        )
    filters = _attendance_filters(grade_name, class_name, None)
    sql = (
        "SELECT c.name AS 班级, g.name AS 年级, COUNT(*) AS 迟到次数 "
        "FROM attendance AS a "
        "JOIN classes AS c ON a.class_id = c.id "
        "JOIN grades AS g ON c.grade_id = g.id "
        "WHERE a.status = '迟到' "
        f"AND a.date BETWEEN {_lit(start.isoformat())} AND "
        f"{_lit(end.isoformat())}{filters} "
        "GROUP BY c.id, c.name, g.name, g.sort "
        "ORDER BY 迟到次数 DESC, c.name"
    )
    return {
        "sql": sql,
        "explanation": (
            f"统计 {start.isoformat()} 至 {end.isoformat()} 各班迟到记录数，"
            "并按迟到次数从高到低排序。"
        ),
        "title": f"{period_label}迟到次数班级排名",
        "chart": "bar",
        "answer": lambda rows: _late_answer(rows, period_label),
    }


def _timetable_template(
    session: Session,
    question: str,
    *,
    student: str | None,
    teacher: str | None,
    grade_name: str | None,
    class_name: str | None,
) -> dict:
    if student and not class_name:
        class_name = _class_name_by_student(session, student)
    weekday = _weekday(question)
    filters = ""
    if class_name:
        filters += f" AND c.name = {_lit(class_name)}"
    if grade_name:
        filters += f" AND g.name = {_lit(grade_name)}"
    if teacher:
        filters += f" AND te.name = {_lit(teacher)}"
    if weekday:
        filters += f" AND t.weekday = {weekday}"
    sql = (
        "SELECT c.name AS 班级, "
        "CASE t.weekday WHEN 1 THEN '周一' WHEN 2 THEN '周二' "
        "WHEN 3 THEN '周三' WHEN 4 THEN '周四' WHEN 5 THEN '周五' "
        "ELSE '其他' END AS 星期, "
        "t.period AS 节次, t.subject AS 科目, te.name AS 教师, "
        "t.classroom AS 教室, t.start_time AS 开始时间, "
        "t.end_time AS 结束时间 "
        "FROM timetable AS t "
        "JOIN classes AS c ON t.class_id = c.id "
        "JOIN grades AS g ON c.grade_id = g.id "
        "JOIN teachers AS te ON t.teacher_id = te.id "
        "WHERE 1 = 1"
        f"{filters} "
        "ORDER BY c.name, t.weekday, t.period"
    )
    scope = class_name or (f"{teacher}老师" if teacher else "各班级")
    return {
        "sql": sql,
        "explanation": (
            "按班级、星期和节次查询本学期课程表"
            + (f"，并筛选 {teacher} 老师" if teacher else "")
            + "。"
        ),
        "title": f"{scope}课程表" + (f"（周{'一二三四五'[weekday - 1]}）" if weekday else ""),
        "chart": "table",
        "answer": lambda rows: _timetable_answer(rows, class_name),
    }


def _parent_template(
    question: str,
    *,
    student: str | None,
    grade_name: str | None,
    class_name: str | None,
) -> dict:
    filters = ""
    if student:
        filters += f" AND s.name = {_lit(student)}"
    if class_name:
        filters += f" AND c.name = {_lit(class_name)}"
    if grade_name:
        filters += f" AND g.name = {_lit(grade_name)}"
    sql = (
        "SELECT s.name AS 学生, c.name AS 班级, p.name AS 家长, "
        "p.relation AS 关系, p.phone AS 联系电话, p.occupation AS 职业 "
        "FROM parents AS p "
        "JOIN students AS s ON p.student_id = s.id "
        "JOIN classes AS c ON s.class_id = c.id "
        "JOIN grades AS g ON c.grade_id = g.id "
        "WHERE 1 = 1"
        f"{filters} "
        "ORDER BY s.name, p.is_primary DESC"
    )
    scope = student or class_name or grade_name or "全校"
    return {
        "sql": sql,
        "explanation": "查询学生对应的家长或监护人联系方式。",
        "title": f"{scope}家长联系方式",
        "chart": "table",
        "answer": lambda rows: _parent_answer(rows, student),
    }


def _leave_template(
    question: str,
    as_of: date,
    *,
    student: str | None,
    grade_name: str | None,
    class_name: str | None,
) -> dict:
    status = None
    status_label = "全部状态"
    if "待审批" in question:
        status = "待审批"
        status_label = "待审批"
    elif "已批准" in question or "通过" in question:
        status = "已批准"
        status_label = "已批准"
    elif "已驳回" in question or "驳回" in question:
        status = "已驳回"
        status_label = "已驳回"

    date_filter = ""
    period_label = ""
    if any(
        marker in question
        for marker in ("本周", "上周", "本月", "最近", "近", "今天", "昨天")
    ):
        start, end, period_label = _period_bounds(question, as_of)
        date_filter = (
            f" AND l.start_date BETWEEN {_lit(start.isoformat())} AND "
            f"{_lit(end.isoformat())}"
        )

    scope_filter = ""
    if student:
        scope_filter += f" AND s.name = {_lit(student)}"
    if class_name:
        scope_filter += f" AND c.name = {_lit(class_name)}"
    if grade_name:
        scope_filter += f" AND g.name = {_lit(grade_name)}"

    status_filter = f" AND l.status = {_lit(status)}" if status else ""

    if ("类型" in question or "病假" in question and "多少" in question) and not student:
        sql = (
            "SELECT l.leave_type AS 请假类型, COUNT(*) AS 申请数 "
            "FROM leave_requests AS l "
            "JOIN students AS s ON l.student_id = s.id "
            "JOIN classes AS c ON l.class_id = c.id "
            "JOIN grades AS g ON c.grade_id = g.id "
            "WHERE 1 = 1"
            f"{status_filter}{scope_filter}{date_filter} "
            "GROUP BY l.leave_type ORDER BY 申请数 DESC"
        )
        return {
            "sql": sql,
            "explanation": "按请假类型统计请假申请数量。",
            "title": f"{period_label}{status_label}请假类型统计",
            "chart": "bar",
            "answer": _leave_type_answer,
        }

    if ("班级" in question and ("最多" in question or "排名" in question)) or (
        "哪个班" in question
    ):
        sql = (
            "SELECT c.name AS 班级, COUNT(*) AS 请假次数 "
            "FROM leave_requests AS l "
            "JOIN students AS s ON l.student_id = s.id "
            "JOIN classes AS c ON l.class_id = c.id "
            "JOIN grades AS g ON c.grade_id = g.id "
            "WHERE 1 = 1"
            f"{status_filter}{scope_filter}{date_filter} "
            "GROUP BY c.id, c.name ORDER BY 请假次数 DESC"
        )
        return {
            "sql": sql,
            "explanation": "按班级统计请假申请次数并排序。",
            "title": f"{period_label}{status_label}请假班级排名",
            "chart": "bar",
            "answer": _leave_class_answer,
        }

    wants_count = any(marker in question for marker in _COUNT_MARKERS) and not any(
        marker in question for marker in _LIST_MARKERS
    )
    if wants_count:
        sql = (
            "SELECT COUNT(*) AS 申请数 "
            "FROM leave_requests AS l "
            "JOIN students AS s ON l.student_id = s.id "
            "JOIN classes AS c ON l.class_id = c.id "
            "JOIN grades AS g ON c.grade_id = g.id "
            "WHERE 1 = 1"
            f"{status_filter}{scope_filter}{date_filter}"
        )
        return {
            "sql": sql,
            "explanation": (
                f"统计{period_label}{status_label}的请假申请数量。"
            ),
            "title": f"{period_label}{status_label}请假数量",
            "chart": "table",
            "answer": lambda rows: _leave_count_answer(
                rows, status_label, period_label
            ),
        }

    sql = (
        "SELECT s.name AS 学生, c.name AS 班级, l.leave_type AS 请假类型, "
        "l.start_date AS 开始日期, l.end_date AS 结束日期, "
        "l.days AS 天数, l.status AS 状态, l.reason AS 事由 "
        "FROM leave_requests AS l "
        "JOIN students AS s ON l.student_id = s.id "
        "JOIN classes AS c ON l.class_id = c.id "
        "JOIN grades AS g ON c.grade_id = g.id "
        "WHERE 1 = 1"
        f"{status_filter}{scope_filter}{date_filter} "
        "ORDER BY l.start_date DESC, l.id DESC"
    )
    return {
        "sql": sql,
        "explanation": "查询请假记录明细，按开始日期倒序排列。",
        "title": f"{period_label}{status_label}请假记录",
        "chart": "table",
        "answer": lambda rows: _leave_list_answer(rows, period_label),
    }


def _workload_template(
    question: str,
    *,
    teacher: str | None,
    grade_name: str | None,
    class_name: str | None,
) -> dict:
    filters = ""
    if teacher:
        filters += f" AND te.name = {_lit(teacher)}"
    if class_name:
        filters += f" AND c.name = {_lit(class_name)}"
    if grade_name:
        filters += f" AND g.name = {_lit(grade_name)}"
    sql = (
        "SELECT te.name AS 教师, te.subject AS 学科, COUNT(*) AS 课时数, "
        "COUNT(DISTINCT t.class_id) AS 任教班级数 "
        "FROM timetable AS t "
        "JOIN teachers AS te ON t.teacher_id = te.id "
        "JOIN classes AS c ON t.class_id = c.id "
        "JOIN grades AS g ON c.grade_id = g.id "
        "WHERE 1 = 1"
        f"{filters} "
        "GROUP BY te.id, te.name, te.subject "
        "ORDER BY 课时数 DESC, te.name"
    )
    scope = teacher or class_name or grade_name or "全校"
    return {
        "sql": sql,
        "explanation": "按教师统计本学期课程表课时数，并统计任教班级数。",
        "title": f"{scope}教师课时统计",
        "chart": "bar",
        "answer": _workload_answer,
    }


def _head_teacher_template(
    *,
    grade_name: str | None,
    class_name: str | None,
) -> dict:
    filters = ""
    if class_name:
        filters += f" AND c.name = {_lit(class_name)}"
    if grade_name:
        filters += f" AND g.name = {_lit(grade_name)}"
    sql = (
        "SELECT c.name AS 班级, g.name AS 年级, te.name AS 班主任, "
        "te.subject AS 学科, te.phone AS 联系电话 "
        "FROM classes AS c "
        "JOIN grades AS g ON c.grade_id = g.id "
        "LEFT JOIN teachers AS te ON c.head_teacher_id = te.id "
        "WHERE 1 = 1"
        f"{filters} "
        "ORDER BY g.sort, c.name"
    )
    scope = class_name or grade_name or "各班级"
    return {
        "sql": sql,
        "explanation": "从班级表关联教师表查询班主任信息。",
        "title": f"{scope}班主任信息",
        "chart": "table",
        "answer": _head_teacher_answer,
    }


def _student_info_template(student: str) -> dict:
    sql = (
        "SELECT s.name AS 学生, s.student_no AS 学号, s.gender AS 性别, "
        "s.birth_date AS 出生日期, g.name AS 年级, c.name AS 班级, "
        "s.enrollment_date AS 入学日期, s.status AS 状态, "
        "s.phone AS 联系电话, s.address AS 家庭住址 "
        "FROM students AS s "
        "JOIN classes AS c ON s.class_id = c.id "
        "JOIN grades AS g ON c.grade_id = g.id "
        f"WHERE s.name = {_lit(student)}"
    )
    return {
        "sql": sql,
        "explanation": f"查询学生 {student} 的档案信息。",
        "title": f"{student}学生档案",
        "chart": "table",
        "answer": _student_info_answer,
    }


def _teacher_info_template(teacher: str) -> dict:
    sql = (
        "SELECT te.name AS 教师, te.teacher_no AS 工号, te.gender AS 性别, "
        "te.subject AS 学科, te.title AS 职称, te.phone AS 联系电话, "
        "te.hire_date AS 入职日期 "
        "FROM teachers AS te "
        f"WHERE te.name = {_lit(teacher)}"
    )
    return {
        "sql": sql,
        "explanation": f"查询教师 {teacher} 的档案信息。",
        "title": f"{teacher}教师档案",
        "chart": "table",
        "answer": _teacher_info_answer,
    }


def _counts_template(
    question: str,
    *,
    student: str | None,
    teacher: str | None,
    grade_name: str | None,
    class_name: str | None,
) -> dict | None:
    del student, teacher  # 人数统计按范围筛选，不需要具体姓名
    if "学生" in question or "男生" in question or "女生" in question:
        filter_sql = ""
        if grade_name:
            filter_sql += f" AND g.name = {_lit(grade_name)}"
        if class_name:
            filter_sql += f" AND c.name = {_lit(class_name)}"
        if "男生" in question or "女生" in question:
            gender = "男" if "男生" in question else "女"
            sql = (
                f"SELECT COUNT(*) AS {'男生人数' if gender == '男' else '女生人数'} "
                "FROM students AS s "
                "JOIN classes AS c ON s.class_id = c.id "
                "JOIN grades AS g ON c.grade_id = g.id "
                f"WHERE s.gender = {_lit(gender)}{filter_sql}"
            )
            label = "男生" if gender == "男" else "女生"
            return {
                "sql": sql,
                "explanation": f"统计{label}学生人数。",
                "title": f"{label}人数",
                "chart": "table",
                "answer": lambda rows: _count_total_answer(rows, f"名{label}"),
            }
        if "性别" in question:
            sql = (
                "SELECT s.gender AS 性别, COUNT(*) AS 人数 "
                "FROM students AS s "
                "JOIN classes AS c ON s.class_id = c.id "
                "JOIN grades AS g ON c.grade_id = g.id "
                "WHERE 1 = 1"
                f"{filter_sql} "
                "GROUP BY s.gender ORDER BY 人数 DESC"
            )
            return {
                "sql": sql,
                "explanation": "按性别统计学生人数。",
                "title": "学生性别分布",
                "chart": "bar",
                "answer": _gender_answer,
            }
        if "各年级" in question or ("年级" in question and "人数" in question and not class_name):
            sql = (
                "SELECT g.name AS 年级, COUNT(*) AS 学生人数 "
                "FROM students AS s "
                "JOIN classes AS c ON s.class_id = c.id "
                "JOIN grades AS g ON c.grade_id = g.id "
                "WHERE 1 = 1"
                f"{filter_sql} "
                "GROUP BY g.id, g.name, g.sort ORDER BY g.sort"
            )
            return {
                "sql": sql,
                "explanation": "按年级统计在读学生人数。",
                "title": "各年级学生人数",
                "chart": "bar",
                "answer": lambda rows: _group_count_answer(
                    rows, "年级", "学生人数", "人"
                ),
            }
        if "各班" in question or "每个班" in question or (
            "班级" in question and "学生" in question
        ):
            sql = (
                "SELECT c.name AS 班级, g.name AS 年级, COUNT(*) AS 学生人数 "
                "FROM students AS s "
                "JOIN classes AS c ON s.class_id = c.id "
                "JOIN grades AS g ON c.grade_id = g.id "
                "WHERE 1 = 1"
                f"{filter_sql} "
                "GROUP BY c.id, c.name, g.name, g.sort "
                "ORDER BY g.sort, c.name"
            )
            return {
                "sql": sql,
                "explanation": "按班级统计学生人数。",
                "title": "各班级学生人数",
                "chart": "bar",
                "answer": lambda rows: _group_count_answer(
                    rows, "班级", "学生人数", "人"
                ),
            }
        if any(marker in question for marker in _COUNT_MARKERS) or "人数" in question:
            sql = (
                "SELECT COUNT(*) AS 学生总数 "
                "FROM students AS s "
                "JOIN classes AS c ON s.class_id = c.id "
                "JOIN grades AS g ON c.grade_id = g.id "
                "WHERE 1 = 1"
                f"{filter_sql}"
            )
            scope = class_name or grade_name or "全校"
            return {
                "sql": sql,
                "explanation": f"统计{scope}学生总数。",
                "title": f"{scope}学生总数",
                "chart": "table",
                "answer": lambda rows: _count_total_answer(rows, "名学生"),
            }

    if "老师" in question or "教师" in question:
        if "各科" in question or "学科" in question:
            sql = (
                "SELECT subject AS 学科, COUNT(*) AS 教师人数 "
                "FROM teachers GROUP BY subject ORDER BY 教师人数 DESC, subject"
            )
            return {
                "sql": sql,
                "explanation": "按任教学科统计教师人数。",
                "title": "各学科教师人数",
                "chart": "bar",
                "answer": lambda rows: _group_count_answer(
                    rows, "学科", "教师人数", "人"
                ),
            }
        if any(marker in question for marker in _COUNT_MARKERS):
            sql = "SELECT COUNT(*) AS 教师总数 FROM teachers"
            return {
                "sql": sql,
                "explanation": "统计学校教师总数。",
                "title": "教师总数",
                "chart": "table",
                "answer": lambda rows: _count_total_answer(rows, "名教师"),
            }

    if "班级" in question and any(marker in question for marker in _COUNT_MARKERS):
        if "各年级" in question:
            sql = (
                "SELECT g.name AS 年级, COUNT(*) AS 班级数 "
                "FROM classes AS c "
                "JOIN grades AS g ON c.grade_id = g.id "
                "GROUP BY g.id, g.name, g.sort ORDER BY g.sort"
            )
            return {
                "sql": sql,
                "explanation": "按年级统计班级数量。",
                "title": "各年级班级数",
                "chart": "bar",
                "answer": lambda rows: _group_count_answer(
                    rows, "年级", "班级数", "个"
                ),
            }
        sql = "SELECT COUNT(*) AS 班级总数 FROM classes"
        return {
            "sql": sql,
            "explanation": "统计学校班级总数。",
            "title": "班级总数",
            "chart": "table",
            "answer": lambda rows: _count_total_answer(rows, "个班级"),
        }
    return None


def _mock_template(session: Session, question: str) -> dict | None:
    scaled = re.sub(r"\s+", "", question)
    as_of = resolve_as_of(session)
    student = _find_student(session, scaled)
    teacher = _find_teacher(session, scaled)
    grade_name, class_name = _scope(scaled)

    if any(marker in scaled for marker in ("课程表", "课表", "课程安排", "排课")):
        return _timetable_template(
            session,
            scaled,
            student=student,
            teacher=teacher,
            grade_name=grade_name,
            class_name=class_name,
        )

    if any(marker in scaled for marker in ("家长", "监护人", "父母")):
        return _parent_template(
            scaled,
            student=student,
            grade_name=grade_name,
            class_name=class_name,
        )

    attendance_intent = (
        "出勤率" in scaled
        or "出勤" in scaled
        or "考勤" in scaled
        or (
            student is not None
            and any(marker in scaled for marker in ("迟到", "缺勤", "早退"))
        )
    )
    leave_intent = any(marker in scaled for marker in _LEAVE_MARKERS)
    if attendance_intent and not (
        leave_intent and "出勤" not in scaled and "考勤" not in scaled
    ):
        return _attendance_template(
            session,
            scaled,
            as_of,
            student=student,
            grade_name=grade_name,
            class_name=class_name,
        )

    if "迟到" in scaled and any(
        marker in scaled for marker in ("最多", "排名", "次数", "最多", "统计")
    ):
        return _late_template(
            session,
            scaled,
            as_of,
            grade_name=grade_name,
            class_name=class_name,
            student=student,
        )

    if leave_intent:
        return _leave_template(
            scaled,
            as_of,
            student=student,
            grade_name=grade_name,
            class_name=class_name,
        )

    if "课时" in scaled or "工作量" in scaled or (
        ("老师" in scaled or "教师" in scaled)
        and any(marker in scaled for marker in ("排名", "最多", "排课", "课时"))
    ):
        return _workload_template(
            scaled,
            teacher=teacher,
            grade_name=grade_name,
            class_name=class_name,
        )

    if "班主任" in scaled:
        return _head_teacher_template(
            grade_name=grade_name,
            class_name=class_name,
        )

    if teacher and any(
        marker in scaled
        for marker in ("信息", "档案", "资料", "联系方式", "电话", "简介")
    ):
        return _teacher_info_template(teacher)

    if student and any(
        marker in scaled
        for marker in ("信息", "档案", "资料", "基本情况", "学号", "住址", "电话")
    ):
        return _student_info_template(student)

    return _counts_template(
        scaled,
        student=student,
        teacher=teacher,
        grade_name=grade_name,
        class_name=class_name,
    )


def _schema_summary(tables: Sequence[str] | None = None) -> str:
    selected = set(tables) if tables else set(TABLE_COLUMNS)
    lines = []
    for table, columns in sorted(TABLE_COLUMNS.items()):
        if table not in selected:
            continue
        lines.append(f"- {table}({', '.join(sorted(columns))})")
    return "\n".join(lines)


def _prompt_examples() -> str:
    return "\n".join(f"- {item['question']}" for item in EXAMPLES)


def _build_messages(
    question: str,
    *,
    tables: Sequence[str] | None = None,
    as_of: date | None = None,
    previous_sql: str | None = None,
    previous_error: str | None = None,
) -> list[dict[str, str]]:
    fallback = (
        "你是校园数据助手，只能生成 SQLite 只读 SELECT 查询。"
        "只允许查询 grades、teachers、classes、students、parents、"
        "attendance、leave_requests、timetable 这 8 张表。"
        "禁止写操作、PRAGMA、ATTACH、多语句、注释和 SELECT *。"
        "输出列别名使用中文；classes.name 已是“八年级一班”这类完整班级名，"
        "查询具体班级时直接过滤 c.name。当前业务日期：{today}。"
        "只输出 JSON 对象，不要 Markdown 代码块，格式："
        '{"sql": "...", "explanation": "中文解释", "chart": "bar|line|table", '
        '"title": "中文标题"}。\n'
        "表结构：\n{schema}\n"
        "可参考的问题：\n{examples}"
    )
    system = render_prompt(
        "text2sql",
        PROMPT_FILE_VERSION,
        fallback,
        schema=_schema_summary(tables),
        examples=_prompt_examples(),
        today=(as_of or date.today()).isoformat(),
    )
    user = question
    if previous_sql and previous_error:
        user = (
            f"问题：{question}\n"
            f"上一版 SQL 未通过校验或执行失败：{previous_sql}\n"
            f"错误：{previous_error}\n请修正后重新输出 JSON。"
        )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _parse_llm_payload(content: str) -> dict:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("模型未返回 JSON")
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"模型 JSON 无法解析：{exc}") from None
    sql = payload.get("sql")
    if not isinstance(sql, str) or not sql.strip():
        raise ValueError("模型未返回 SQL")
    return {
        "sql": sql.strip(),
        "explanation": str(payload.get("explanation") or "由模型生成的只读查询。"),
        "chart": str(payload.get("chart") or "table"),
        "title": str(payload.get("title") or "").strip() or "查询结果",
    }


def _execute_sqlite(
    sql: str, started: float
) -> tuple[list[str], list[tuple], int]:
    raw_connection = engine.raw_connection()
    driver = getattr(raw_connection, "driver_connection", None)
    if driver is None:
        driver = raw_connection.connection
    try:
        driver.execute("PRAGMA query_only=ON")
        deadline = time.perf_counter() + QUERY_TIMEOUT_SECONDS

        def _timeout() -> int:
            return 1 if time.perf_counter() > deadline else 0

        driver.set_progress_handler(_timeout, 10000)
        try:
            cursor = driver.execute(sql)
            columns = [item[0] for item in (cursor.description or [])]
            rows = cursor.fetchmany(MAX_ROWS + 1)
        finally:
            driver.set_progress_handler(None, 0)
    except sqlite3.Error as exc:
        message = str(exc)
        if "interrupted" in message.lower():
            raise SqlExecutionError("查询超时（超过 3 秒）") from None
        raise SqlExecutionError(f"查询执行失败：{message}") from None
    finally:
        try:
            driver.execute("PRAGMA query_only=OFF")
        except sqlite3.Error:
            pass
        raw_connection.close()
    return columns, list(rows[:MAX_ROWS]), int((time.perf_counter() - started) * 1000)


def _execute_postgres(
    sql: str, started: float
) -> tuple[list[str], list[tuple], int]:
    """PostgreSQL 只读事务 + 语句级 3 秒超时。"""
    raw_connection = engine.raw_connection()
    driver = getattr(raw_connection, "driver_connection", None)
    if driver is None:
        driver = raw_connection.connection
    cursor = driver.cursor()
    try:
        cursor.execute("SET TRANSACTION READ ONLY")
        cursor.execute("SET LOCAL statement_timeout = 3000")
        cursor.execute(sql)
        columns = [
            getattr(item, "name", None) or item[0]
            for item in (cursor.description or [])
        ]
        rows = cursor.fetchmany(MAX_ROWS + 1)
    except Exception as exc:  # noqa: BLE001 - 只向调用方暴露可读原因
        sqlstate = getattr(exc, "sqlstate", None) or getattr(exc, "pgcode", None)
        if sqlstate == "57014":
            raise SqlExecutionError("查询超时（超过 3 秒）") from None
        raise SqlExecutionError(f"查询执行失败：{exc}") from None
    finally:
        try:
            cursor.close()
            raw_connection.rollback()
        except Exception:  # noqa: BLE001 - 清理阶段不覆盖原始错误
            pass
        raw_connection.close()
    return columns, list(rows[:MAX_ROWS]), int((time.perf_counter() - started) * 1000)


def execute_readonly(sql: str) -> tuple[list[str], list[tuple], int]:
    started = time.perf_counter()
    if engine.dialect.name == "postgresql":
        return _execute_postgres(sql, started)
    return _execute_sqlite(sql, started)


def _json_value(value):
    if isinstance(value, (datetime, date, dt_time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _build_result_rows(
    names: list[str], raw_rows: list[tuple]
) -> tuple[list[dict], list[dict]]:
    rows = []
    for raw_row in raw_rows:
        rows.append({name: _json_value(value) for name, value in zip(names, raw_row)})
    columns = []
    for name in names:
        values = [row[name] for row in rows if row[name] is not None]
        kind = (
            "number"
            if values and all(isinstance(value, (int, float)) for value in values)
            else "string"
        )
        columns.append(_column(name, name, kind))
    return columns, rows


def _build_chart(
    columns: list[dict], rows: list[dict], hint: str | None
) -> dict:
    if not rows:
        return {"type": "table", "x": None, "y": [], "series": []}
    numeric = [item["key"] for item in columns if item.get("type") == "number"]
    strings = [item["key"] for item in columns if item.get("type") != "number"]
    if not numeric:
        return {"type": "table", "x": None, "y": [], "series": []}
    x_key = None
    if strings:
        distinct = {
            key: len({str(row.get(key)) for row in rows}) for key in strings
        }
        x_key = max(strings, key=lambda key: distinct[key])
        if distinct[x_key] == 1 and len(strings) > 1:
            x_key = strings[0]
    chart_type = hint if hint in {"bar", "line", "table"} else "bar"
    if x_key is None:
        chart_type = "table"
    elif chart_type != "table":
        pattern = re.compile(r"^\d{4}-\d{2}-\d{2}")
        if all(pattern.match(str(row.get(x_key, ""))) for row in rows[:5]):
            chart_type = "line"
    return {
        "type": chart_type,
        "x": x_key,
        "y": numeric,
        "series": [{"name": key, "key": key} for key in numeric],
    }


def _row_brief(row: dict, columns: list[dict]) -> str:
    parts = [
        f"{item['label']} {_fmt(row.get(item['key']))}"
        for item in columns[:4]
        if row.get(item["key"]) not in (None, "")
    ]
    return "，".join(parts) or "空记录"


def _generic_answer(title: str, columns: list[dict], rows: list[dict]) -> str:
    if not rows:
        return f"{title}：没有查询到符合条件的数据。"
    if len(rows) == 1:
        pairs = [
            f"{item['label']}为 {_fmt(rows[0].get(item['key']))}"
            for item in columns[:6]
        ]
        return f"{title}：" + "，".join(pairs) + "。"
    details = "；".join(_row_brief(row, columns) for row in rows[:3])
    if len(rows) > 3:
        return f"{title}：{details} 等 {len(rows)} 条记录。"
    return f"{title}：{details}（共 {len(rows)} 条记录）。"


def _answer_text(
    answer_fn: Callable[[list[dict]], str] | None,
    title: str,
    columns: list[dict],
    rows: list[dict],
) -> str:
    if answer_fn:
        try:
            return answer_fn(rows)
        except Exception:  # noqa: BLE001 - 回答生成失败时退回通用描述
            pass
    return _generic_answer(title, columns, rows)


def _base_result(
    question: str,
    started: float,
    *,
    degraded: bool,
    model_mode: str,
    title: str,
    answer: str,
    explanation: str,
    sql: str | None,
    columns: list[dict] | None = None,
    rows: list[dict] | None = None,
    chart: dict | None = None,
    need_clarification: bool = False,
) -> dict:
    columns = columns or []
    rows = rows or []
    elapsed = int((time.perf_counter() - started) * 1000)
    return {
        "question": question,
        "title": title,
        "sql": sql,
        "explanation": explanation,
        "columns": columns,
        "rows": rows,
        "chart": chart or {"type": "table", "x": None, "y": [], "series": []},
        "answer": answer,
        "summary": answer,
        "row_count": len(rows),
        "elapsed_ms": elapsed,
        "need_clarification": need_clarification,
        "degraded": degraded,
        "model_mode": model_mode,
    }


def _clarification_result(
    question: str, started: float, *, degraded: bool, model_mode: str
) -> dict:
    return _base_result(
        question,
        started,
        degraded=degraded,
        model_mode=model_mode,
        title="需要更多信息",
        answer=CLARIFICATION,
        explanation="暂时没有匹配到固定的查询模板。",
        sql=None,
        need_clarification=True,
    )


def _failure_result(
    question: str,
    started: float,
    *,
    degraded: bool,
    model_mode: str,
    reason: str,
) -> dict:
    return _base_result(
        question,
        started,
        degraded=degraded,
        model_mode=model_mode,
        title="查询未能完成",
        answer=f"{reason}{CLARIFICATION}",
        explanation=reason,
        sql=None,
        need_clarification=True,
    )


def _run_sql(
    question: str,
    started: float,
    sql: str,
    *,
    title: str,
    explanation: str,
    chart_hint: str | None,
    answer_fn: Callable[[list[dict]], str] | None,
    degraded: bool,
    model_mode: str,
) -> dict:
    safe_sql = validate_sql(sql)
    names, raw_rows, _query_ms = execute_readonly(safe_sql)
    columns, rows = _build_result_rows(names, raw_rows)
    chart = _build_chart(columns, rows, chart_hint)
    answer = _answer_text(answer_fn, title, columns, rows)
    return _base_result(
        question,
        started,
        degraded=degraded,
        model_mode=model_mode,
        title=title,
        answer=answer,
        explanation=explanation,
        sql=safe_sql,
        columns=columns,
        rows=rows,
        chart=chart,
    )


def _answer_question(
    session: Session,
    question: str,
    *,
    gateway: LLMGateway | None = None,
) -> dict:
    started = time.perf_counter()
    question = (question or "").strip()
    gateway = gateway or get_gateway()
    degraded = gateway.degraded
    model_mode = "real" if gateway.effective_mode == "real" else "mock"

    if not question:
        return _clarification_result(
            question, started, degraded=degraded, model_mode=model_mode
        )

    if gateway.effective_mode == "mock":
        template = _mock_template(session, question)
        if template is None:
            return _clarification_result(
                question, started, degraded=degraded, model_mode=model_mode
            )
        try:
            return _run_sql(
                question,
                started,
                template["sql"],
                title=template["title"],
                explanation=template["explanation"],
                chart_hint=template.get("chart"),
                answer_fn=template.get("answer"),
                degraded=degraded,
                model_mode="mock",
            )
        except (SqlValidationError, SqlExecutionError) as exc:
            return _failure_result(
                question,
                started,
                degraded=degraded,
                model_mode="mock",
                reason=f"内置查询模板执行失败：{exc}",
            )

    previous_sql: str | None = None
    previous_error: str | None = None
    as_of = resolve_as_of(session)
    retrieved_tables = (
        retrieve_tables(question)
        if settings.TEXT2SQL_SCHEMA_RETRIEVAL
        else list(TABLE_COLUMNS)
    )
    observability.add_metadata(
        retrieved_tables=retrieved_tables,
        schema_retrieval=len(retrieved_tables) < len(TABLE_COLUMNS),
        as_of=as_of.isoformat(),
        prompt_version=f"text2sql-{PROMPT_FILE_VERSION}",
    )
    for attempt in range(1, 3):
        payload: dict | None = None
        messages = _build_messages(
            question,
            # 第一次只给相关表；校验或执行失败后回退完整 8 表结构。
            tables=retrieved_tables if attempt == 1 else None,
            as_of=as_of,
            previous_sql=previous_sql,
            previous_error=previous_error,
        )
        llm_result = gateway.complete(
            messages,
            task="text2sql",
            prompt_version="text2sql-v1",
            temperature=0.1,
            max_tokens=800,
        )
        if llm_result.mode != "real":
            degraded = True
            template = _mock_template(session, question)
            if template is None:
                return _failure_result(
                    question,
                    started,
                    degraded=degraded,
                    model_mode="mock",
                    reason="大模型当前不可用，且没有匹配到离线模板。",
                )
            try:
                return _run_sql(
                    question,
                    started,
                    template["sql"],
                    title=template["title"],
                    explanation=template["explanation"],
                    chart_hint=template.get("chart"),
                    answer_fn=template.get("answer"),
                    degraded=degraded,
                    model_mode="mock",
                )
            except (SqlValidationError, SqlExecutionError) as exc:
                return _failure_result(
                    question,
                    started,
                    degraded=degraded,
                    model_mode="mock",
                    reason=f"离线模板执行失败：{exc}",
                )

        try:
            payload = _parse_llm_payload(llm_result.content)
            return _run_sql(
                question,
                started,
                payload["sql"],
                title=payload["title"],
                explanation=payload["explanation"],
                chart_hint=payload["chart"],
                answer_fn=None,
                degraded=degraded or llm_result.degraded,
                model_mode="real",
            )
        except (ValueError, SqlValidationError, SqlExecutionError) as exc:
            previous_sql = payload["sql"] if payload else None
            previous_error = str(exc)
            if attempt == 2:
                break

    template = _mock_template(session, question)
    if template is not None:
        try:
            return _run_sql(
                question,
                started,
                template["sql"],
                title=template["title"],
                explanation=(
                    template["explanation"]
                    + "（模型生成的 SQL 未通过校验，已回退到内置模板）"
                ),
                chart_hint=template.get("chart"),
                answer_fn=template.get("answer"),
                degraded=True,
                model_mode="mock",
            )
        except (SqlValidationError, SqlExecutionError):
            pass

    return _failure_result(
        question,
        started,
        degraded=True,
        model_mode="real",
        reason="大模型生成的查询未通过安全校验或执行失败。",
    )


def answer_question(
    session: Session,
    question: str,
    *,
    gateway: LLMGateway | None = None,
    record: bool = True,
) -> dict:
    """问数入口：包一层 Langfuse trace，并记录任务完成率与忠实度。

    ``record=False`` 供智能助手与黄金评测复用，避免同一次请求重复计数；
    助手在 ``assistant.chat`` 外层统一记录。
    """

    with observability.trace(
        "text2sql.answer",
        input={"question": question},
        metadata={"prompt_version": f"text2sql-{PROMPT_FILE_VERSION}"},
        tags=["text2sql"],
    ) as trace:
        result = _answer_question(session, question, gateway=gateway)
        trace.update(
            output={
                "title": result.get("title"),
                "sql": result.get("sql"),
                "row_count": result.get("row_count"),
                "answer": result.get("answer"),
            },
            metadata={
                "model_mode": result.get("model_mode"),
                "degraded": result.get("degraded"),
                "need_clarification": result.get("need_clarification"),
                "elapsed_ms": result.get("elapsed_ms"),
            },
        )
        observability.add_metadata(
            row_count=result.get("row_count"),
            degraded=result.get("degraded"),
            model_mode=result.get("model_mode"),
        )
        if record:
            evaluation.record_query(result)
        return result
