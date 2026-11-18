"""Text2SQL 只读安全校验。"""

import pytest

from app.core.sql_guard import (
    MAX_ROWS,
    TABLE_COLUMNS,
    SqlValidationError,
    validate_sql,
)
from app.services.text2sql import execute_readonly


def test_whitelist_contains_only_eight_tables():
    assert set(TABLE_COLUMNS) == {
        "grades",
        "teachers",
        "classes",
        "students",
        "parents",
        "attendance",
        "leave_requests",
        "timetable",
    }
    assert "password_hash" not in set().union(*TABLE_COLUMNS.values())


def test_validate_adds_limit():
    sql = validate_sql("SELECT name FROM students")
    assert sql.upper().endswith(f"LIMIT {MAX_ROWS}")


def test_validate_keeps_smaller_limit():
    sql = validate_sql("SELECT name FROM students LIMIT 5")
    assert sql.upper().endswith("LIMIT 5")


def test_validate_clamps_large_limit():
    sql = validate_sql("SELECT name FROM students LIMIT 9999")
    assert sql.upper().endswith(f"LIMIT {MAX_ROWS}")


def test_validate_allows_join_and_case():
    sql = validate_sql(
        "SELECT c.name AS 班级, "
        "SUM(CASE WHEN a.status IN ('正常','迟到') THEN 1 ELSE 0 END) AS 出勤人次 "
        "FROM attendance a "
        "JOIN classes c ON a.class_id = c.id "
        "GROUP BY c.name ORDER BY 出勤人次 DESC"
    )
    assert "attendance" in sql and "LIMIT" in sql.upper()


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM students",
        "SELECT s.* FROM students s",
        "SELECT COUNT(*) FROM (SELECT * FROM students) AS t",
        "WITH x AS (SELECT * FROM students) SELECT COUNT(*) FROM x",
        "DELETE FROM students",
        "UPDATE students SET name = 'x'",
        "DROP TABLE students",
        "SELECT name FROM users",
        "SELECT password_hash FROM students",
        "SELECT name FROM students; SELECT name FROM teachers",
        "SELECT name FROM students -- 注释",
        "PRAGMA table_info(students)",
        "SELECT name FROM exam_scores",
    ],
)
def test_validate_rejects_unsafe_or_unknown_sql(sql):
    with pytest.raises(SqlValidationError):
        validate_sql(sql)


def test_execute_readonly_returns_rows(seeded_db):
    safe_sql = validate_sql("SELECT COUNT(*) AS total FROM students")
    names, rows, elapsed = execute_readonly(safe_sql)
    assert names == ["total"]
    assert rows[0][0] == 180
    assert elapsed >= 0


def test_postgres_dialect_rendering_keeps_limit():
    sql = validate_sql("SELECT name FROM students", dialect="postgres")
    assert "LIMIT" in sql.upper()
