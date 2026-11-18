"""8 张业务表的结构与种子数据基线。"""

from sqlalchemy import func, inspect, select

from app.core.db import engine
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
from app.seed.seed import seed_database

EXPECTED_TABLES = {
    "grades",
    "teachers",
    "classes",
    "students",
    "parents",
    "attendance",
    "leave_requests",
    "timetable",
}


def test_database_contains_exactly_eight_business_tables(seeded_db):
    assert set(inspect(engine).get_table_names()) == EXPECTED_TABLES


def test_seed_counts(session):
    expected = {
        Grade: 3,
        Teacher: 24,
        Class: 6,
        Student: 180,
        Parent: 360,
        Attendance: 8280,
        LeaveRequest: 60,
        Timetable: 180,
    }
    for model, count in expected.items():
        actual = session.scalar(select(func.count()).select_from(model))
        assert actual == count, model.__name__


def test_seed_is_idempotent(session):
    before = session.scalar(select(func.count()).select_from(Student))
    seed_database(session)
    after = session.scalar(select(func.count()).select_from(Student))
    assert after == before == 180


def test_fixed_students_belong_to_expected_classes(session):
    for name, class_name in (("张小明", "八年级一班"), ("李雷", "八年级二班")):
        student = session.scalar(select(Student).where(Student.name == name))
        assert student is not None
        assert session.get(Class, student.class_id).name == class_name


def test_attendance_statuses_and_unique_slots(session):
    statuses = set(session.scalars(select(Attendance.status).distinct()).all())
    assert statuses == {"正常", "迟到", "早退", "缺勤", "请假"}

    duplicate = session.execute(
        select(
            Attendance.student_id,
            Attendance.date,
            func.count().label("count"),
        )
        .group_by(Attendance.student_id, Attendance.date)
        .having(func.count() > 1)
    ).all()
    assert duplicate == []


def test_timetable_covers_six_periods_per_weekday(session):
    rows = session.scalars(select(Timetable)).all()
    slots = {(row.class_id, row.weekday, row.period) for row in rows}
    assert len(slots) == 6 * 5 * 6
    assert {row.weekday for row in rows} == {1, 2, 3, 4, 5}
    assert {row.period for row in rows} == {1, 2, 3, 4, 5, 6}
