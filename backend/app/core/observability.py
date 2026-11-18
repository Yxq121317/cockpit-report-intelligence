"""Langfuse LLM 观测：trace / span / generation / score。

设计要点：
- 默认关闭；只有 ``LANGFUSE_ENABLED=true`` 且同时配置公钥与私钥时才上报，
  未启用时不发起任何网络请求，保证 mock / 离线模式的确定性。
- 直接使用 Langfuse 公开的 ingestion API（``POST /api/public/ingestion``），
  不依赖 SDK 版本；事件先进入内存队列，由后台线程批量上报。
- 日志与状态只输出主机、环境与错误类型，不输出任何凭据或请求内容。
"""

from __future__ import annotations

import atexit
import base64
import json
import logging
import queue
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterator
from uuid import uuid4

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

Transport = Callable[[str, dict[str, Any], dict[str, str]], int]
_MAX_QUEUE_SIZE = 2048
_MAX_BATCH_SIZE = 500


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uid() -> str:
    return uuid4().hex


def _json_safe(value: Any) -> Any:
    """把日期、Decimal、ORM 对象等转成可 JSON 序列化的结构。"""

    if value is None:
        return None
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _truncate(value: Any, limit: int = 500) -> str:
    text = str(value)
    return text if len(text) <= limit else text[:limit] + "…"


@dataclass
class _TraceState:
    trace_id: str
    root: bool
    name: str
    observation_id: str | None = None
    parent_observation_id: str | None = None
    input: Any = None
    output: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    start_time: str = field(default_factory=_now)


_current_trace: ContextVar[_TraceState | None] = ContextVar(
    "langfuse_current_trace", default=None
)
_current_observation: ContextVar[str | None] = ContextVar(
    "langfuse_current_observation", default=None
)


class LangfuseClient:
    """批量上报 Langfuse ingestion 事件；未启用时所有方法均为空操作。"""

    def __init__(
        self,
        *,
        enabled: bool | None = None,
        public_key: str | None = None,
        secret_key: str | None = None,
        host: str | None = None,
        environment: str | None = None,
        release: str | None = None,
        timeout: float | None = None,
        capture_content: bool | None = None,
        flush_interval: float | None = None,
        background: bool = True,
        transport: Transport | None = None,
    ) -> None:
        enabled = settings.LANGFUSE_ENABLED if enabled is None else enabled
        self.public_key = (
            settings.LANGFUSE_PUBLIC_KEY if public_key is None else public_key
        ).strip()
        self.secret_key = (
            settings.LANGFUSE_SECRET_KEY if secret_key is None else secret_key
        ).strip()
        self.host = (
            settings.LANGFUSE_HOST if host is None else host
        ).strip().rstrip("/")
        self.environment = (
            settings.LANGFUSE_ENVIRONMENT if environment is None else environment
        ).strip()
        self.release = (
            settings.LANGFUSE_RELEASE if release is None else release
        ).strip()
        self.timeout = (
            settings.LANGFUSE_TIMEOUT_SECONDS if timeout is None else timeout
        )
        self.capture_content = (
            settings.LANGFUSE_CAPTURE_CONTENT
            if capture_content is None
            else capture_content
        )
        self.flush_interval = (
            settings.LANGFUSE_FLUSH_INTERVAL_SECONDS
            if flush_interval is None
            else flush_interval
        )
        self._transport = transport or self._http_transport
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue(
            maxsize=_MAX_QUEUE_SIZE
        )
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        reason = ""
        if not enabled:
            reason = "未启用"
        elif not self.public_key or not self.secret_key:
            reason = "缺少 Langfuse 凭据"
        elif not self.host:
            reason = "缺少 LANGFUSE_HOST"
        self.reason = reason
        self.enabled = not reason

        if self.enabled and background:
            self._thread = threading.Thread(
                target=self._worker,
                name="langfuse-flush",
                daemon=True,
            )
            self._thread.start()
        atexit.register(self.shutdown)

    # ------------------------------------------------------------------ 状态
    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "host": self.host,
            "environment": self.environment,
            "release": self.release,
            "reason": self.reason,
            "queued": self._queue.qsize(),
        }

    def status_line(self) -> str:
        if self.enabled:
            return (
                f"Langfuse: 开启（host={self.host}, env={self.environment}）"
            )
        return f"Langfuse: 关闭（{self.reason}）"

    # ------------------------------------------------------------------ 事件
    def emit(self, event_type: str, body: dict[str, Any]) -> None:
        if not self.enabled:
            return
        event = {
            "id": _uid(),
            "type": event_type,
            "timestamp": _now(),
            "body": _json_safe(body),
        }
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            logger.warning("Langfuse 队列已满，丢弃一条观测事件")

    def flush(self) -> int:
        """同步上报当前队列中的事件，返回成功上报的事件数。"""

        if not self.enabled:
            return 0
        sent_total = 0
        while True:
            batch: list[dict[str, Any]] = []
            while len(batch) < _MAX_BATCH_SIZE:
                try:
                    batch.append(self._queue.get_nowait())
                except queue.Empty:
                    break
            if not batch:
                break
            if not self._send(batch):
                break
            sent_total += len(batch)
        return sent_total

    def _send(self, batch: list[dict[str, Any]]) -> bool:
        token = base64.b64encode(
            f"{self.public_key}:{self.secret_key}".encode("utf-8")
        ).decode("ascii")
        headers = {
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
        }
        try:
            status_code = self._transport(
                f"{self.host}/api/public/ingestion",
                {"batch": batch},
                headers,
            )
        except Exception as exc:  # noqa: BLE001 - 观测失败不影响主流程
            logger.warning("Langfuse 上报失败：%s", type(exc).__name__)
            return False
        if status_code >= 400:
            logger.warning("Langfuse 上报失败：HTTP %s", status_code)
            return False
        return True

    def _http_transport(
        self, url: str, payload: dict[str, Any], headers: dict[str, str]
    ) -> int:
        response = httpx.post(
            url,
            json=payload,
            headers=headers,
            timeout=self.timeout,
        )
        return response.status_code

    def _worker(self) -> None:
        while not self._stop.wait(self.flush_interval):
            self.flush()

    def shutdown(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._thread = None
        self.flush()


_client: LangfuseClient | None = None
_client_lock = threading.Lock()


def get_client() -> LangfuseClient:
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = LangfuseClient()
    return _client


def set_client(client: LangfuseClient | None) -> None:
    global _client
    _client = client


def reset_for_tests() -> None:
    global _client
    client = _client
    _client = None
    _current_trace.set(None)
    _current_observation.set(None)
    if client is not None:
        client.shutdown()


# ---------------------------------------------------------------------- trace


@dataclass
class TraceContext:
    trace_id: str
    root: bool
    name: str
    observation_id: str | None = None
    _state: _TraceState | None = None
    _client: LangfuseClient | None = None

    def update(
        self,
        *,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> None:
        state = self._state
        if state is None:
            return
        if output is not None:
            state.output = _json_safe(output)
        if metadata:
            state.metadata.update(_json_safe(metadata))
        if tags:
            state.tags = list(dict.fromkeys([*state.tags, *tags]))


def _trace_body(state: _TraceState, client: LangfuseClient, output: Any) -> dict:
    return {
        "id": state.trace_id,
        "name": state.name,
        "timestamp": state.start_time,
        "input": state.input,
        "output": _json_safe(output),
        "metadata": _json_safe(state.metadata),
        "tags": state.tags,
        "environment": client.environment,
        "release": client.release,
    }


def _span_body(
    *,
    span_id: str,
    trace_id: str,
    parent_observation_id: str | None,
    name: str,
    start_time: str,
    input_value: Any,
    output: Any,
    metadata: dict[str, Any],
    level: str = "DEFAULT",
    status_message: str | None = None,
) -> dict:
    body: dict[str, Any] = {
        "id": span_id,
        "traceId": trace_id,
        "name": name,
        "startTime": start_time,
        "endTime": _now(),
        "input": _json_safe(input_value),
        "output": _json_safe(output),
        "metadata": _json_safe(metadata),
        "level": level,
    }
    if parent_observation_id:
        body["parentObservationId"] = parent_observation_id
    if status_message:
        body["statusMessage"] = status_message
    return body


@contextmanager
def trace(
    name: str,
    *,
    input: Any = None,
    metadata: dict[str, Any] | None = None,
    tags: list[str] | None = None,
) -> Iterator[TraceContext]:
    client = get_client()
    parent = _current_trace.get()
    parent_observation = _current_observation.get()
    root = parent is None
    state = _TraceState(
        trace_id=parent.trace_id if parent else _uid(),
        root=root,
        name=name,
        observation_id=None if root else _uid(),
        parent_observation_id=parent_observation if not root else None,
        input=_json_safe(input),
        metadata=_json_safe(metadata or {}),
        tags=list(tags or []),
    )

    if root:
        client.emit("trace-create", _trace_body(state, client, None))
    token_trace = _current_trace.set(state)
    token_observation = _current_observation.set(
        state.observation_id if not root else None
    )
    context = TraceContext(
        trace_id=state.trace_id,
        root=root,
        name=name,
        observation_id=state.observation_id,
        _state=state,
        _client=client,
    )
    try:
        yield context
    except Exception as exc:  # noqa: BLE001 - 记录后继续抛出
        state.metadata["error"] = type(exc).__name__
        if root:
            client.emit(
                "trace-create",
                _trace_body(state, client, {"error": type(exc).__name__}),
            )
        else:
            client.emit(
                "span-create",
                _span_body(
                    span_id=state.observation_id or _uid(),
                    trace_id=state.trace_id,
                    parent_observation_id=state.parent_observation_id,
                    name=state.name,
                    start_time=state.start_time,
                    input_value=state.input,
                    output=None,
                    metadata=state.metadata,
                    level="ERROR",
                    status_message=type(exc).__name__,
                ),
            )
        raise
    else:
        if root:
            client.emit(
                "trace-create",
                _trace_body(state, client, state.output),
            )
        else:
            client.emit(
                "span-create",
                _span_body(
                    span_id=state.observation_id or _uid(),
                    trace_id=state.trace_id,
                    parent_observation_id=state.parent_observation_id,
                    name=state.name,
                    start_time=state.start_time,
                    input_value=state.input,
                    output=state.output,
                    metadata=state.metadata,
                ),
            )
    finally:
        _current_observation.reset(token_observation)
        _current_trace.reset(token_trace)


@dataclass
class SpanHandle:
    _client: LangfuseClient
    _body: dict[str, Any]
    _ended: bool = False

    def end(
        self,
        *,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
        level: str = "DEFAULT",
        status_message: str | None = None,
    ) -> None:
        if self._ended:
            return
        self._ended = True
        body = dict(self._body)
        body["endTime"] = _now()
        body["output"] = _json_safe(output)
        body["level"] = level
        if metadata:
            body["metadata"] = {
                **body.get("metadata", {}),
                **_json_safe(metadata),
            }
        if status_message:
            body["statusMessage"] = status_message
        self._client.emit("span-create", body)


@contextmanager
def span(
    name: str,
    *,
    input: Any = None,
    metadata: dict[str, Any] | None = None,
) -> Iterator[SpanHandle]:
    client = get_client()
    parent = _current_trace.get()
    implicit = parent is None
    trace_tokens = None
    observation_tokens = None
    if implicit:
        parent = _TraceState(
            trace_id=_uid(),
            root=True,
            name=name,
            input=_json_safe(input),
            metadata=_json_safe(metadata or {}),
        )
        trace_tokens = _current_trace.set(parent)
        observation_tokens = _current_observation.set(None)
        client.emit("trace-create", _trace_body(parent, client, None))

    parent_observation = _current_observation.get()
    span_id = _uid()
    body = _span_body(
        span_id=span_id,
        trace_id=parent.trace_id,
        parent_observation_id=parent_observation,
        name=name,
        start_time=_now(),
        input_value=input,
        output=None,
        metadata=metadata or {},
    )
    handle = SpanHandle(_client=client, _body=body)
    token_span = _current_observation.set(span_id)
    try:
        yield handle
    except Exception as exc:  # noqa: BLE001
        handle.end(level="ERROR", status_message=type(exc).__name__)
        raise
    else:
        handle.end()
    finally:
        _current_observation.reset(token_span)
        if implicit:
            client.emit("trace-create", _trace_body(parent, client, parent.output))
            _current_observation.reset(observation_tokens)
            _current_trace.reset(trace_tokens)


@dataclass
class GenerationHandle:
    _client: LangfuseClient
    _body: dict[str, Any]
    _capture_content: bool
    _ended: bool = False

    def end(
        self,
        *,
        output: Any = None,
        usage: dict[str, int] | None = None,
        level: str = "DEFAULT",
        status_message: str | None = None,
        metadata: dict[str, Any] | None = None,
        model: str | None = None,
    ) -> None:
        if self._ended:
            return
        self._ended = True
        body = dict(self._body)
        body["endTime"] = _now()
        if self._capture_content:
            body["output"] = _json_safe(output)
        else:
            body["output"] = None
        if model:
            body["model"] = model
        if usage:
            total = usage.get("total")
            if total is None:
                total = int(usage.get("input", 0)) + int(
                    usage.get("output", 0)
                )
            body["usage"] = {
                "input": int(usage.get("input", 0)),
                "output": int(usage.get("output", 0)),
                "total": int(total),
                "unit": "TOKENS",
            }
        body["level"] = level
        if status_message:
            body["statusMessage"] = _truncate(status_message)
        if metadata:
            body["metadata"] = {
                **body.get("metadata", {}),
                **_json_safe(metadata),
            }
        self._client.emit("generation-create", body)


@contextmanager
def generation(
    name: str,
    *,
    model: str,
    input: Any = None,
    metadata: dict[str, Any] | None = None,
    model_parameters: dict[str, Any] | None = None,
) -> Iterator[GenerationHandle]:
    client = get_client()
    parent = _current_trace.get()
    implicit = parent is None
    trace_tokens = None
    observation_tokens = None
    if implicit:
        parent = _TraceState(
            trace_id=_uid(),
            root=True,
            name=name,
            input=_json_safe(input),
        )
        trace_tokens = _current_trace.set(parent)
        observation_tokens = _current_observation.set(None)
        client.emit("trace-create", _trace_body(parent, client, None))

    parent_observation = _current_observation.get()
    body: dict[str, Any] = {
        "id": _uid(),
        "traceId": parent.trace_id,
        "name": name,
        "startTime": _now(),
        "model": model,
        "input": _json_safe(input) if client.capture_content else None,
        "metadata": _json_safe(metadata or {}),
        "modelParameters": _json_safe(model_parameters or {}),
    }
    if parent_observation:
        body["parentObservationId"] = parent_observation
    handle = GenerationHandle(
        _client=client,
        _body=body,
        _capture_content=client.capture_content,
    )
    try:
        yield handle
    except Exception as exc:  # noqa: BLE001
        handle.end(level="ERROR", status_message=type(exc).__name__)
        raise
    else:
        handle.end()
    finally:
        if implicit:
            client.emit("trace-create", _trace_body(parent, client, parent.output))
            _current_observation.reset(observation_tokens)
            _current_trace.reset(trace_tokens)


# ---------------------------------------------------------------------- score


def score(
    name: str,
    value: Any,
    *,
    comment: str | None = None,
    data_type: str = "NUMERIC",
    observation_id: str | None = None,
) -> None:
    state = _current_trace.get()
    if state is None:
        return
    client = get_client()
    body: dict[str, Any] = {
        "id": _uid(),
        "traceId": state.trace_id,
        "name": name,
        "dataType": data_type,
    }
    if data_type == "NUMERIC":
        try:
            body["value"] = float(value)
        except (TypeError, ValueError):
            body["value"] = value
    else:
        body["value"] = value
    if comment:
        body["comment"] = _truncate(comment)
    parent_observation = observation_id or _current_observation.get()
    if parent_observation:
        body["observationId"] = parent_observation
    client.emit("score-create", body)


def add_metadata(**values: Any) -> None:
    state = _current_trace.get()
    if state is not None and values:
        state.metadata.update(_json_safe(values))


# ------------------------------------------------------------------ module API


def status() -> dict[str, Any]:
    return get_client().status()


def status_line() -> str:
    return get_client().status_line()


def flush() -> int:
    return get_client().flush()


def shutdown() -> None:
    get_client().shutdown()
