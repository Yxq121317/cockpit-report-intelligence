"""Text2SQL 表结构检索策略：按问题召回相关表并补全 JOIN 路径。"""

from app.core.schema_retrieval import (
    ALL_TABLES,
    retrieve_tables,
    schema_recall,
)


def test_attendance_question_retrieves_join_path_tables():
    tables = set(retrieve_tables("上周八年级各班出勤率"))
    assert {"attendance", "students", "classes", "grades"} <= tables
    # 与出勤无关的表不应被带入，避免干扰模型生成 SQL。
    assert "timetable" not in tables
    assert "parents" not in tables


def test_parent_question_retrieves_parent_student_class_grade():
    tables = set(retrieve_tables("李雷的家长联系方式"))
    assert {"parents", "students", "classes", "grades"} <= tables
    assert "attendance" not in tables


def test_timetable_question_retrieves_teacher_tables():
    tables = set(retrieve_tables("八年级二班本周的课程表"))
    assert {"timetable", "classes", "teachers", "grades"} <= tables


def test_leave_question_retrieves_leave_tables():
    tables = set(retrieve_tables("本周待审批请假有多少"))
    assert {"leave_requests", "students", "classes", "grades"} <= tables


def test_unrecognized_question_falls_back_to_all_tables():
    assert set(retrieve_tables("你好，请介绍一下你自己")) == set(ALL_TABLES)


def test_retrieval_respects_max_tables():
    tables = retrieve_tables("全校各年级班级学生出勤请假课程家长教师", max_tables=3)
    assert len(tables) == 3
    assert all(table in ALL_TABLES for table in tables)


def test_schema_recall_measurement():
    assert schema_recall({"attendance", "students"}, {"attendance", "students", "classes"}) == 1.0
    assert schema_recall({"attendance", "students"}, {"attendance"}) == 0.5
    assert schema_recall(set(), {"attendance"}) == 1.0
    assert schema_recall({"attendance"}, set()) == 0.0
