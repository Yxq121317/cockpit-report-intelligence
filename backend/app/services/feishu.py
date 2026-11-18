"""飞书群机器人：事件订阅、群内问答与报表推送。

- mock 模式完全离线，所有发送只记录到内存 outbox，便于测试与演示。
- real 模式使用飞书开放平台 tenant_access_token 与消息 API。
- 群机器人默认只读：支持数据查询和报表生成；数据录入需到网页端确认。
- 不新增数据库表，凭据只从环境配置读取，日志与响应不输出任何密钥。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping

import httpx
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app.core.config import settings
from app.core.db import SessionLocal
from app.services import assistant
from app.services.report import REPORTS_DIR, ReportError, resolve_report

logger = logging.getLogger(__name__)

SEEN_LIMIT = 2000
SEEN_TTL_SECONDS = 3600
TOKEN_REFRESH_MARGIN = 60
MENTION_PATTERN = re.compile(r"@_user_\d+")


class FeishuError(Exception):
    """可展示给用户的飞书集成错误。"""


@dataclass(frozen=True)
class IncomingMessage:
    message_id: str
    chat_id: str
    text: str
    message_type: str


def normalize_incoming(
    *,
    message_id: Any,
    chat_id: Any,
    message_type: Any,
    content: Any,
    mentions: Any,
) -> IncomingMessage | None:
    """把回调 JSON 或长连接 SDK 事件统一为内部消息。"""
    message_id = str(message_id or "").strip()
    if not message_id:
        return None
    message_type = str(message_type or "").strip()
    text = ""
    if message_type == "text":
        parsed = content
        if isinstance(content, str):
            try:
                parsed = json.loads(content or "{}")
            except (TypeError, json.JSONDecodeError):
                parsed = {}
        if isinstance(parsed, dict):
            text = str(parsed.get("text") or "")
    for mention in mentions or []:
        if isinstance(mention, dict):
            key = str(mention.get("key") or "")
        else:
            key = str(getattr(mention, "key", "") or "")
        if key:
            text = text.replace(key, " ")
    text = MENTION_PATTERN.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return IncomingMessage(
        message_id=message_id,
        chat_id=str(chat_id or "").strip(),
        text=text,
        message_type=message_type,
    )


def _header(headers: Mapping[str, Any], name: str) -> str:
    target = name.lower()
    for key, value in headers.items():
        if str(key).lower() == target:
            return str(value)
    return ""


def _truncate(value: str, limit: int = 60) -> str:
    value = (value or "").replace("\n", " ").strip()
    return value if len(value) <= limit else f"{value[: limit - 1]}…"


def _display(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, float):
        return f"{value:.2f}".rstrip("0").rstrip(".")
    return _truncate(str(value), 80)


def _report_path(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(REPORTS_DIR.resolve()) or not resolved.exists():
        raise FeishuError("报表文件不存在或不在报表目录")
    return resolved


def build_reply_card(result: dict[str, Any]) -> dict[str, Any]:
    """把助手结果转换为飞书交互卡片，保留中文结论与少量结果行。"""
    intent = str(result.get("intent") or "")
    query = result.get("query") or {}
    report = result.get("report") or {}
    question = str(result.get("question") or "")

    if intent == "data_query":
        header_title = query.get("title") or "校园数据查询"
        template = "blue"
    elif intent == "report_generation":
        header_title = report.get("title") or "校园报表"
        template = "green"
    else:
        header_title = "校园数据问答"
        template = "grey"

    elements: list[dict[str, Any]] = [
        {"tag": "markdown", "content": f"**问题**：{_truncate(question, 120)}"}
    ]

    if intent == "data_query":
        answer = query.get("answer") or result.get("reply") or ""
        elements.append({"tag": "markdown", "content": answer})
        columns = query.get("columns") or []
        rows = query.get("rows") or []
        lines: list[str] = []
        for index, row in enumerate(rows[:8], start=1):
            if not isinstance(row, dict):
                continue
            pairs = []
            for column in columns[:6]:
                if not isinstance(column, dict):
                    continue
                key = column.get("key")
                if not key:
                    continue
                label = column.get("label") or key
                pairs.append(f"{label}：{_display(row.get(key))}")
            if pairs:
                lines.append(f"{index}. " + " · ".join(pairs))
        if lines:
            table_text = "\n".join(lines)
            if len(rows) > 8 or len(columns) > 6:
                table_text += (
                    f"\n（共 {len(rows)} 行 / {len(columns)} 列，"
                    "仅展示前 8 行、前 6 列）"
                )
            elements.append({"tag": "markdown", "content": table_text})
        chart = query.get("chart") or {}
        if chart.get("type") and chart.get("type") != "table":
            elements.append(
                {
                    "tag": "note",
                    "elements": [
                        {
                            "tag": "plain_text",
                            "content": "图表请在网页端查看；完整明细可在网页端导出 XLSX / CSV。",
                        }
                    ],
                }
            )
    elif intent == "report_generation":
        elements.append(
            {
                "tag": "markdown",
                "content": (
                    f"{result.get('reply') or '报表已生成。'}\n"
                    f"共 {report.get('row_count', 0)} 行，XLSX 文件见下一条消息。"
                ),
            }
        )
    else:
        elements.append(
            {"tag": "markdown", "content": result.get("reply") or assistant.AGENT_HELP}
        )

    elements.append(
        {
            "tag": "note",
            "elements": [
                {
                    "tag": "plain_text",
                    "content": "统计数字来自本次查询 · 群机器人默认只读 · 录入请在网页端确认",
                }
            ],
        }
    )
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": template,
            "title": {"tag": "plain_text", "content": _truncate(header_title, 48)},
        },
        "elements": elements,
    }


def build_report_push_card(title: str, row_count: int) -> dict[str, Any]:
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "green",
            "title": {"tag": "plain_text", "content": _truncate(title, 48)},
        },
        "elements": [
            {
                "tag": "markdown",
                "content": f"报表已生成，共 {row_count} 行。XLSX 文件见下一条消息。",
            },
            {
                "tag": "note",
                "elements": [
                    {"tag": "plain_text", "content": "由校园数据问答自动推送"}
                ],
            },
        ],
    }


class FeishuService:
    """飞书开放平台客户端；mock 模式不发起任何网络请求。"""

    def __init__(
        self,
        *,
        mode: str | None = None,
        app_id: str | None = None,
        app_secret: str | None = None,
        api_base: str | None = None,
        verification_token: str | None = None,
        encrypt_key: str | None = None,
        default_chat_id: str | None = None,
        mock_chat_id: str | None = None,
        allow_writes: bool | None = None,
        timeout: float | None = None,
    ) -> None:
        configured = (
            settings.FEISHU_MODE if mode is None else mode
        ).strip().lower()
        self.configured_mode = (
            configured if configured in {"auto", "real", "mock"} else "auto"
        )
        self.app_id = (
            settings.FEISHU_APP_ID if app_id is None else app_id
        ).strip()
        self.app_secret = (
            settings.FEISHU_APP_SECRET if app_secret is None else app_secret
        ).strip()
        self.api_base = (
            settings.FEISHU_API_BASE if api_base is None else api_base
        ).strip().rstrip("/")
        self.verification_token = (
            settings.FEISHU_VERIFICATION_TOKEN
            if verification_token is None
            else verification_token
        ).strip()
        self.encrypt_key = (
            settings.FEISHU_ENCRYPT_KEY if encrypt_key is None else encrypt_key
        ).strip()
        self.default_chat_id = (
            settings.FEISHU_DEFAULT_CHAT_ID
            if default_chat_id is None
            else default_chat_id
        ).strip()
        self.mock_chat_id = (
            settings.FEISHU_MOCK_CHAT_ID
            if mock_chat_id is None
            else mock_chat_id
        ).strip() or "oc_mock_campus"
        self.allow_writes = (
            settings.FEISHU_ALLOW_WRITES if allow_writes is None else allow_writes
        )
        self.timeout = (
            settings.FEISHU_TIMEOUT_SECONDS if timeout is None else timeout
        )

        self.mock_outbox: list[dict[str, Any]] = []
        self._seen: OrderedDict[str, float] = OrderedDict()
        self._lock = threading.Lock()
        self._token = ""
        self._token_expires_at = 0.0
        self._degraded = False
        self._fallback_reason = ""

        if self.configured_mode == "mock":
            self.effective_mode = "mock"
        elif self.configured_mode == "auto":
            if self.app_id and self.app_secret:
                self.effective_mode = "real"
            else:
                self.effective_mode = "mock"
                self._degraded = True
                self._fallback_reason = "未配置飞书应用凭据，自动降级 mock"
        else:
            if self.app_id and self.app_secret and self.api_base:
                self.effective_mode = "real"
            else:
                self.effective_mode = "mock"
                self._degraded = True
                self._fallback_reason = "real 模式缺少飞书应用凭据，降级 mock"

    def status_line(self) -> str:
        if self.effective_mode == "real":
            return "飞书: real（群机器人与报表推送已启用）"
        reason = (
            "显式配置 mock"
            if self.configured_mode == "mock"
            else self._fallback_reason
        )
        return f"飞书: mock（{reason}）"

    def meta(self) -> dict[str, Any]:
        return {
            "mode": self.effective_mode,
            "degraded": self._degraded,
            "configured": bool(self.app_id and self.app_secret),
            "default_chat_configured": bool(self.default_chat_id),
            "writes_allowed": bool(self.allow_writes),
        }

    # ---------- 事件校验与解析 ----------

    def verify_and_decode(
        self,
        raw_body: bytes,
        headers: Mapping[str, Any],
    ) -> dict[str, Any]:
        body = raw_body if isinstance(raw_body, bytes) else str(raw_body).encode()
        if self.encrypt_key:
            timestamp = _header(headers, "X-Lark-Request-Timestamp")
            nonce = _header(headers, "X-Lark-Request-Nonce")
            signature = _header(headers, "X-Lark-Signature")
            expected = hashlib.sha256(
                f"{timestamp}{nonce}{self.encrypt_key}".encode() + body
            ).hexdigest()
            if not signature or not hmac.compare_digest(signature, expected):
                raise FeishuError("飞书事件签名校验失败")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FeishuError("飞书事件体不是合法 JSON") from exc
        if not isinstance(payload, dict):
            raise FeishuError("飞书事件体格式不正确")
        if "encrypt" in payload:
            if not self.encrypt_key:
                raise FeishuError("收到加密事件，但未配置 FEISHU_ENCRYPT_KEY")
            payload = self._decrypt(str(payload["encrypt"]))
        self._verify_token(payload)
        return payload

    def _decrypt(self, encrypted: str) -> dict[str, Any]:
        try:
            data = base64.b64decode(encrypted)
            key = hashlib.sha256(self.encrypt_key.encode()).digest()
            iv, ciphertext = data[:16], data[16:]
            decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
            padded = decryptor.update(ciphertext) + decryptor.finalize()
            unpadder = padding.PKCS7(128).unpadder()
            plain = unpadder.update(padded) + unpadder.finalize()
            payload = json.loads(plain.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - 解密失败统一脱敏
            raise FeishuError("飞书事件解密失败") from exc
        if not isinstance(payload, dict):
            raise FeishuError("飞书事件解密后格式不正确")
        return payload

    def _verify_token(self, payload: dict[str, Any]) -> None:
        if not self.verification_token:
            return
        header = payload.get("header") or {}
        token = payload.get("token") or (
            header.get("token") if isinstance(header, dict) else ""
        )
        if not token or not hmac.compare_digest(
            str(token), self.verification_token
        ):
            raise FeishuError("飞书事件 Token 校验失败")

    def parse_callback(
        self,
        raw_body: bytes,
        headers: Mapping[str, Any],
    ) -> tuple[dict[str, Any] | None, IncomingMessage | None]:
        payload = self.verify_and_decode(raw_body, headers)
        if payload.get("type") == "url_verification":
            return {"challenge": str(payload.get("challenge") or "")}, None
        return None, self.extract_message(payload)

    def extract_message(self, payload: dict[str, Any]) -> IncomingMessage | None:
        if payload.get("schema") != "2.0":
            return None
        header = payload.get("header") or {}
        if header.get("event_type") != "im.message.receive_v1":
            return None
        event = payload.get("event") or {}
        message = event.get("message") or {}
        return normalize_incoming(
            message_id=message.get("message_id"),
            chat_id=message.get("chat_id"),
            message_type=message.get("message_type"),
            content=message.get("content"),
            mentions=message.get("mentions") or [],
        )

    def mark_seen(self, message_id: str) -> bool:
        now = time.time()
        with self._lock:
            self._purge_seen(now)
            if message_id in self._seen:
                return False
            self._seen[message_id] = now
            while len(self._seen) > SEEN_LIMIT:
                self._seen.popitem(last=False)
            return True

    def clear_seen(self) -> None:
        with self._lock:
            self._seen.clear()

    def _purge_seen(self, now: float) -> None:
        expired = [
            key
            for key, seen_at in self._seen.items()
            if now - seen_at > SEEN_TTL_SECONDS
        ]
        for key in expired:
            self._seen.pop(key, None)

    # ---------- 消息处理 ----------

    def process_message(self, message: IncomingMessage) -> None:
        try:
            if message.message_type != "text" or not message.text:
                self.reply_text(
                    message.message_id,
                    "目前只支持文本提问，请直接发送校园数据问题。",
                )
                return
            intent = assistant.classify_intent(message.text)
            if intent.intent == "data_entry" and not self.allow_writes:
                self.reply_text(
                    message.message_id,
                    "群机器人默认只读，数据录入不会自动执行。"
                    "请打开网页端，确认信息后再提交。",
                )
                return
            with SessionLocal() as session:
                result = assistant.chat(
                    session,
                    message.text,
                    commit=self.allow_writes,
                )
            self.reply_card(message.message_id, build_reply_card(result))
            report = result.get("report") or {}
            file_path = report.get("file_path")
            if file_path:
                try:
                    path = _report_path(Path(str(file_path)))
                    self.reply_file(
                        message.message_id,
                        path,
                        str(report.get("filename") or path.name),
                    )
                except FeishuError as exc:
                    logger.warning("飞书报表文件发送跳过：%s", exc)
        except FeishuError as exc:
            logger.warning("飞书消息处理失败：%s", exc)
            self._safe_reply(message.message_id, f"处理失败：{exc}")
        except Exception:  # noqa: BLE001 - 群机器人兜底，不泄露内部堆栈
            logger.exception("飞书消息处理异常")
            self._safe_reply(
                message.message_id,
                "处理失败，请稍后重试，或到网页端操作。",
            )

    def _safe_reply(self, message_id: str, text: str) -> None:
        try:
            self.reply_text(message_id, text)
        except Exception:  # noqa: BLE001 - 错误提示失败时不再递归
            logger.warning("飞书错误提示发送失败")

    # ---------- 发送接口 ----------

    def reply_text(self, message_id: str, text: str) -> dict[str, Any]:
        return self.reply_card(
            message_id,
            {
                "config": {"wide_screen_mode": True},
                "elements": [
                    {
                        "tag": "markdown",
                        "content": text,
                    }
                ],
            },
        )

    def reply_card(
        self, message_id: str, card: dict[str, Any]
    ) -> dict[str, Any]:
        if self.effective_mode == "mock":
            return self._record(
                action="reply",
                target=message_id,
                msg_type="interactive",
                content=card,
            )
        return self._reply_message(message_id, "interactive", card)

    def reply_file(
        self, message_id: str, path: Path, filename: str
    ) -> dict[str, Any]:
        path = Path(path)
        if self.effective_mode == "mock":
            if not path.exists():
                raise FeishuError("报表文件不存在")
            return self._record(
                action="reply",
                target=message_id,
                msg_type="file",
                filename=filename,
                file_path=str(path),
            )
        path = _report_path(path)
        file_key = self._upload_file(path, filename)
        return self._reply_message(
            message_id, "file", {"file_key": file_key}
        )

    def send_card(self, chat_id: str, card: dict[str, Any]) -> dict[str, Any]:
        if self.effective_mode == "mock":
            return self._record(
                action="send",
                target=chat_id,
                msg_type="interactive",
                content=card,
            )
        return self._send_message(chat_id, "interactive", card)

    def send_text(self, chat_id: str, text: str) -> dict[str, Any]:
        if self.effective_mode == "mock":
            return self._record(
                action="send",
                target=chat_id,
                msg_type="text",
                content={"text": text},
            )
        return self._send_message(chat_id, "text", {"text": text})

    def send_file(
        self, chat_id: str, path: Path, filename: str
    ) -> dict[str, Any]:
        path = Path(path)
        if self.effective_mode == "mock":
            if not path.exists():
                raise FeishuError("报表文件不存在")
            return self._record(
                action="send",
                target=chat_id,
                msg_type="file",
                filename=filename,
                file_path=str(path),
            )
        path = _report_path(path)
        file_key = self._upload_file(path, filename)
        return self._send_message(chat_id, "file", {"file_key": file_key})

    def push_report(
        self, report_id: str, chat_id: str | None = None
    ) -> dict[str, Any]:
        try:
            path, metadata = resolve_report(report_id)
        except ReportError as exc:
            raise FeishuError(str(exc)) from exc
        target = (
            (chat_id or "").strip()
            or self.default_chat_id
            or (self.mock_chat_id if self.effective_mode == "mock" else "")
        )
        if not target:
            raise FeishuError(
                "未配置飞书群 chat_id，请在请求中提供或设置 FEISHU_DEFAULT_CHAT_ID"
            )
        title = str(metadata.get("title") or "校园报表")
        row_count = int(metadata.get("row_count") or 0)
        filename = str(metadata.get("filename") or path.name)
        self.send_card(target, build_report_push_card(title, row_count))
        self.send_file(target, path, filename)
        return {
            "report_id": report_id,
            "title": title,
            "filename": filename,
            "row_count": row_count,
            "chat_id": target,
            "mode": self.effective_mode,
            "degraded": self._degraded,
        }

    def _record(
        self,
        *,
        action: str,
        target: str,
        msg_type: str,
        content: dict[str, Any] | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "action": action,
            "target": target,
            "msg_type": msg_type,
            "mode": "mock",
            **extra,
        }
        if content is not None:
            entry["content"] = content
        self.mock_outbox.append(entry)
        return {
            "message_id": f"mock_{len(self.mock_outbox)}",
            "mode": "mock",
        }

    # ---------- 飞书开放平台 ----------

    def _access_token(self) -> str:
        now = time.time()
        with self._lock:
            if self._token and now < self._token_expires_at - TOKEN_REFRESH_MARGIN:
                return self._token
            try:
                response = httpx.post(
                    f"{self.api_base}/open-apis/auth/v3/tenant_access_token/internal",
                    json={
                        "app_id": self.app_id,
                        "app_secret": self.app_secret,
                    },
                    timeout=self.timeout,
                )
            except Exception as exc:  # noqa: BLE001 - 上游异常统一脱敏
                raise FeishuError("获取飞书访问令牌失败，请检查网络") from exc
            try:
                data = response.json()
            except ValueError:
                data = {}
            token = (
                data.get("tenant_access_token")
                if isinstance(data, dict)
                else None
            )
            if (
                response.status_code != 200
                or not isinstance(data, dict)
                or data.get("code") != 0
                or not token
            ):
                detail = (
                    str(data.get("msg") or data.get("code") or "")
                    if isinstance(data, dict)
                    else ""
                )
                raise FeishuError(
                    "获取飞书访问令牌失败："
                    f"{detail or f'HTTP {response.status_code}'}"
                )
            self._token = str(token)
            self._token_expires_at = now + int(data.get("expire") or 7200)
            return self._token

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = httpx.post(
                f"{self.api_base}{path}",
                headers={
                    "Authorization": f"Bearer {self._access_token()}",
                    "Content-Type": "application/json; charset=utf-8",
                },
                json=payload,
                timeout=self.timeout,
            )
        except FeishuError:
            raise
        except Exception as exc:  # noqa: BLE001 - 上游异常统一脱敏
            raise FeishuError("飞书消息发送失败，请检查网络") from exc
        try:
            data = response.json()
        except ValueError:
            data = {}
        if (
            response.status_code >= 400
            or not isinstance(data, dict)
            or data.get("code") != 0
        ):
            detail = (
                str(data.get("msg") or data.get("code") or "")
                if isinstance(data, dict)
                else ""
            )
            raise FeishuError(
                "飞书消息发送失败："
                f"{detail or f'HTTP {response.status_code}'}"
            )
        return data

    def _reply_message(
        self,
        message_id: str,
        msg_type: str,
        content: dict[str, Any],
    ) -> dict[str, Any]:
        return self._post(
            f"/open-apis/im/v1/messages/{message_id}/reply",
            {
                "content": json.dumps(content, ensure_ascii=False),
                "msg_type": msg_type,
            },
        )

    def _send_message(
        self,
        chat_id: str,
        msg_type: str,
        content: dict[str, Any],
    ) -> dict[str, Any]:
        return self._post(
            "/open-apis/im/v1/messages?receive_id_type=chat_id",
            {
                "receive_id": chat_id,
                "msg_type": msg_type,
                "content": json.dumps(content, ensure_ascii=False),
            },
        )

    def _upload_file(self, path: Path, filename: str) -> str:
        try:
            with path.open("rb") as handle:
                response = httpx.post(
                    f"{self.api_base}/open-apis/im/v1/files",
                    headers={
                        "Authorization": f"Bearer {self._access_token()}",
                    },
                    data={"file_type": "stream", "file_name": filename},
                    files={
                        "file": (
                            filename,
                            handle,
                            "application/vnd.openxmlformats-officedocument."
                            "spreadsheetml.sheet",
                        )
                    },
                    timeout=self.timeout,
                )
        except FeishuError:
            raise
        except Exception as exc:  # noqa: BLE001 - 上游异常统一脱敏
            raise FeishuError("飞书报表文件上传失败，请检查网络与机器人权限") from exc
        try:
            data = response.json()
        except ValueError:
            data = {}
        if (
            response.status_code >= 400
            or not isinstance(data, dict)
            or data.get("code") != 0
        ):
            detail = (
                str(data.get("msg") or data.get("code") or "")
                if isinstance(data, dict)
                else ""
            )
            raise FeishuError(
                "飞书报表文件上传失败："
                f"{detail or f'HTTP {response.status_code}'}"
            )
        payload = data.get("data") if isinstance(data.get("data"), dict) else {}
        file_key = payload.get("file_key")
        if not file_key:
            raise FeishuError("飞书报表文件上传失败：未返回 file_key")
        return str(file_key)


_service: FeishuService | None = None


def get_feishu_service() -> FeishuService:
    global _service
    if _service is None:
        _service = FeishuService()
    return _service
