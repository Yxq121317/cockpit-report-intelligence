"""语音转写接口：接收浏览器 WAV，调用 DashScope 并返回文本。"""

from dataclasses import asdict

from fastapi import APIRouter, File, Form, UploadFile

from app.core.errors import ApiError
from app.schemas.common import ok
from app.services.asr import ASRError, get_asr_service

router = APIRouter()

_MAX_UPLOAD_BYTES = 8 * 1024 * 1024


@router.post("/transcribe")
def transcribe(
    audio: UploadFile = File(...),
    language: str | None = Form(default=None),
) -> dict:
    # 同步路由运行在线程池中，避免阻塞事件循环；WebSocket 客户端也是同步的。
    content = audio.file.read(_MAX_UPLOAD_BYTES + 1)
    if len(content) > _MAX_UPLOAD_BYTES:
        raise ApiError(1002, "音频过大，请控制在 8 MB 以内")
    try:
        result = get_asr_service().transcribe(content, language=language)
    except ASRError as exc:
        raise ApiError(1002, str(exc)) from exc
    return ok(asdict(result))
