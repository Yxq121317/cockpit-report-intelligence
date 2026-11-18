"""飞书群机器人接口：事件回调、报表推送与状态。"""

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse

from app.core.errors import ApiError
from app.schemas.common import ok
from app.schemas.feishu import FeishuPushRequest
from app.services.feishu import FeishuError, get_feishu_service
from app.services.feishu_ws import get_feishu_listener

router = APIRouter()


@router.post("/events")
async def events(request: Request, background: BackgroundTasks):
    """接收飞书事件订阅；URL 校验原样返回 challenge。"""
    raw_body = await request.body()
    service = get_feishu_service()
    try:
        response, message = service.parse_callback(raw_body, request.headers)
    except FeishuError as exc:
        raise ApiError(4010, str(exc), status_code=403) from exc
    if response is not None:
        return JSONResponse(response)
    if message is not None and service.mark_seen(message.message_id):
        background.add_task(service.process_message, message)
    return {"code": 0, "message": "ok"}


@router.post("/push/report")
def push_report(payload: FeishuPushRequest) -> dict:
    try:
        data = get_feishu_service().push_report(
            payload.report_id, payload.chat_id
        )
    except FeishuError as exc:
        message = str(exc)
        if any(word in message for word in ("不存在", "过期", "不合法")):
            raise ApiError(4040, message, status_code=404) from exc
        raise ApiError(4041, message, status_code=400) from exc
    return ok(data)


@router.get("/meta")
def meta() -> dict:
    data = get_feishu_service().meta()
    listener = get_feishu_listener()
    data["long_connection"] = listener.status()
    data["long_connection_connected"] = listener.connected()
    return ok(data)
