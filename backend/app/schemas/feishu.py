"""飞书接入的请求模型。"""

from pydantic import BaseModel, Field


class FeishuPushRequest(BaseModel):
    report_id: str = Field(min_length=32, max_length=32)
    chat_id: str | None = Field(default=None, max_length=128)
