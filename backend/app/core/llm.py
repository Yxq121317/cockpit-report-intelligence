"""OpenAI 兼容 LLM 网关，支持 auto / real / mock 三种模式。"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Sequence

import httpx

from app.core import observability
from app.core.config import settings

logger = logging.getLogger(__name__)

Message = dict[str, str]

MOCK_CHAT_REPLY = "（Mock 模式）当前未连接大模型，Text2SQL 使用内置模板离线运行。"


@dataclass(frozen=True)
class LLMResult:
    content: str
    model: str
    mode: str
    degraded: bool
    input_tokens: int
    output_tokens: int
    latency_ms: int
    error: str | None = None
    tool_calls: list[dict] | None = None


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 2)


def _estimate_messages_tokens(messages: Sequence[Message]) -> int:
    total = sum(len(str(message.get("content", ""))) for message in messages)
    return max(1, total // 2)


def _normalize_tool_calls(raw_calls) -> list[dict] | None:
    """把 OpenAI 兼容的 tool_calls 统一为 {id, name, arguments}。"""
    if not isinstance(raw_calls, list):
        return None
    normalized: list[dict] = []
    for item in raw_calls:
        if not isinstance(item, dict):
            continue
        function = item.get("function")
        if not isinstance(function, dict):
            continue
        name = function.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        arguments = function.get("arguments")
        if isinstance(arguments, dict):
            arguments = json.dumps(arguments, ensure_ascii=False)
        if not isinstance(arguments, str):
            arguments = "{}"
        normalized.append(
            {
                "id": str(item.get("id") or f"call_{len(normalized) + 1}"),
                "name": name.strip(),
                "arguments": arguments,
            }
        )
    return normalized or None


class LLMGateway:
    """统一 LLM 入口；真实调用失败时自动降级为 mock 并标记。"""

    def __init__(
        self,
        *,
        mode: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        model_name: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        configured = (settings.LLM_MODE if mode is None else mode).strip().lower()
        self.configured_mode = (
            configured if configured in {"auto", "real", "mock"} else "auto"
        )
        self._api_key = (settings.api_key if api_key is None else api_key).strip()
        self.base_url = (
            settings.base_url if base_url is None else base_url
        ).strip().rstrip("/")
        self.model_name = (
            (settings.model_name if model_name is None else model_name).strip()
            or "deepseek-chat"
        )
        self.timeout = timeout
        self._fallback_reason = ""

        if self.configured_mode == "mock":
            self.effective_mode = "mock"
            self.degraded = False
        elif self.configured_mode == "auto":
            if self._api_key:
                self.effective_mode = "real"
                self.degraded = False
            else:
                self.effective_mode = "mock"
                self.degraded = True
                self._fallback_reason = "API_KEY 未配置，自动降级 mock"
        else:
            if not self._api_key:
                self.effective_mode = "mock"
                self.degraded = True
                self._fallback_reason = "real 模式缺少 API_KEY，降级 mock"
            elif not self.base_url:
                self.effective_mode = "mock"
                self.degraded = True
                self._fallback_reason = "real 模式缺少 BASE_URL，降级 mock"
            else:
                self.effective_mode = "real"
                self.degraded = False

    def status_line(self) -> str:
        if self.effective_mode == "real":
            return f"LLM: real（model={self.model_name}）"
        reason = (
            "显式配置 mock"
            if self.configured_mode == "mock"
            else self._fallback_reason
        )
        return f"LLM: mock（{reason}）"

    def complete(
        self,
        messages: Sequence[Message],
        *,
        task: str = "text2sql",
        prompt_version: str = "v1",
        temperature: float = 0.1,
        max_tokens: int = 800,
        tools: Sequence[dict] | None = None,
        tool_choice: str | None = None,
    ) -> LLMResult:
        started = time.perf_counter()

        if self.effective_mode == "real":
            parameters = {
                "temperature": temperature,
                "max_tokens": max_tokens,
                "tools": bool(tools),
            }
            if tool_choice:
                parameters["tool_choice"] = tool_choice
            with observability.generation(
                f"llm.{task}",
                model=self.model_name,
                input={"messages": list(messages)},
                metadata={
                    "task": task,
                    "prompt_version": prompt_version,
                },
                model_parameters=parameters,
            ) as generation:
                try:
                    (
                        content,
                        input_tokens,
                        output_tokens,
                        tool_calls,
                    ) = self._complete_real(
                        messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        tools=tools,
                        tool_choice=tool_choice,
                    )
                    result = LLMResult(
                        content=content,
                        model=self.model_name,
                        mode="real",
                        degraded=False,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        latency_ms=self._elapsed_ms(started),
                        tool_calls=tool_calls,
                    )
                    generation.end(
                        output={
                            "content": content,
                            "tool_calls": tool_calls,
                        },
                        usage={
                            "input": input_tokens,
                            "output": output_tokens,
                        },
                        metadata={"latency_ms": result.latency_ms},
                    )
                    return result
                except Exception as exc:  # noqa: BLE001 - 网关兜底所有上游异常
                    error = f"{type(exc).__name__}: {exc}"
                    generation.end(
                        level="ERROR",
                        status_message=type(exc).__name__,
                        metadata={"degraded": True},
                    )
                    logger.warning("LLM 真实调用失败，降级 mock：%s", error)
                    return self._mock_result(
                        messages,
                        started=started,
                        degraded=True,
                        error=error,
                    )

        return self._mock_result(
            messages,
            started=started,
            degraded=self.degraded,
            error=self._fallback_reason or None,
        )

    def _complete_real(
        self,
        messages: Sequence[Message],
        *,
        temperature: float,
        max_tokens: int,
        tools: Sequence[dict] | None = None,
        tool_choice: str | None = None,
    ) -> tuple[str, int, int, list[dict] | None]:
        payload = {
            "model": self.model_name,
            "messages": list(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if tools:
            payload["tools"] = list(tools)
            payload["tool_choice"] = tool_choice or "auto"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        response = httpx.post(
            f"{self.base_url}/chat/completions",
            headers=headers,
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()

        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("LLM 响应缺少 choices[0].message") from exc
        content = message.get("content")
        tool_calls = _normalize_tool_calls(message.get("tool_calls"))
        if (not isinstance(content, str) or not content.strip()) and not tool_calls:
            raise ValueError("LLM 返回空内容")
        content = content if isinstance(content, str) else ""

        usage = data.get("usage") or {}
        input_tokens = int(
            usage.get("prompt_tokens") or _estimate_messages_tokens(messages)
        )
        output_tokens = int(
            usage.get("completion_tokens") or _estimate_tokens(content)
        )
        return content, input_tokens, output_tokens, tool_calls

    def _mock_result(
        self,
        messages: Sequence[Message],
        *,
        started: float,
        degraded: bool,
        error: str | None,
    ) -> LLMResult:
        return LLMResult(
            content=MOCK_CHAT_REPLY,
            model="mock",
            mode="mock",
            degraded=degraded,
            input_tokens=_estimate_messages_tokens(messages),
            output_tokens=_estimate_tokens(MOCK_CHAT_REPLY),
            latency_ms=self._elapsed_ms(started),
            error=error,
        )

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return max(0, int((time.perf_counter() - started) * 1000))


_gateway: LLMGateway | None = None


def get_gateway() -> LLMGateway:
    global _gateway
    if _gateway is None:
        _gateway = LLMGateway()
    return _gateway
