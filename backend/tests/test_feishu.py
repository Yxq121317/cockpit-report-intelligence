"""飞书群机器人：事件回调、只读问答、报表推送与签名校验。"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from sqlalchemy import func, select

from app.models import Student
from app.services.feishu import FeishuError, FeishuService
from app.services.report import generate_report


def message_event(
    text: str,
    *,
    message_id: str | None = None,
    chat_id: str = "oc_test",
) -> dict:
    return {
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
                "message_id": message_id or f"om_{uuid4().hex}",
                "chat_id": chat_id,
                "chat_type": "group",
                "message_type": "text",
                "content": json.dumps({"text": text}, ensure_ascii=False),
                "mentions": [{"key": "@_user_1", "name": "校园问答"}],
            },
        },
    }


def replies_for(service: FeishuService, message_id: str) -> list[dict]:
    return [
        item
        for item in service.mock_outbox
        if item["action"] == "reply" and item["target"] == message_id
    ]


def test_feishu_url_verification_echoes_challenge(client, feishu):
    response = client.post(
        "/api/feishu/events",
        json={"type": "url_verification", "challenge": "challenge-123"},
    )
    assert response.status_code == 200
    assert response.json() == {"challenge": "challenge-123"}


def test_feishu_query_message_replies_with_campus_data(client, feishu):
    message_id = f"om_{uuid4().hex}"
    response = client.post(
        "/api/feishu/events",
        json=message_event("@_user_1 上周八年级各班出勤率", message_id=message_id),
    )
    assert response.status_code == 200
    replies = replies_for(feishu, message_id)
    assert len(replies) == 1
    assert replies[0]["msg_type"] == "interactive"
    card_text = json.dumps(replies[0]["content"], ensure_ascii=False)
    assert "出勤率" in card_text
    assert "八年级二班" in card_text


def test_feishu_duplicate_event_is_processed_once(client, feishu):
    event = message_event("上周八年级各班出勤率")
    message_id = event["event"]["message"]["message_id"]
    assert client.post("/api/feishu/events", json=event).status_code == 200
    assert client.post("/api/feishu/events", json=event).status_code == 200
    assert len(replies_for(feishu, message_id)) == 1


def test_feishu_data_entry_is_refused_without_writing(client, session, feishu):
    before = session.scalar(select(func.count()).select_from(Student))
    message_id = f"om_{uuid4().hex}"
    response = client.post(
        "/api/feishu/events",
        json=message_event(
            "新增学生 飞书同学，学号 FEISHU2026001，性别男，班级八年级一班",
            message_id=message_id,
        ),
    )
    assert response.status_code == 200
    after = session.scalar(select(func.count()).select_from(Student))
    assert after == before
    replies = replies_for(feishu, message_id)
    assert len(replies) == 1
    assert "网页端" in json.dumps(replies[0]["content"], ensure_ascii=False)


def test_feishu_report_request_replies_with_xlsx_file(client, feishu):
    message_id = f"om_{uuid4().hex}"
    response = client.post(
        "/api/feishu/events",
        json=message_event("生成八年级出勤报表", message_id=message_id),
    )
    assert response.status_code == 200
    replies = replies_for(feishu, message_id)
    assert [item["msg_type"] for item in replies] == ["interactive", "file"]
    file_item = replies[1]
    assert file_item["filename"].endswith(".xlsx")
    path = Path(file_item["file_path"])
    assert path.exists()
    assert path.read_bytes().startswith(b"PK")
    path.unlink(missing_ok=True)
    path.with_suffix(".json").unlink(missing_ok=True)


def test_feishu_push_report_uses_mock_outbox(client, session, feishu):
    result = generate_report(
        session,
        report_type="attendance_summary",
        target="八年级",
    )
    response = client.post(
        "/api/feishu/push/report",
        json={"report_id": result["report_id"]},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["mode"] == "mock"
    assert data["filename"].endswith(".xlsx")
    assert data["chat_id"] == "oc_mock_campus"
    pushes = [item for item in feishu.mock_outbox if item["action"] == "send"]
    assert [item["msg_type"] for item in pushes[-2:]] == ["interactive", "file"]
    assert pushes[-1]["filename"].endswith(".xlsx")

    path = Path(result["file_path"])
    path.unlink(missing_ok=True)
    path.with_suffix(".json").unlink(missing_ok=True)


def test_feishu_push_report_unknown_id_returns_business_error(client, feishu):
    response = client.post(
        "/api/feishu/push/report",
        json={"report_id": "a" * 32},
    )
    assert response.status_code == 404
    assert response.json()["code"] == 4040


def test_feishu_mock_never_calls_network(monkeypatch, feishu, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("mock 模式不应发起网络请求")

    monkeypatch.setattr(httpx, "post", forbidden)
    monkeypatch.setattr(httpx, "get", forbidden)

    feishu.reply_text("om_mock", "你好")
    feishu.reply_card("om_mock", {"elements": []})
    file_path = tmp_path / "report.xlsx"
    file_path.write_bytes(b"PK\x03\x04")
    feishu.reply_file("om_mock", file_path, "报表.xlsx")
    assert len(feishu.mock_outbox) == 3
    assert all(item["mode"] == "mock" for item in feishu.mock_outbox)


def test_feishu_meta_does_not_expose_secrets(client):
    data = client.get("/api/feishu/meta").json()["data"]
    assert data["mode"] == "mock"
    assert data["configured"] is False
    assert data["writes_allowed"] is False
    text = json.dumps(data, ensure_ascii=False).lower()
    assert "secret" not in text
    assert "encrypt" not in text
    assert "token" not in text


def _signature(timestamp: str, nonce: str, encrypt_key: str, body: bytes) -> str:
    return hashlib.sha256(
        f"{timestamp}{nonce}{encrypt_key}".encode() + body
    ).hexdigest()


def _encrypt(payload: dict, encrypt_key: str) -> bytes:
    key = hashlib.sha256(encrypt_key.encode()).digest()
    iv = b"0123456789abcdef"
    padder = padding.PKCS7(128).padder()
    padded = padder.update(json.dumps(payload).encode()) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return json.dumps(
        {"encrypt": base64.b64encode(iv + ciphertext).decode()}
    ).encode()


def test_feishu_signature_and_encrypted_event_roundtrip():
    service = FeishuService(
        mode="real",
        app_id="cli_test",
        app_secret="secret",
        verification_token="verify-token",
        encrypt_key="encrypt-key",
    )
    payload = message_event("上周八年级各班出勤率")
    payload["header"]["token"] = "verify-token"
    body = _encrypt(payload, "encrypt-key")
    timestamp = "1700000000"
    nonce = "nonce"
    headers = {
        "X-Lark-Request-Timestamp": timestamp,
        "X-Lark-Request-Nonce": nonce,
        "X-Lark-Signature": _signature(
            timestamp, nonce, "encrypt-key", body
        ),
    }
    assert service.verify_and_decode(body, headers) == payload


def test_feishu_invalid_signature_is_rejected():
    service = FeishuService(
        mode="real",
        app_id="cli_test",
        app_secret="secret",
        encrypt_key="encrypt-key",
    )
    body = json.dumps({"encrypt": "abc"}).encode()
    headers = {
        "X-Lark-Request-Timestamp": "1700000000",
        "X-Lark-Request-Nonce": "nonce",
        "X-Lark-Signature": "bad-signature",
    }
    with pytest.raises(FeishuError, match="签名"):
        service.verify_and_decode(body, headers)


def test_feishu_send_error_includes_upstream_permission_message(monkeypatch):
    service = FeishuService(
        mode="real",
        app_id="cli_test",
        app_secret="secret",
        api_base="https://open.feishu.cn",
    )
    monkeypatch.setattr(service, "_access_token", lambda: "token")

    class FakeResponse:
        status_code = 400

        def json(self):
            return {
                "code": 99991672,
                "msg": "Access denied: im:message:send_as_bot",
            }

    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: FakeResponse())
    with pytest.raises(FeishuError, match="im:message:send_as_bot"):
        service.send_text("oc_test", "测试")
