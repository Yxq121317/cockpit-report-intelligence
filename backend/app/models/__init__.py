"""校园数据模型的唯一汇总入口，共 8 张表。"""

from app.models.school import (
    Attendance,
    Class,
    Grade,
    LeaveRequest,
    Parent,
    Student,
    Teacher,
    Timetable,
)

__all__ = [
    "Attendance",
    "Class",
    "Grade",
    "LeaveRequest",
    "Parent",
    "Student",
    "Teacher",
    "Timetable",
]
