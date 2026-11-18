"""查数请求与导出模型。"""

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)

    @field_validator("question")
    @classmethod
    def _strip_question(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("问题不能为空")
        return value


class ExportColumn(BaseModel):
    key: str
    label: str | None = None


class ExportRequest(BaseModel):
    format: Literal["xlsx", "csv"] = "xlsx"
    filename: str = "校园数据导出"
    columns: list[ExportColumn] = Field(min_length=1, max_length=50)
    rows: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
