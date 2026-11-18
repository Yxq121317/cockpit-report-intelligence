"""飞书长连接：事件转换、去重与消息处理。"""

from __future__ import annotations

import json
from uuid import uuid4

from lark_oapi.api.im.v1 import P2ImMessageReceiveV1
from lark_oapi.event.dispatcher_handler import EventDispatcherHandler

from app.services.feishu_ws import FeishuLongConnection, incoming_from_sdk_event


def sdk_event(
    text: str,
    *,
    message_id: str | None = None,
    chat_id: str = "oc_test",
    sender_type: str = "user",
) -> P2ImMessageReceiveV1:
    return P2ImMessageReceiveV1(
        {
            "schema": "2.0",
            "header": {
                "event_id": uuid4().hex,
                "event_type": "im.message.receive_v1",
            },
            "event": {
                "sender": {
                    "sender_type": sender_type,
                    "sender_id": {"open_id": "ou_test"},
                },
                "message": {
                    "message_id": message_id or f"om_{uuid4().hex}",
                    "chat_id": chat_id,
                    "chat_type": "group",
                    "message_type": "text",
                    "content": json.dumps(
                        {"text": text}, ensure_ascii=False
                    ),
                    "mentions": [{"key": "@_user_1", "name": "校园问答"}],
                },
            },
        }
    )


def replies_for(service, message_id: str) -> list[dict]:
    return [
        item
        for item in service.mock_outbox
        if item["action"] == "reply" and item["target"] == message_id
    ]


def test_long_connection_event_strips_mention_and_extracts_text():
    message_id = f"om_{uuid4().hex}"
    message = incoming_from_sdk_event(
        sdk_event("@_user_1 上周八年级各班出勤率", message_id=message_id)
    )
    assert message is not None
    assert message.message_id == message_id
    assert message.chat_id == "oc_test"
    assert message.text == "上周八年级各班出勤率"
    assert message.message_type == "text"


def test_long_connection_message_is_processed(feishu):
    message_id = f"om_{uuid4().hex}"
    listener = FeishuLongConnection(feishu, executor=lambda task: task())
    listener.handle_event(
        sdk_event("@_user_1 上周八年级各班出勤率", message_id=message_id)
    )
    replies = replies_for(feishu, message_id)
    assert len(replies) == 1
    assert "出勤率" in json.dumps(replies[0]["content"], ensure_ascii=False)


def test_long_connection_ignores_messages_sent_by_app(feishu):
    listener = FeishuLongConnection(feishu, executor=lambda task: task())
    listener.handle_event(
        sdk_event("@_user_1 上周八年级各班出勤率", sender_type="app")
    )
    assert feishu.mock_outbox == []


def test_long_connection_duplicate_event_is_processed_once(feishu):
    message_id = f"om_{uuid4().hex}"
    event = sdk_event("@_user_1 上周八年级各班出勤率", message_id=message_id)
    listener = FeishuLongConnection(feishu, executor=lambda task: task())
    listener.handle_event(event)
    listener.handle_event(event)
    assert len(replies_for(feishu, message_id)) == 1


def test_long_connection_does_not_start_in_mock_mode(feishu):
    listener = FeishuLongConnection(feishu)
    assert listener.start() is False
    assert listener.status() == "disabled"


def test_lark_sdk_dispatches_message_event_to_long_connection(feishu):
    listener = FeishuLongConnection(feishu, executor=lambda task: task())
    handler = (
        EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(listener.handle_event)
        .build()
    )
    message_id = f"om_{uuid4().hex}"
    payload = {
        "schema": "2.0",
        "header": {
            "event_id": uuid4().hex,
            "event_type": "im.message.receive_v1",
            "token": "",
        },
        "event": {
            "sender": {
                "sender_type": "user",
                "sender_id": {"open_id": "ou_test"},
            },
            "message": {
                "message_id": message_id,
                "chat_id": "oc_test",
                "chat_type": "group",
                "message_type": "text",
                "content": json.dumps(
                    {"text": "@_user_1 上周八年级各班出勤率"},
                    ensure_ascii=False,
                ),
                "mentions": [{"key": "@_user_1", "name": "校园问答"}],
            },
        },
    }
    handler._do_without_validation(
        json.dumps(payload, ensure_ascii=False).encode("utf-8")
    )
    replies = replies_for(feishu, message_id)
    assert len(replies) == 1
    assert "出勤率" in json.dumps(replies[0]["content"], ensure_ascii=False)


def test_feishu_meta_reports_long_connection_state(client):
    data = client.get("/api/feishu/meta").json()["data"]
    assert data["long_connection"] == "disabled"
    assert data["long_connection_connected"] is False
