"""飞书长连接：通过 WebSocket 接收群消息事件。

长连接由飞书开放平台主动推送事件，无需公网回调地址。监听线程为守护线程，
事件解析后交给独立工作线程执行，避免阻塞飞书心跳与后续事件。
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from app.core.config import settings
from app.services.feishu import (
    FeishuService,
    get_feishu_service,
    normalize_incoming,
)

logger = logging.getLogger(__name__)

STATUS_DISABLED = "disabled"
STATUS_STARTING = "starting"
STATUS_RUNNING = "running"
STATUS_ERROR = "error"
STATUS_STOPPED = "stopped"


def _run_in_thread(task: Callable[[], None]) -> None:
    threading.Thread(
        target=task,
        name="feishu-long-connection-worker",
        daemon=True,
    ).start()


def incoming_from_sdk_event(event: Any) -> Any:
    """把 lark-oapi 的 ``P2ImMessageReceiveV1`` 转为 ``IncomingMessage``。"""
    data = getattr(event, "event", None)
    if data is None:
        return None
    sender = getattr(data, "sender", None)
    if str(getattr(sender, "sender_type", "") or "") == "app":
        return None
    message = getattr(data, "message", None)
    if message is None:
        return None
    return normalize_incoming(
        message_id=getattr(message, "message_id", ""),
        chat_id=getattr(message, "chat_id", ""),
        message_type=getattr(message, "message_type", ""),
        content=getattr(message, "content", ""),
        mentions=getattr(message, "mentions", None) or [],
    )


class FeishuLongConnection:
    """封装 lark-oapi WebSocket 客户端，并复用现有消息处理链路。"""

    def __init__(
        self,
        service: FeishuService,
        *,
        executor: Callable[[Callable[[], None]], None] | None = None,
    ) -> None:
        self.service = service
        self._executor = executor or _run_in_thread
        self._client: Any = None
        self._thread: threading.Thread | None = None
        self._status = STATUS_DISABLED
        self._status_lock = threading.Lock()

    def status(self) -> str:
        with self._status_lock:
            return self._status

    def connected(self) -> bool:
        """只读探测 SDK 的连接对象，供 /meta 展示，不暴露连接地址。"""
        client = self._client
        return bool(client is not None and getattr(client, "_conn", None))

    def _set_status(self, status: str) -> None:
        with self._status_lock:
            self._status = status

    def start(self) -> bool:
        """启动监听线程；mock 模式或显式关闭时不发起任何网络请求。"""
        if not settings.FEISHU_LONG_CONNECTION:
            self._set_status(STATUS_DISABLED)
            return False
        if self.service.effective_mode != "real":
            self._set_status(STATUS_DISABLED)
            return False
        with self._status_lock:
            if self._thread is not None and self._thread.is_alive():
                return True
            self._status = STATUS_STARTING
            thread = threading.Thread(
                target=self._run,
                name="feishu-long-connection",
                daemon=True,
            )
            self._thread = thread
        thread.start()
        return True

    def _run(self) -> None:
        try:
            from lark_oapi import LogLevel
            from lark_oapi.event.dispatcher_handler import (
                EventDispatcherHandler,
            )
            from lark_oapi.ws import Client
        except ImportError:
            self._set_status(STATUS_ERROR)
            logger.error("飞书长连接未启动：缺少 lark-oapi 依赖")
            return
        try:
            handler = (
                EventDispatcherHandler.builder("", "")
                .register_p2_im_message_receive_v1(self.handle_event)
                .build()
            )
            client = Client(
                self.service.app_id,
                self.service.app_secret,
                event_handler=handler,
                domain=self.service.api_base,
                log_level=LogLevel.WARNING,
            )
        except Exception:  # noqa: BLE001 - 初始化失败不泄露凭据
            self._set_status(STATUS_ERROR)
            logger.exception("飞书长连接初始化失败")
            return
        self._client = client
        self._set_status(STATUS_RUNNING)
        logger.info("飞书长连接已启动，等待群消息事件")
        try:
            client.start()
        except Exception:  # noqa: BLE001 - 断线重连由 SDK 负责
            self._set_status(STATUS_ERROR)
            logger.exception("飞书长连接异常退出")
        else:
            self._set_status(STATUS_STOPPED)

    def handle_event(self, event: Any) -> None:
        """飞书 SDK 回调入口：解析、去重后异步处理，立即返回确认。"""
        message = incoming_from_sdk_event(event)
        if message is None:
            return
        if not self.service.mark_seen(message.message_id):
            return
        logger.info(
            "收到飞书长连接消息 | chat_id=%s message_id=%s type=%s",
            message.chat_id,
            message.message_id,
            message.message_type,
        )
        try:
            self._executor(lambda: self.service.process_message(message))
        except Exception:  # noqa: BLE001 - 派发失败时同步兜底
            logger.exception("飞书长连接消息派发失败，改为同步处理")
            self.service.process_message(message)

    def stop(self) -> None:
        """尽力断开长连接；守护线程会随进程退出。"""
        client = self._client
        thread = self._thread
        if client is None or thread is None or not thread.is_alive():
            return
        try:
            client._auto_reconnect = False
            from lark_oapi.ws.client import loop

            if loop.is_running():
                loop.call_soon_threadsafe(
                    lambda: loop.create_task(client._disconnect())
                )
        except Exception:  # noqa: BLE001 - 关闭阶段不再影响进程退出
            logger.debug("飞书长连接关闭时出现异常", exc_info=True)
        self._set_status(STATUS_STOPPED)


_listener: FeishuLongConnection | None = None


def get_feishu_listener() -> FeishuLongConnection:
    global _listener
    if _listener is None:
        _listener = FeishuLongConnection(get_feishu_service())
    return _listener
