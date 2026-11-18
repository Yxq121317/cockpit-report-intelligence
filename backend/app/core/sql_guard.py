"""Text2SQL 只读安全校验：单条 SELECT、表/列白名单、禁写、强制 LIMIT。"""

from __future__ import annotations

import re

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from app.core.db import Base, target_sql_dialect
from app.models import Attendance  # noqa: F401  确保全部模型已注册

MAX_ROWS = 200

TABLE_COLUMNS: dict[str, set[str]] = {
    table.name.lower(): {
        column.name.lower() for column in table.columns
    }
    for table in Base.metadata.tables.values()
}

_DANGEROUS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|PRAGMA|ATTACH|DETACH|VACUUM|"
    r"CREATE|REPLACE|TRUNCATE|LOAD_EXTENSION)\b",
    re.IGNORECASE,
)
_COMMENT = re.compile(r"(--|/\*|\*/|#)")


class SqlValidationError(ValueError):
    """SQL 未通过安全校验。"""


class SqlExecutionError(RuntimeError):
    """SQL 只读执行失败（对外只返回可读原因）。"""


def validate_sql(sql: str, *, dialect: str | None = None) -> str:
    if not isinstance(sql, str) or not sql.strip():
        raise SqlValidationError("SQL 不能为空")
    text = sql.strip()
    if _COMMENT.search(text):
        raise SqlValidationError("SQL 不允许包含注释")
    if _DANGEROUS.search(text):
        raise SqlValidationError("SQL 只允许只读查询")

    try:
        statements = sqlglot.parse(text, read="sqlite")
    except ParseError as exc:
        raise SqlValidationError(f"SQL 无法解析：{exc}") from None
    statements = [statement for statement in statements if statement is not None]
    if len(statements) != 1:
        raise SqlValidationError("只允许单条 SQL")
    expression = statements[0]
    if not isinstance(expression, exp.Select):
        raise SqlValidationError("只允许 SELECT 查询")

    cte_names = {
        cte.alias_or_name.lower()
        for cte in expression.find_all(exp.CTE)
        if cte.alias_or_name
    }
    cte_aliases: set[str] = set()
    table_aliases: dict[str, str] = {}
    for table in expression.find_all(exp.Table):
        name = table.name.lower()
        if name in cte_names:
            cte_aliases.add(table.alias_or_name.lower())
            continue
        if name not in TABLE_COLUMNS:
            raise SqlValidationError(f"不允许访问表：{name}")
        table_aliases[table.alias_or_name.lower()] = name

    if not table_aliases:
        raise SqlValidationError("查询必须包含白名单内的数据表")

    for select in expression.find_all(exp.Select):
        for projection in select.expressions:
            if isinstance(projection, exp.Star) or (
                isinstance(projection, exp.Alias)
                and isinstance(projection.this, exp.Star)
            ):
                raise SqlValidationError("不允许使用 SELECT *，请显式列出字段")

    select_aliases = {
        alias.alias.lower()
        for alias in expression.find_all(exp.Alias)
        if alias.alias
    }
    known_columns = set().union(*TABLE_COLUMNS.values())
    for column in expression.find_all(exp.Column):
        name = column.name.lower()
        if name == "*":
            raise SqlValidationError("不允许使用 *")
        qualifier = column.table.lower()
        if qualifier:
            if qualifier in cte_names or qualifier in cte_aliases:
                continue
            table_name = table_aliases.get(qualifier)
            if table_name is None:
                raise SqlValidationError(f"未知的表别名：{column.table}")
            if name not in TABLE_COLUMNS[table_name]:
                raise SqlValidationError(
                    f"不允许访问字段：{column.table}.{column.name}"
                )
        elif name in select_aliases:
            continue
        elif name not in known_columns:
            raise SqlValidationError(f"不允许访问字段：{column.name}")

    limit = expression.args.get("limit")
    if limit is None:
        expression = expression.limit(MAX_ROWS)
    else:
        value = None
        try:
            value = int(limit.expression.this)
        except (AttributeError, TypeError, ValueError):
            value = None
        if value is None or value > MAX_ROWS:
            expression = expression.limit(MAX_ROWS)

    target = (dialect or target_sql_dialect()).lower()
    if target in {"postgres", "postgresql"}:
        rendered = expression.sql(dialect="postgres")
        # sqlglot 的 PostgreSQL 生成器输出 pyformat 占位符，SQLAlchemy
        # text() 需要 :name；只替换 AST 中真实存在的占位符。
        for placeholder in expression.find_all(exp.Placeholder):
            if placeholder.name:
                rendered = rendered.replace(
                    f"%({placeholder.name})s", f":{placeholder.name}"
                )
        return rendered
    if target != "sqlite":
        raise ValueError(f"不支持的 SQL 方言：{target}")
    return expression.sql(dialect=target)
