"""黄金用例与评测运行器。

运行方式：``python scripts/evaluate_llm.py --mode mock``。默认只跑 mock
网关，结果写入 ``data/evals/latest.json``；``--push`` 才会把分数上报到
Langfuse。评测本身不新增数据库表，写操作统一回滚，报表文件随后删除。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core import observability
from app.core.config import settings
from app.core.db import SessionLocal
from app.core.llm import LLMGateway, get_gateway
from app.core.schema_retrieval import retrieve_tables, schema_recall
from app.services import assistant, evaluation, text2sql

PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_OUTPUT = object()


@dataclass(frozen=True)
class GoldenCase:
    id: str
    question: str
    kind: str  # query | entry | report | chitchat
    expected_intent: str
    expected_tables: tuple[str, ...] = ()
    expected_tool: str | None = None
    expected_arguments: dict[str, Any] = field(default_factory=dict)
    expected_row_count: int | None = None


GOLDEN_CASES: tuple[GoldenCase, ...] = (
    GoldenCase(
        id="query_attendance_rate",
        question="上周八年级各班出勤率",
        kind="query",
        expected_intent="data_query",
        expected_tables=("attendance", "students", "classes", "grades"),
        expected_row_count=2,
    ),
    GoldenCase(
        id="query_late_ranking",
        question="最近两周迟到次数最多的班级",
        kind="query",
        expected_intent="data_query",
        expected_tables=("attendance", "students", "classes", "grades"),
    ),
    GoldenCase(
        id="query_pending_leave",
        question="本周待审批请假有多少",
        kind="query",
        expected_intent="data_query",
        expected_tables=("leave_requests", "students", "classes", "grades"),
        expected_row_count=1,
    ),
    GoldenCase(
        id="query_timetable",
        question="八年级二班本周的课程表",
        kind="query",
        expected_intent="data_query",
        expected_tables=("timetable", "classes", "teachers", "grades"),
        expected_row_count=30,
    ),
    GoldenCase(
        id="query_parent_contact",
        question="李雷的家长联系方式",
        kind="query",
        expected_intent="data_query",
        expected_tables=("parents", "students", "classes", "grades"),
        expected_row_count=2,
    ),
    GoldenCase(
        id="query_student_count",
        question="各年级学生人数",
        kind="query",
        expected_intent="data_query",
        expected_tables=("students", "classes", "grades"),
        expected_row_count=3,
    ),
    GoldenCase(
        id="query_head_teacher",
        question="八年级一班班主任是谁",
        kind="query",
        expected_intent="data_query",
        expected_tables=("classes", "teachers", "grades"),
        expected_row_count=1,
    ),
    GoldenCase(
        id="query_teacher_workload",
        question="本学期老师课时排名",
        kind="query",
        expected_intent="data_query",
        expected_tables=("timetable", "teachers", "classes", "grades"),
    ),
    GoldenCase(
        id="chitchat_help",
        question="你好，你是谁",
        kind="chitchat",
        expected_intent="chitchat",
    ),
    GoldenCase(
        id="entry_attendance",
        question="给李雷登记今天的正常考勤",
        kind="entry",
        expected_intent="data_entry",
        expected_tool="record_attendance",
        expected_arguments={"student_name": "李雷", "status": "正常"},
    ),
    GoldenCase(
        id="entry_student",
        question="新增学生 测试同学，学号 TEST2026001，性别男，班级八年级一班",
        kind="entry",
        expected_intent="data_entry",
        expected_tool="create_student",
        expected_arguments={
            "student_no": "TEST2026001",
            "name": "测试同学",
            "class_name": "八年级一班",
        },
    ),
    GoldenCase(
        id="entry_leave",
        question="帮李雷提交明天到后天的病假申请",
        kind="entry",
        expected_intent="data_entry",
        expected_tool="create_leave_request",
        expected_arguments={"student_name": "李雷", "leave_type": "病假"},
    ),
    GoldenCase(
        id="report_attendance",
        question="生成八年级出勤报表",
        kind="report",
        expected_intent="report_generation",
        expected_tool="generate_report",
        expected_arguments={
            "report_type": "attendance_summary",
            "target": "八年级",
        },
    ),
    GoldenCase(
        id="report_leave",
        question="生成本周请假报表",
        kind="report",
        expected_intent="report_generation",
        expected_tool="generate_report",
        expected_arguments={"report_type": "leave_summary"},
    ),
)


def _parse_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}
    return {}


def _tool_matches(result: dict[str, Any], case: GoldenCase) -> bool | None:
    if not case.expected_tool:
        return None
    for call in result.get("tool_calls") or []:
        if call.get("name") != case.expected_tool:
            continue
        if call.get("status") != "success":
            return False
        arguments = _parse_arguments(call.get("arguments"))
        if all(
            str(arguments.get(key)) == str(value)
            for key, value in case.expected_arguments.items()
        ):
            return True
    return False


def _task_completed(
    case: GoldenCase, result: dict[str, Any], scores: dict[str, Any]
) -> bool:
    if case.kind == "query":
        return (
            bool(result.get("sql"))
            and not result.get("need_clarification")
            and (
                case.expected_row_count is None
                or result.get("row_count") == case.expected_row_count
            )
        )
    if case.kind in {"entry", "report"}:
        return (
            result.get("intent") == case.expected_intent
            and scores["task_completion"] >= 1.0
            and _tool_matches(result, case) is not False
        )
    if case.kind == "chitchat":
        return (
            result.get("intent") == "chitchat"
            and bool(str(result.get("reply") or "").strip())
        )
    return False


def _cleanup_report(result: dict[str, Any]) -> None:
    report = result.get("report")
    if not isinstance(report, dict):
        return
    raw_path = report.get("file_path")
    if not raw_path:
        return
    path = Path(raw_path)
    path.unlink(missing_ok=True)
    path.with_suffix(".json").unlink(missing_ok=True)


def _run_case(
    case: GoldenCase, gateway: LLMGateway, mode: str
) -> dict[str, Any]:
    intent = assistant.classify_intent(case.question, gateway=gateway)
    intent_ok = intent.intent == case.expected_intent
    with SessionLocal() as session:
        if case.kind == "query":
            result = text2sql.answer_question(
                session, case.question, gateway=gateway, record=False
            )
            scores = evaluation.evaluate_query(result)
        else:
            result = assistant.chat(
                session,
                case.question,
                gateway=gateway,
                commit=False,
                record=False,
            )
            session.rollback()
            scores = evaluation.evaluate_assistant(result)
            _cleanup_report(result)

    tables = retrieve_tables(case.question)
    recall = (
        schema_recall(case.expected_tables, tables)
        if case.expected_tables
        else None
    )
    tool_ok = _tool_matches(result, case)
    task_completion = scores["task_completion"]
    if case.kind == "query" and not _task_completed(case, result, scores):
        task_completion = 0.0
    task_ok = _task_completed(case, result, scores)
    passed = (
        intent_ok
        and task_ok
        and scores["faithfulness"] >= 0.99
        and (tool_ok is not False)
        and (recall is None or recall >= 0.99)
    )
    failure = None
    if not passed:
        reasons = []
        if not intent_ok:
            reasons.append(f"意图 {intent.intent} != {case.expected_intent}")
        if not task_ok:
            reasons.append("任务未完成")
        if scores["faithfulness"] < 0.99:
            reasons.append(
                "忠实度不足：" + "、".join(scores["ungrounded"])
            )
        if tool_ok is False:
            reasons.append("工具调用或参数不匹配")
        if recall is not None and recall < 0.99:
            reasons.append(f"Schema 召回率 {recall}")
        failure = "；".join(reasons)

    return {
        "id": case.id,
        "question": case.question,
        "kind": case.kind,
        "mode": mode,
        "expected_intent": case.expected_intent,
        "actual_intent": intent.intent,
        "intent_ok": intent_ok,
        "task_completion": task_completion,
        "tool_call_accuracy": (
            None if tool_ok is None else (1.0 if tool_ok else 0.0)
        ),
        "faithfulness": scores["faithfulness"],
        "ungrounded": scores["ungrounded"],
        "schema_recall": recall,
        "retrieved_tables": tables,
        "passed": passed,
        "failure": failure,
        "row_count": result.get("row_count"),
    }


def _push_case(case: dict[str, Any]) -> None:
    """把单个黄金用例的分数写入当前 trace。"""

    observability.score(
        "intent_accuracy",
        1.0 if case["intent_ok"] else 0.0,
        data_type="NUMERIC",
        comment="意图与黄金用例一致" if case["intent_ok"] else "意图不一致",
    )
    observability.score(
        "task_completion",
        case["task_completion"],
        comment="通过" if case["passed"] else case["failure"] or "未通过",
    )
    if case["tool_call_accuracy"] is not None:
        observability.score(
            "tool_call_accuracy",
            case["tool_call_accuracy"],
            comment="工具与参数匹配"
            if case["tool_call_accuracy"] >= 1.0
            else "工具或参数不匹配",
        )
    observability.score(
        "faithfulness",
        case["faithfulness"],
        comment="有未溯源数字" if case["ungrounded"] else "通过",
    )
    if case["schema_recall"] is not None:
        observability.score(
            "schema_recall",
            case["schema_recall"],
            comment="表结构检索覆盖期望表"
            if case["schema_recall"] >= 1.0
            else "表结构检索未覆盖期望表",
        )
    observability.score(
        "case_passed",
        1.0 if case["passed"] else 0.0,
        data_type="NUMERIC",
        comment="通过" if case["passed"] else case["failure"],
    )


def _push_summary(summary: dict[str, Any], mode: str) -> None:
    with observability.trace(
        "eval.golden.summary",
        input={"total": summary["total"], "mode": mode},
        tags=["eval", "golden", "summary"],
    ) as trace:
        observability.score("pass_rate", summary["pass_rate"])
        observability.score("intent_accuracy", summary["intent_accuracy"])
        observability.score(
            "task_completion", summary["task_completion_rate"]
        )
        if summary["tool_call_accuracy"] is not None:
            observability.score(
                "tool_call_accuracy", summary["tool_call_accuracy"]
            )
        observability.score("faithfulness", summary["faithfulness_avg"])
        if summary["schema_recall_avg"] is not None:
            observability.score(
                "schema_recall", summary["schema_recall_avg"]
            )
        trace.update(output=summary)


def _summarize(cases: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(cases)
    passed = sum(1 for case in cases if case["passed"])
    intent_ok = sum(1 for case in cases if case["intent_ok"])
    tool_cases = [
        case for case in cases if case["tool_call_accuracy"] is not None
    ]
    recall_cases = [
        case for case in cases if case["schema_recall"] is not None
    ]
    return {
        "total": total,
        "passed": passed,
        "pass_rate": round(passed / total, 4) if total else 0.0,
        "intent_accuracy": round(intent_ok / total, 4) if total else 0.0,
        "task_completion_rate": round(
            sum(case["task_completion"] for case in cases) / total, 4
        )
        if total
        else 0.0,
        "tool_call_accuracy": (
            round(
                sum(case["tool_call_accuracy"] for case in tool_cases)
                / len(tool_cases),
                4,
            )
            if tool_cases
            else None
        ),
        "faithfulness_avg": (
            round(
                sum(case["faithfulness"] for case in cases) / total, 4
            )
            if total
            else 0.0
        ),
        "schema_recall_avg": (
            round(
                sum(case["schema_recall"] for case in recall_cases)
                / len(recall_cases),
                4,
            )
            if recall_cases
            else None
        ),
    }


def run_golden_cases(
    *,
    gateway: LLMGateway | None = None,
    output_path: str | Path | None | object = _DEFAULT_OUTPUT,
    push: bool = False,
) -> dict[str, Any]:
    gateway = gateway or get_gateway()
    mode = "real" if gateway.effective_mode == "real" else "mock"
    cases: list[dict[str, Any]] = []

    for case in GOLDEN_CASES:
        with observability.trace(
            "eval.golden",
            input={"case_id": case.id, "question": case.question},
            metadata={"kind": case.kind, "mode": mode},
            tags=["eval", "golden"],
        ) as case_trace:
            case_result = _run_case(case, gateway, mode)
            cases.append(case_result)
            if push:
                _push_case(case_result)
            case_trace.update(
                output={
                    "passed": case_result["passed"],
                    "failure": case_result["failure"],
                    "row_count": case_result.get("row_count"),
                },
                metadata={"intent_ok": case_result["intent_ok"]},
            )

    summary = _summarize(cases)
    payload = {
        "generated_at": evaluation.utc_now(),
        "mode": mode,
        "summary": summary,
        "cases": cases,
        "failures": [case for case in cases if not case["passed"]],
    }
    if push:
        _push_summary(summary, mode)
        observability.flush()

    if output_path is _DEFAULT_OUTPUT:
        output_path = settings.EVAL_GOLDEN_OUTPUT
    if output_path is not None:
        path = Path(output_path)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        payload["output_path"] = str(path)
    return payload
