"""校园数据问答的 8 张业务表。"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Grade(Base):
    """年级：七年级 / 八年级 / 九年级。"""

    __tablename__ = "grades"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    level: Mapped[int] = mapped_column(Integer, unique=True)
    sort: Mapped[int] = mapped_column(Integer, default=0)


class Teacher(Base):
    """教师档案。班主任身份由 classes.head_teacher_id 体现。"""

    __tablename__ = "teachers"

    id: Mapped[int] = mapped_column(primary_key=True)
    teacher_no: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(50), index=True)
    gender: Mapped[str] = mapped_column(String(10))
    subject: Mapped[str] = mapped_column(String(30), index=True)
    title: Mapped[str] = mapped_column(String(30), default="")
    phone: Mapped[str] = mapped_column(String(20), default="")
    hire_date: Mapped[date] = mapped_column(Date)


class Class(Base):
    """班级，关联年级与班主任。"""

    __tablename__ = "classes"

    id: Mapped[int] = mapped_column(primary_key=True)
    grade_id: Mapped[int] = mapped_column(ForeignKey("grades.id"), index=True)
    name: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    head_teacher_id: Mapped[int | None] = mapped_column(
        ForeignKey("teachers.id"), nullable=True, index=True
    )
    classroom: Mapped[str] = mapped_column(String(30), default="")


class Student(Base):
    """学生档案，同时冗余年级和班级便于直接统计。"""

    __tablename__ = "students"

    id: Mapped[int] = mapped_column(primary_key=True)
    student_no: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(50), index=True)
    gender: Mapped[str] = mapped_column(String(10), index=True)
    birth_date: Mapped[date] = mapped_column(Date)
    grade_id: Mapped[int] = mapped_column(ForeignKey("grades.id"), index=True)
    class_id: Mapped[int] = mapped_column(ForeignKey("classes.id"), index=True)
    enrollment_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="在读", index=True)
    phone: Mapped[str] = mapped_column(String(20), default="")
    address: Mapped[str] = mapped_column(String(200), default="")


class Parent(Base):
    """家长/监护人，一个学生可以有多位家长。"""

    __tablename__ = "parents"

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(
        ForeignKey("students.id"), index=True
    )
    name: Mapped[str] = mapped_column(String(50))
    relation: Mapped[str] = mapped_column(String(20))
    phone: Mapped[str] = mapped_column(String(20), index=True)
    occupation: Mapped[str] = mapped_column(String(50), default="")
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)


class Attendance(Base):
    """每日出勤记录，一名学生一天一条。"""

    __tablename__ = "attendance"
    __table_args__ = (
        UniqueConstraint("student_id", "date", name="uq_attendance_student_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(
        ForeignKey("students.id"), index=True
    )
    class_id: Mapped[int] = mapped_column(ForeignKey("classes.id"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(10), index=True)
    check_in_time: Mapped[str | None] = mapped_column(String(8), nullable=True)
    remark: Mapped[str] = mapped_column(String(200), default="")


class LeaveRequest(Base):
    """请假申请与审批结果。"""

    __tablename__ = "leave_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(
        ForeignKey("students.id"), index=True
    )
    class_id: Mapped[int] = mapped_column(ForeignKey("classes.id"), index=True)
    leave_type: Mapped[str] = mapped_column(String(20), index=True)
    start_date: Mapped[date] = mapped_column(Date, index=True)
    end_date: Mapped[date] = mapped_column(Date)
    days: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), index=True)
    applied_at: Mapped[datetime] = mapped_column(DateTime)
    approver_teacher_id: Mapped[int | None] = mapped_column(
        ForeignKey("teachers.id"), nullable=True, index=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    remark: Mapped[str] = mapped_column(String(200), default="")


class Timetable(Base):
    """课程表：班级 + 星期 + 节次唯一。"""

    __tablename__ = "timetable"
    __table_args__ = (
        UniqueConstraint(
            "class_id",
            "weekday",
            "period",
            "semester",
            name="uq_timetable_class_slot",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    class_id: Mapped[int] = mapped_column(ForeignKey("classes.id"), index=True)
    weekday: Mapped[int] = mapped_column(Integer, index=True)
    period: Mapped[int] = mapped_column(Integer)
    subject: Mapped[str] = mapped_column(String(30), index=True)
    teacher_id: Mapped[int] = mapped_column(
        ForeignKey("teachers.id"), index=True
    )
    classroom: Mapped[str] = mapped_column(String(30), default="")
    semester: Mapped[str] = mapped_column(String(30), index=True)
    start_time: Mapped[str] = mapped_column(String(5))
    end_time: Mapped[str] = mapped_column(String(5))
