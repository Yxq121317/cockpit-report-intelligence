"""DashScope Qwen-ASR-Realtime 语音转写。

浏览器上传 16 kHz / 单声道 / PCM16 WAV；本服务只把裸 PCM 通过 WebSocket
流式发送给 DashScope，音频不落盘。协议字段 ``input_audio_format`` 使用
``pcm``（PCM16 由 ``sample_rate=16000`` 与 16 bit 采样隐含）。未配置 Key
或上游异常时确定性降级为 mock，保证离线演示可用。
"""

from __future__ import annotations

import base64
import io
import json
import logging
import re
import time
import wave
from array import array
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit, urlunsplit
from uuid import uuid4

from websockets.sync.client import connect

from app.core.config import settings

logger = logging.getLogger(__name__)

_SECRET_PATTERN = re.compile(r"sk-[A-Za-z0-9._-]+|Bearer\s+\S+", re.IGNORECASE)
_MAX_AUDIO_SECONDS = 60
_CHUNK_BYTES = 3200  # 16 kHz / 16 bit / 单声道下约 100 ms


class ASRError(Exception):
    """可展示给用户的语音识别错误。"""


@dataclass(frozen=True)
class ASRResult:
    text: str
    mode: str
    degraded: bool
    model: str
    elapsed_ms: int
    error: str | None = None


def _sanitize_error(message: str) -> str:
    return _SECRET_PATTERN.sub("***", str(message))[:300]


def _resample_pcm16(pcm: bytes, source_rate: int, target_rate: int) -> bytes:
    if source_rate == target_rate:
        return pcm
    samples = array("h")
    samples.frombytes(pcm)
    if not samples:
        return b""
    ratio = target_rate / source_rate
    output_length = max(1, int(len(samples) * ratio))
    output = array("h", [0]) * output_length
    last = len(samples) - 1
    for index in range(output_length):
        position = index / ratio
        left = min(int(position), last)
        right = min(left + 1, last)
        fraction = position - left
        output[index] = int(
            samples[left] * (1 - fraction) + samples[right] * fraction
        )
    return output.tobytes()


def wav_to_pcm16(audio: bytes, *, target_rate: int = 16000) -> bytes:
    """把 WAV 转为目标采样率的单声道 PCM16，返回裸 PCM。"""
    if not audio:
        raise ASRError("没有收到音频数据")
    if len(audio) > 8 * 1024 * 1024:
        raise ASRError("音频过大，请控制在 8 MB 以内")
    try:
        with wave.open(io.BytesIO(audio), "rb") as handle:
            channels = handle.getnchannels()
            sample_width = handle.getsampwidth()
            source_rate = handle.getframerate()
            compression = handle.getcomptype()
            frame_count = handle.getnframes()
            raw = handle.readframes(frame_count)
    except (wave.Error, EOFError) as exc:
        raise ASRError("音频格式无法识别，请上传 WAV（PCM16）文件") from exc

    if compression != "NONE" or sample_width != 2:
        raise ASRError("仅支持 PCM16 编码的 WAV 音频")
    if source_rate <= 0 or frame_count <= 0:
        raise ASRError("音频内容为空")
    seconds = frame_count / source_rate
    if seconds > _MAX_AUDIO_SECONDS:
        raise ASRError("单次语音不能超过 60 秒")

    if channels > 1:
        samples = array("h")
        samples.frombytes(raw)
        mono = array("h", [0]) * (len(samples) // channels)
        for index in range(len(mono)):
            start = index * channels
            mono[index] = int(
                sum(samples[start : start + channels]) / channels
            )
        raw = mono.tobytes()

    return _resample_pcm16(raw, source_rate, target_rate)


class ASRService:
    """统一语音入口：真实 DashScope WebSocket + 确定性 mock 降级。"""

    def __init__(
        self,
        *,
        mode: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        model_name: str | None = None,
        language: str | None = None,
        sample_rate: int | None = None,
        audio_format: str | None = None,
        timeout: float | None = None,
    ) -> None:
        configured = (settings.ASR_MODE if mode is None else mode).strip().lower()
        self.configured_mode = (
            configured if configured in {"auto", "real", "mock"} else "auto"
        )
        self._api_key = (
            settings.ASR_API_KEY if api_key is None else api_key
        ).strip()
        self.base_url = (
            settings.ASR_BASE_URL if base_url is None else base_url
        ).strip()
        self.model_name = (
            (settings.ASR_MODEL_NAME if model_name is None else model_name).strip()
            or "qwen3-asr-flash-realtime"
        )
        self.language = (
            (settings.ASR_LANGUAGE if language is None else language).strip()
            or "zh"
        )
        self.sample_rate = int(
            settings.ASR_SAMPLE_RATE if sample_rate is None else sample_rate
        )
        self.audio_format = (
            (settings.ASR_AUDIO_FORMAT if audio_format is None else audio_format)
            .strip()
            or "pcm"
        )
        self.timeout = float(
            settings.ASR_TIMEOUT_SECONDS if timeout is None else timeout
        )
        self._fallback_reason = ""

        if self.configured_mode == "mock":
            self.effective_mode = "mock"
            self.degraded = False
        elif self.configured_mode == "auto":
            if self._api_key and self.base_url:
                self.effective_mode = "real"
                self.degraded = False
            else:
                self.effective_mode = "mock"
                self.degraded = True
                self._fallback_reason = "ASR_API_KEY 未配置，自动降级 mock"
        else:
            if not self._api_key:
                self.effective_mode = "mock"
                self.degraded = True
                self._fallback_reason = "real 模式缺少 ASR_API_KEY，降级 mock"
            elif not self.base_url:
                self.effective_mode = "mock"
                self.degraded = True
                self._fallback_reason = "real 模式缺少 ASR_BASE_URL，降级 mock"
            else:
                self.effective_mode = "real"
                self.degraded = False

    def status_line(self) -> str:
        if self.effective_mode == "real":
            return f"ASR: real（model={self.model_name}）"
        reason = (
            "显式配置 mock"
            if self.configured_mode == "mock"
            else self._fallback_reason
        )
        return f"ASR: mock（{reason}）"

    def meta(self) -> dict:
        return {
            "mode": self.effective_mode,
            "degraded": self.degraded,
            "model": self.model_name,
            "language": self.language,
            "sample_rate": self.sample_rate,
            "max_seconds": _MAX_AUDIO_SECONDS,
        }

    def transcribe(
        self,
        audio: bytes,
        *,
        language: str | None = None,
        mock_text: str | None = None,
    ) -> ASRResult:
        started = time.perf_counter()
        if self.effective_mode == "mock":
            return self._mock_result(
                started,
                degraded=self.degraded,
                error=self._fallback_reason or None,
                mock_text=mock_text,
            )

        try:
            pcm = wav_to_pcm16(audio, target_rate=self.sample_rate)
            text = self._transcribe_real(
                pcm,
                language=(language or self.language).strip() or "zh",
            )
            return ASRResult(
                text=text,
                mode="real",
                degraded=False,
                model=self.model_name,
                elapsed_ms=self._elapsed_ms(started),
                error=None if text else "未识别到语音内容",
            )
        except Exception as exc:  # noqa: BLE001 - 语音失败必须降级，不能阻断页面
            error = _sanitize_error(f"{type(exc).__name__}: {exc}")
            logger.warning("ASR 真实调用失败，降级 mock：%s", error)
            return self._mock_result(
                started,
                degraded=True,
                error=error,
                mock_text=mock_text,
            )

    def _mock_result(
        self,
        started: float,
        *,
        degraded: bool,
        error: str | None,
        mock_text: str | None,
    ) -> ASRResult:
        text = (mock_text or settings.ASR_MOCK_TEXT).strip()
        return ASRResult(
            text=text,
            mode="mock",
            degraded=degraded,
            model="mock",
            elapsed_ms=self._elapsed_ms(started),
            error=error,
        )

    def _transcribe_real(self, pcm: bytes, *, language: str) -> str:
        deadline = time.monotonic() + self.timeout
        url = self._websocket_url()
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "OpenAI-Beta": "realtime=v1",
        }
        transcripts: list[str] = []
        partial = ""

        with connect(
            url,
            additional_headers=headers,
            open_timeout=min(10.0, self.timeout),
            close_timeout=5,
            max_size=4 * 1024 * 1024,
        ) as websocket:
            # 服务端先发 session.created；个别兼容网关可能省略，收到即配置。
            first = self._recv_json(websocket, deadline)
            if first.get("type") == "error":
                raise ASRError(self._event_error(first))
            self._send_json(
                websocket,
                {
                    "type": "session.update",
                    "event_id": f"event_{uuid4().hex}",
                    "session": {
                        "modalities": ["text"],
                        "input_audio_format": self.audio_format,
                        "sample_rate": self.sample_rate,
                        "input_audio_transcription": {
                            "model": self.model_name,
                            "language": language,
                        },
                        "turn_detection": None,
                    },
                },
            )
            self._wait_for_session_updated(websocket, deadline)

            for offset in range(0, len(pcm), _CHUNK_BYTES):
                chunk = pcm[offset : offset + _CHUNK_BYTES]
                self._send_json(
                    websocket,
                    {
                        "type": "input_audio_buffer.append",
                        "event_id": f"event_{uuid4().hex}",
                        "audio": base64.b64encode(chunk).decode("ascii"),
                    },
                )
            self._send_json(
                websocket,
                {
                    "type": "input_audio_buffer.commit",
                    "event_id": f"event_{uuid4().hex}",
                },
            )
            self._send_json(
                websocket,
                {
                    "type": "session.finish",
                    "event_id": f"event_{uuid4().hex}",
                },
            )

            while time.monotonic() < deadline:
                event = self._recv_json(websocket, deadline)
                event_type = str(event.get("type") or "")
                if event_type == "error":
                    if transcripts:
                        break
                    raise ASRError(self._event_error(event))
                if event_type.endswith(
                    "input_audio_transcription.completed"
                ):
                    transcript = event.get("transcript")
                    if isinstance(transcript, str) and transcript.strip():
                        transcripts.append(transcript.strip())
                elif event_type.endswith("input_audio_transcription.text"):
                    text = event.get("text")
                    stash = event.get("stash")
                    partial = (
                        (text if isinstance(text, str) else "")
                        + (stash if isinstance(stash, str) else "")
                    ).strip()
                elif event_type.endswith("input_audio_transcription.failed"):
                    raise ASRError(
                        str(event.get("error") or "语音识别失败")
                    )
                elif event_type == "session.finished":
                    break

        text = "".join(transcripts).strip() or partial
        if not text:
            raise ASRError("未识别到语音内容")
        return text

    def _websocket_url(self) -> str:
        parts = urlsplit(self.base_url)
        query = parts.query
        if "model=" not in query:
            extra = urlencode({"model": self.model_name})
            query = f"{query}&{extra}" if query else extra
        return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))

    def _wait_for_session_updated(self, websocket, deadline: float) -> None:
        """等待 session.updated；兼容不返回该事件的网关。"""
        wait_until = min(deadline, time.monotonic() + 5)
        while time.monotonic() < wait_until:
            try:
                event = self._recv_json(websocket, wait_until)
            except ASRError:
                return
            event_type = str(event.get("type") or "")
            if event_type == "session.updated":
                return
            if event_type == "error":
                raise ASRError(self._event_error(event))
            if event_type:
                return

    @staticmethod
    def _send_json(websocket, payload: dict) -> None:
        websocket.send(json.dumps(payload, ensure_ascii=False))

    @staticmethod
    def _recv_json(websocket, deadline: float) -> dict:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ASRError("语音识别超时")
        try:
            raw = websocket.recv(timeout=remaining)
        except TimeoutError as exc:
            raise ASRError("语音识别超时") from exc
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        try:
            event = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ASRError("语音服务返回了无法解析的消息") from exc
        if not isinstance(event, dict):
            raise ASRError("语音服务返回了无法识别的消息")
        return event

    @staticmethod
    def _event_error(event: dict) -> str:
        error = event.get("error")
        if isinstance(error, dict):
            message = error.get("message") or error.get("code")
            if message:
                return _sanitize_error(str(message))
        return "语音服务返回错误"

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return max(0, int((time.perf_counter() - started) * 1000))


_asr_service: ASRService | None = None


def get_asr_service() -> ASRService:
    global _asr_service
    if _asr_service is None:
        _asr_service = ASRService()
    return _asr_service
