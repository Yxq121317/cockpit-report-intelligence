"""智能助手请求模型。"""

from pydantic import BaseModel, Field, field_validator


class AssistantChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    force_report: bool = False

    @field_validator("question")
    @classmethod
    def _strip_question(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("问题不能为空")
        return value
