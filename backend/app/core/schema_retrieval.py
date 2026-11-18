"""Text2SQL 表结构检索：按问题召回相关表并补全 JOIN 路径。

项目没有向量检索层；这里的“检索策略”指在生成 SQL 前做表结构链接
（schema linking），只把问题相关的表和必要的关联表放进提示词，降低
模型看到无关字段后生成错误 SQL 的概率。无法识别问题时回退到全部 8 表。
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from app.core.sql_guard import TABLE_COLUMNS

ALL_TABLES: tuple[str, ...] = tuple(
    table
    for table in (
        "grades",
        "teachers",
        "classes",
        "students",
        "parents",
        "attendance",
        "leave_requests",
        "timetable",
    )
    if table in TABLE_COLUMNS
)

# 关键词按业务问法维护；长词（如“出勤率”）与短词（如“出勤”）同时保留。
TABLE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "grades": ("年级", "全校", "各年级", "七年级", "八年级", "九年级"),
    "teachers": (
        "老师",
        "教师",
        "班主任",
        "任课",
        "学科",
        "职称",
        "工号",
        "课时",
        "工作量",
    ),
    "classes": ("班级", "各班", "班里", "教室", "班", "年级"),
    "students": (
        "学生",
        "学号",
        "男生",
        "女生",
        "性别",
        "花名册",
        "入学",
        "在读",
        "出生",
        "住址",
    ),
    "parents": ("家长", "监护人", "父母", "父亲", "母亲", "职业"),
    "attendance": (
        "出勤",
        "出勤率",
        "考勤",
        "迟到",
        "缺勤",
        "早退",
        "正常",
    ),
    "leave_requests": ("请假", "病假", "事假", "审批", "待审批", "驳回", "批准"),
    "timetable": (
        "课程",
        "课程表",
        "课表",
        "排课",
        "节次",
        "星期",
        "周一",
        "周二",
        "周三",
        "周四",
        "周五",
        "科目",
        "课时",
    ),
}

# 只做一跳补全，避免无关表通过多跳关联扩散到全表。
TABLE_JOIN_NEIGHBORS: dict[str, tuple[str, ...]] = {
    "grades": ("classes",),
    "teachers": ("classes",),
    "classes": ("grades",),
    "students": ("classes", "grades"),
    "parents": ("students", "classes", "grades"),
    "attendance": ("students", "classes", "grades"),
    "leave_requests": ("students", "classes", "grades"),
    "timetable": ("classes", "teachers", "grades"),
}

_TABLE_ORDER = {table: index for index, table in enumerate(ALL_TABLES)}


def _normalize(question: str) -> str:
    return re.sub(r"\s+", "", question or "")


def _table_score(text: str, keywords: Iterable[str]) -> int:
    return sum(len(keyword) for keyword in keywords if keyword and keyword in text)


def retrieve_tables(question: str, *, max_tables: int = 8) -> list[str]:
    """返回与问题相关的表（含一跳 JOIN 依赖），无法识别时返回全部 8 表。"""

    text = _normalize(question)
    scores = {
        table: _table_score(text, TABLE_KEYWORDS.get(table, ()))
        for table in ALL_TABLES
    }
    matched = {table for table, score in scores.items() if score > 0}
    if not matched:
        return list(ALL_TABLES[: max(1, min(max_tables, len(ALL_TABLES)))])

    selected = set(matched)
    for table in matched:
        selected.update(TABLE_JOIN_NEIGHBORS.get(table, ()))

    ordered = sorted(
        matched,
        key=lambda table: (-scores[table], _TABLE_ORDER[table]),
    )
    expanded = [
        table
        for table in ALL_TABLES
        if table in selected and table not in matched
    ]
    result = ordered + expanded
    return result[: max(1, min(max_tables, len(ALL_TABLES)))]


def schema_recall(expected: Iterable[str], retrieved: Iterable[str]) -> float:
    """表结构召回率：期望表被检索到的比例；没有期望表时视为 1.0。"""

    expected_set = {table.lower() for table in expected if table}
    if not expected_set:
        return 1.0
    retrieved_set = {table.lower() for table in retrieved if table}
    return len(expected_set & retrieved_set) / len(expected_set)


def validate_retrieval_tables() -> None:
    """开发期自检：检索配置必须始终落在 SQL 白名单内。"""

    unknown = (set(TABLE_KEYWORDS) | set(TABLE_JOIN_NEIGHBORS)) - set(TABLE_COLUMNS)
    if unknown:
        raise ValueError(f"检索配置包含未知表：{sorted(unknown)}")
