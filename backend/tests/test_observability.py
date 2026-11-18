"""Langfuse 观测客户端：离线关闭、事件结构、批量上报与脱敏。"""

from __future__ import annotations

import pytest

from app.core import observability


@pytest.fixture(autouse=True)
def _reset_observability():
    observability.reset_for_tests()
    yield
    observability.reset_for_tests()


def build_client(events: list[dict], **overrides) -> observability.LangfuseClient:
    def transport(url: str, payload: dict, headers: dict) -> int:
        events.append({"url": url, "payload": payload, "headers": headers})
        return 207

    options = {
        "enabled": True,
        "public_key": "pk-test",
        "secret_key": "sk-test",
        "host": "https://langfuse.example",
        "environment": "test",
        "release": "test-release",
        "background": False,
        "transport": transport,
    }
    options.update(overrides)
    return observability.LangfuseClient(**options)


def test_disabled_client_does_not_touch_network():
    events: list[tuple] = []

    def transport(url, payload, headers):
        events.append((url, payload, headers))
        return 200

    client = observability.LangfuseClient(
        enabled=False,
        public_key="",
        secret_key="",
        background=False,
        transport=transport,
    )
    observability.set_client(client)

    with observability.trace("query", input={"question": "q"}) as trace:
        assert trace.root is True
        observability.score("task_completion", 1.0)

    assert client.enabled is False
    assert client.flush() == 0
    assert events == []


def test_trace_generation_span_and_score_payload():
    events: list[dict] = []
    client = build_client(events)
    observability.set_client(client)

    with observability.trace(
        "assistant.chat",
        input={"question": "你好"},
        metadata={"intent": "chitchat"},
        tags=["assistant"],
    ) as trace:
        with observability.span("text2sql.answer", input={"question": "q"}) as span:
            with observability.generation(
                "llm.text2sql",
                model="mock",
                input=[{"role": "user", "content": "q"}],
                metadata={"task": "text2sql", "prompt_version": "v2"},
            ) as generation:
                generation.end(
                    output='{"sql": "SELECT 1"}',
                    usage={"input": 3, "output": 2, "total": 5},
                )
            span.end(output={"row_count": 1})
        observability.score("task_completion", 1.0, comment="完成")
        trace.update(output={"intent": "chitchat"}, metadata={"degraded": False})

    assert client.flush() == 5
    assert len(events) == 1
    request = events[0]
    assert request["url"] == "https://langfuse.example/api/public/ingestion"
    assert request["headers"]["Authorization"].startswith("Basic ")
    batch = request["payload"]["batch"]
    types = [event["type"] for event in batch]
    assert types.count("trace-create") == 2
    assert "span-create" in types
    assert "generation-create" in types
    assert "score-create" in types

    trace_end = [event for event in batch if event["type"] == "trace-create"][-1]
    assert trace_end["body"]["output"] == {"intent": "chitchat"}
    assert trace_end["body"]["metadata"]["intent"] == "chitchat"
    assert trace_end["body"]["metadata"]["degraded"] is False
    assert trace_end["body"]["environment"] == "test"
    assert trace_end["body"]["release"] == "test-release"

    span_event = next(event for event in batch if event["type"] == "span-create")
    generation_event = next(
        event for event in batch if event["type"] == "generation-create"
    )
    assert generation_event["body"]["parentObservationId"] == span_event["body"]["id"]
    assert generation_event["body"]["usage"] == {
        "input": 3,
        "output": 2,
        "total": 5,
        "unit": "TOKENS",
    }

    score_event = next(event for event in batch if event["type"] == "score-create")
    assert score_event["body"]["name"] == "task_completion"
    assert score_event["body"]["value"] == 1.0
    assert score_event["body"]["comment"] == "完成"


def test_nested_trace_becomes_child_span():
    events: list[dict] = []
    client = build_client(events)
    observability.set_client(client)

    with observability.trace("assistant.chat", input={"question": "q"}) as root:
        assert root.root is True
        with observability.trace("text2sql.answer", input={"question": "q"}) as child:
            assert child.root is False
            assert child.observation_id

    client.flush()
    spans = [
        event
        for event in events[0]["payload"]["batch"]
        if event["type"] == "span-create"
    ]
    assert spans[0]["body"]["name"] == "text2sql.answer"


def test_generation_without_trace_creates_implicit_trace():
    events: list[dict] = []
    client = build_client(events)
    observability.set_client(client)

    with observability.generation("llm.intent", model="mock") as generation:
        generation.end(output='{"intent": "data_query"}')

    client.flush()
    batch = events[0]["payload"]["batch"]
    trace_names = [
        event["body"].get("name")
        for event in batch
        if event["type"] == "trace-create"
    ]
    assert "llm.intent" in trace_names
    assert any(event["type"] == "generation-create" for event in batch)


def test_capture_content_can_be_disabled():
    events: list[dict] = []
    client = build_client(events, capture_content=False)
    observability.set_client(client)

    with observability.generation(
        "llm.text2sql",
        model="mock",
        input=[{"role": "user", "content": "敏感内容"}],
    ) as generation:
        generation.end(output="敏感输出")

    client.flush()
    event = next(
        item
        for item in events[0]["payload"]["batch"]
        if item["type"] == "generation-create"
    )
    assert event["body"]["input"] is None
    assert event["body"]["output"] is None


def test_missing_credentials_disables_client():
    client = observability.LangfuseClient(
        enabled=True,
        public_key="",
        secret_key="",
        background=False,
    )
    assert client.enabled is False
    assert "凭据" in client.status()["reason"]


def test_status_does_not_expose_secret_key():
    client = build_client([])
    status = client.status()
    assert status["enabled"] is True
    assert "pk-test" not in str(status)
    assert "sk-test" not in str(status)
    assert "sk-test" not in client.status_line()


def test_flush_failure_is_swallowed():
    def failing_transport(url, payload, headers):
        raise RuntimeError("network down")

    client = observability.LangfuseClient(
        enabled=True,
        public_key="pk-test",
        secret_key="sk-test",
        host="https://langfuse.example",
        background=False,
        transport=failing_transport,
    )
    observability.set_client(client)

    with observability.trace("query", input={"question": "q"}):
        observability.score("task_completion", 1.0)

    assert client.flush() == 0
