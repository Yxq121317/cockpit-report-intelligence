"""语音问答助手接口：意图识别 + Function Call 工具编排。"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.schemas.assistant import AssistantChatRequest
from app.schemas.common import ok
from app.services import assistant

router = APIRouter()


def _strip_server_paths(result: dict) -> dict:
    """接口响应不暴露服务器绝对路径，只保留下载地址。"""
    report = result.get("report")
    if isinstance(report, dict):
        report.pop("file_path", None)
    for call in result.get("tool_calls") or []:
        data = call.get("data")
        if isinstance(data, dict):
            data.pop("file_path", None)
    return result


@router.post("/chat")
def chat(
    payload: AssistantChatRequest,
    session: Session = Depends(get_db),
) -> dict:
    return ok(
        _strip_server_paths(
            assistant.chat(
                session,
                payload.question,
                force_report=payload.force_report,
            )
        )
    )


@router.get("/meta")
def meta() -> dict:
    return ok(assistant.get_meta())
