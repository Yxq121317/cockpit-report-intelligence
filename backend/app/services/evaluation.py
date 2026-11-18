"""LLM 应用效果评测：任务完成率、工具调用准确率与 Faithfulness。

指标全部由线上请求或黄金用例实时计算；评测结果只存在进程内存与可选的
本地 JSONL/JSON 文件，不新增数据库表。Langfuse 开启时，同一批分数通过
observability.score() 写入 trace，便于在 Langfuse 控制台查看趋势。
"""

from __future__ import annotations

import json
import logging
import re
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from app.core import observability
from app.core.config import settings

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
_MAX_FAILURES = 200
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_NUMBER_BOUNDARY_TEMPLATE = r"(?<!\d){token}(?!\d)"
# 这些问法属于正常的问候/帮助类闲聊，回复能力说明即视为完成；
# 其他被归为 chitchat 的输入通常是没有识别出的数据问题，记为澄清失败。
_CHITCHAT_HELP_MARKERS = (
    "你好",
    "您好",
    "你是谁",
    "你能做什么",
    "能做什么",
    "帮助",
    "谢谢",
    "再见",
    "在吗",
    "hello",
    "hi",
)

METRIC_DEFINITIONS = [
    {
        "key": "task_completion",
        "label": "任务完成率",
        "description": "问答成功执行并返回结果的比例；澄清、校验失败或工具报错计为未完成。",
    },
    {
        "key": "tool_call_accuracy",
        "label": "工具调用准确率",
        "description": "Function Call 工具执行成功的比例；缺少工具规划时按一次失败调用计算。",
    },
    {
        "key": "faithfulness",
        "label": "忠实度",
        "description": "回答中的数字和日期能追溯到本次 SQL 结果或工具返回数据的比例。",
    },
    {
        "key": "schema_recall",
        "label": "表结构召回率",
        "description": "Schema 检索结果覆盖黄金用例期望表的比例，用于评估检索策略。",
    },
]


@dataclass(frozen=True)
class FaithfulnessResult:
    score: float
    checked: int
    ungrounded: tuple[str, ...]


_lock = threading.Lock()
_log_lock = threading.Lock()
_failures: deque[dict[str, Any]] = deque(maxlen=_MAX_FAILURES)
_stats: dict[str, Any] = {}


def reset() -> None:
    """清空进程内评测状态（测试与演示重置用，不影响 Langfuse 服务端数据）。"""

    with _lock:
        _failures.clear()
        _stats.clear()
        _stats.update(_empty_stats())


def _empty_stats() -> dict[str, Any]:
    return {
        "queries": {
            "total": 0,
            "completed": 0,
            "faithfulness_sum": 0.0,
            "faithfulness_count": 0,
        },
        "assistants": {
            "total": 0,
            "completed": 0,
            "tool_calls": 0,
            "tool_call_success": 0,
            "faithfulness_sum": 0.0,
            "faithfulness_count": 0,
        },
        "failures": {"total": 0, "by_category": {}},
        "since": None,
        "updated_at": None,
    }


reset()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def _average(total: float, count: int) -> float | None:
    if count <= 0:
        return None
    return round(total / count, 4)


def _flatten_values(value: Any) -> Iterable[Any]:
    if value is None:
        return
    if isinstance(value, dict):
        for item in value.values():
            yield from _flatten_values(item)
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _flatten_values(item)
        return
    yield value


def _grounding_corpus(
    rows: list[dict] | None,
    row_count: int | None,
    extra_grounded: Iterable[Any],
) -> tuple[set[float], list[str]]:
    values: list[Any] = []
    for row in rows or []:
        values.extend(row.values())
    values.extend(_flatten_values(list(extra_grounded)))
    if row_count is not None:
        values.append(row_count)

    numbers: set[float] = set()
    texts: list[str] = []
    for value in values:
        if value is None or isinstance(value, bool):
            continue
        if isinstance(value, (int, float, Decimal)):
            numbers.add(float(value))
            texts.append(str(value))
            continue
        text = str(value)
        texts.append(text)
        for token in _NUMBER_RE.findall(text):
            try:
                numbers.add(float(token))
            except ValueError:  # pragma: no cover - 正则已保证可转换
                continue
    return numbers, texts


def _matches_number(token: str, numbers: set[float]) -> bool:
    try:
        value = float(token)
    except ValueError:
        return False
    if any(abs(value - candidate) < 1e-9 for candidate in numbers):
        return True
    # 模板回答通常保留 1 位小数，与 SQL 结果按同一精度比较。
    return any(
        abs(round(value, 1) - round(candidate, 1)) < 1e-9
        for candidate in numbers
    )


def _matches_text_number(token: str, texts: list[str]) -> bool:
    pattern = re.compile(_NUMBER_BOUNDARY_TEMPLATE.format(token=re.escape(token)))
    return any(pattern.search(text) for text in texts)


def faithfulness_score(
    answer: str,
    columns: list[dict] | None = None,
    rows: list[dict] | None = None,
    *,
    row_count: int | None = None,
    extra_grounded: Iterable[Any] = (),
) -> FaithfulnessResult:
    """检查回答中的数字/日期是否能从本次结果数据中找到依据。"""

    del columns  # 当前按整行取值校验；列信息保留给后续扩展
    text = (answer or "").strip()
    if not text:
        return FaithfulnessResult(score=0.0, checked=0, ungrounded=())

    numbers, texts = _grounding_corpus(rows, row_count, extra_grounded)
    checked = 0
    ungrounded: list[str] = []

    def check(token: str) -> None:
        nonlocal checked
        checked += 1
        if token in ungrounded:
            return
        if _matches_number(token, numbers) or _matches_text_number(token, texts):
            return
        ungrounded.append(token)

    for date_token in _DATE_RE.findall(text):
        check(date_token)
    text_without_dates = _DATE_RE.sub(" ", text)
    for number_token in _NUMBER_RE.findall(text_without_dates):
        check(number_token)

    if checked == 0:
        return FaithfulnessResult(score=1.0, checked=0, ungrounded=())
    score = (checked - len(ungrounded)) / checked
    return FaithfulnessResult(
        score=round(max(0.0, min(1.0, score)), 4),
        checked=checked,
        ungrounded=tuple(ungrounded),
    )


def _is_help_request(question: str) -> bool:
    text = (question or "").strip().lower()
    return any(marker in text for marker in _CHITCHAT_HELP_MARKERS)


def evaluate_query(result: dict[str, Any]) -> dict[str, Any]:
    """计算单次问数的 task_completion 与 faithfulness。"""

    answer = str(result.get("answer") or "")
    rows = result.get("rows") or []
    row_count = result.get("row_count")
    if row_count is None:
        row_count = len(rows)
    task_completion = (
        1.0
        if result.get("sql") and not result.get("need_clarification")
        else 0.0
    )
    faithfulness = faithfulness_score(
        answer,
        columns=result.get("columns") or [],
        rows=rows,
        row_count=int(row_count),
    )

    failure = None
    if task_completion < 1.0:
        failure = {
            "category": (
                "clarification"
                if result.get("need_clarification")
                else "query_failed"
            ),
            "detail": answer[:500],
        }
    elif faithfulness.score < 0.99:
        failure = {
            "category": "unfaithful_answer",
            "detail": "无法在 SQL 结果中溯源：" + "、".join(faithfulness.ungrounded),
        }

    return {
        "task_completion": task_completion,
        "faithfulness": faithfulness.score,
        "ungrounded": list(faithfulness.ungrounded),
        "passed": failure is None,
        "failure": failure,
    }


def _assistant_tool_scores(result: dict[str, Any]) -> tuple[float | None, int, int]:
    intent = result.get("intent")
    calls = result.get("tool_calls") or []
    if intent not in {"data_entry", "report_generation"}:
        return None, 0, 0
    evaluated = max(len(calls), 1)
    success = sum(1 for call in calls if call.get("status") == "success")
    if not calls:
        success = 0
    return round(success / evaluated, 4), evaluated, success


def evaluate_assistant(result: dict[str, Any]) -> dict[str, Any]:
    """计算单次助手指令的完成率、工具调用准确率与忠实度。"""

    intent = result.get("intent")
    reply = str(result.get("reply") or "")
    query = result.get("query")
    report = result.get("report")
    calls = result.get("tool_calls") or []
    tool_accuracy, evaluated_calls, successful_calls = _assistant_tool_scores(
        result
    )

    if intent == "data_query":
        query_scores = evaluate_query(query or {})
        task_completion = query_scores["task_completion"]
        faithfulness = query_scores["faithfulness"]
        ungrounded = query_scores["ungrounded"]
    elif intent == "report_generation":
        task_completion = (
            1.0
            if report
            or any(
                call.get("name") == "generate_report"
                and call.get("status") == "success"
                for call in calls
            )
            else 0.0
        )
        faithful = faithfulness_score(
            reply,
            rows=[],
            row_count=1,
            extra_grounded=[
                call.get("data") for call in calls
            ]
            + [report],
        )
        faithfulness = faithful.score
        ungrounded = list(faithful.ungrounded)
    elif intent == "data_entry":
        task_completion = (
            1.0
            if calls and all(call.get("status") == "success" for call in calls)
            else 0.0
        )
        faithful = faithfulness_score(
            reply,
            rows=[],
            row_count=1,
            extra_grounded=[
                call.get("data") for call in calls
            ],
        )
        faithfulness = faithful.score
        ungrounded = list(faithful.ungrounded)
    elif intent == "chitchat":
        helpful = _is_help_request(str(result.get("question") or ""))
        task_completion = 1.0 if reply.strip() and helpful else 0.0
        faithful = faithfulness_score(reply, rows=[], row_count=0)
        faithfulness = faithful.score
        ungrounded = list(faithful.ungrounded)
    else:
        task_completion = 0.0
        faithful = faithfulness_score(reply, rows=[], row_count=0)
        faithfulness = faithful.score
        ungrounded = list(faithful.ungrounded)

    failure = None
    if tool_accuracy is not None and tool_accuracy < 1.0:
        failure = {
            "category": "tool_call_failed",
            "detail": f"工具调用成功 {successful_calls}/{evaluated_calls}",
        }
    elif intent == "chitchat" and task_completion < 1.0:
        failure = {
            "category": "clarification",
            "detail": "未识别为校园数据问题：" + (reply[:480] or "无回复"),
        }
    elif task_completion < 1.0:
        failure = {
            "category": "assistant_failed",
            "detail": reply[:500],
        }
    elif faithfulness < 0.99:
        failure = {
            "category": "unfaithful_answer",
            "detail": "无法在工具结果中溯源：" + "、".join(ungrounded),
        }

    return {
        "task_completion": task_completion,
        "tool_call_accuracy": tool_accuracy,
        "faithfulness": faithfulness,
        "ungrounded": ungrounded,
        "passed": failure is None,
        "failure": failure,
    }


def _emit_scores(scope: str, scores: dict[str, Any]) -> None:
    comment = (
        scores["failure"]["category"]
        if scores.get("failure")
        else "通过"
    )
    if scope == "query":
        observability.score(
            "query_task_completion",
            scores["task_completion"],
            comment=comment,
        )
        observability.score(
            "query_faithfulness",
            scores["faithfulness"],
            comment=comment,
        )
        return
    observability.score(
        "task_completion", scores["task_completion"], comment=comment
    )
    if scores.get("tool_call_accuracy") is not None:
        observability.score(
            "tool_call_accuracy",
            scores["tool_call_accuracy"],
            comment=comment,
        )
    observability.score(
        "faithfulness", scores["faithfulness"], comment=comment
    )


def _append_failure_log(record: dict[str, Any]) -> None:
    if not settings.EVAL_FAILURE_LOG:
        return
    path = Path(settings.EVAL_FAILURE_LOG_PATH)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, default=str)
        with _log_lock:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
    except OSError as exc:
        logger.warning("失败案例写入本地日志失败：%s", type(exc).__name__)


def _record(
    scope: str, result: dict[str, Any], scores: dict[str, Any]
) -> dict[str, Any]:
    now = utc_now()
    with _lock:
        if _stats["since"] is None:
            _stats["since"] = now
        _stats["updated_at"] = now
        bucket = _stats["queries"] if scope == "query" else _stats["assistants"]
        bucket["total"] += 1
        if scores["task_completion"] >= 1.0:
            bucket["completed"] += 1
        bucket["faithfulness_sum"] += scores["faithfulness"]
        bucket["faithfulness_count"] += 1
        if scope == "assistant":
            if scores.get("tool_call_accuracy") is not None:
                evaluated = max(len(result.get("tool_calls") or []), 1)
                success = sum(
                    1
                    for call in result.get("tool_calls") or []
                    if call.get("status") == "success"
                )
                _stats["assistants"]["tool_calls"] += evaluated
                _stats["assistants"]["tool_call_success"] += success
        if scores.get("failure"):
            _stats["failures"]["total"] += 1
            category = scores["failure"]["category"]
            by_category = _stats["failures"]["by_category"]
            by_category[category] = by_category.get(category, 0) + 1

    _emit_scores(scope, scores)
    observability.add_metadata(
        eval_scope=scope,
        eval_scores={
            key: scores.get(key)
            for key in ("task_completion", "tool_call_accuracy", "faithfulness")
        },
        failure_category=(
            scores["failure"]["category"] if scores.get("failure") else None
        ),
    )

    if scores.get("failure"):
        record = {
            "id": uuid4().hex,
            "at": now,
            "scope": scope,
            "category": scores["failure"]["category"],
            "detail": scores["failure"]["detail"],
            "question": result.get("question", ""),
            "intent": result.get("intent"),
            "sql": result.get("sql"),
            "answer": str(result.get("answer") or result.get("reply") or "")[:1000],
            "scores": {
                key: scores.get(key)
                for key in ("task_completion", "tool_call_accuracy", "faithfulness")
            },
        }
        with _lock:
            _failures.append(record)
        _append_failure_log(record)
    return scores


def record_query(result: dict[str, Any]) -> dict[str, Any]:
    return _record("query", result, evaluate_query(result))


def record_assistant(result: dict[str, Any]) -> dict[str, Any]:
    return _record("assistant", result, evaluate_assistant(result))


def summary() -> dict[str, Any]:
    with _lock:
        queries = dict(_stats["queries"])
        assistants = dict(_stats["assistants"])
        failures = {
            "total": _stats["failures"]["total"],
            "by_category": dict(_stats["failures"]["by_category"]),
        }
        since = _stats["since"]
        updated_at = _stats["updated_at"]

    return {
        "queries": {
            "total": queries["total"],
            "completed": queries["completed"],
            "task_completion_rate": _rate(
                queries["completed"], queries["total"]
            ),
            "faithfulness_avg": _average(
                queries["faithfulness_sum"], queries["faithfulness_count"]
            ),
        },
        "assistants": {
            "total": assistants["total"],
            "completed": assistants["completed"],
            "task_completion_rate": _rate(
                assistants["completed"], assistants["total"]
            ),
            "tool_calls": assistants["tool_calls"],
            "tool_call_success": assistants["tool_call_success"],
            "tool_call_accuracy": (
                _rate(
                    assistants["tool_call_success"],
                    assistants["tool_calls"],
                )
                if assistants["tool_calls"]
                else None
            ),
            "faithfulness_avg": _average(
                assistants["faithfulness_sum"],
                assistants["faithfulness_count"],
            ),
        },
        "failures": failures,
        "since": since,
        "updated_at": updated_at,
    }


def recent_failures(limit: int = 50) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), _MAX_FAILURES))
    with _lock:
        items = list(_failures)
    return list(reversed(items[-limit:]))


def meta() -> dict[str, Any]:
    return {
        "metric_definitions": METRIC_DEFINITIONS,
        "langfuse": observability.status(),
        "failure_log_enabled": bool(settings.EVAL_FAILURE_LOG),
    }
