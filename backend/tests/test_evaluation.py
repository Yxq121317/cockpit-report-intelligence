"""LLM 效果评测：忠实度、任务完成率、工具调用准确率与失败案例。"""

from __future__ import annotations

import pytest

from app.core.llm import LLMGateway
from app.services import evaluation


@pytest.fixture(autouse=True)
def _reset_evaluation():
    evaluation.reset()
    yield
    evaluation.reset()


def query_success() -> dict:
    return {
        "question": "各年级学生人数",
        "title": "各年级学生人数",
        "sql": "SELECT g.name AS 年级, COUNT(*) AS 学生人数 FROM students",
        "columns": [
            {"key": "年级", "label": "年级", "type": "string"},
            {"key": "学生人数", "label": "学生人数", "type": "number"},
        ],
        "rows": [
            {"年级": "七年级", "学生人数": 60},
            {"年级": "八年级", "学生人数": 60},
            {"年级": "九年级", "学生人数": 60},
        ],
        "row_count": 3,
        "answer": "共统计 3 个年级。学生人数最多的是 七年级，共 60 人。",
        "need_clarification": False,
        "degraded": False,
        "model_mode": "mock",
    }


def query_clarification() -> dict:
    return {
        "question": "帮我写一首诗",
        "title": "需要更多信息",
        "sql": None,
        "columns": [],
        "rows": [],
        "row_count": 0,
        "answer": "我还没有理解你要查哪类数据。可以试试：上周八年级各班出勤率。",
        "need_clarification": True,
        "degraded": False,
        "model_mode": "mock",
    }


def query_unfaithful() -> dict:
    result = query_success()
    result["answer"] = "共有 120 名学生。"
    return result


def assistant_query() -> dict:
    query = query_success()
    return {
        "question": query["question"],
        "intent": "data_query",
        "intent_label": "数据查询",
        "intent_confidence": 1.0,
        "reply": query["answer"],
        "tool_calls": [],
        "query": query,
        "report": None,
        "degraded": False,
        "model_mode": "mock",
    }


def assistant_entry_success() -> dict:
    return {
        "question": "新增学生 测试同学，学号 TEST2026001",
        "intent": "data_entry",
        "intent_label": "数据录入",
        "intent_confidence": 1.0,
        "reply": "新增学生 测试同学（学号 TEST2026001，八年级一班）。",
        "tool_calls": [
            {
                "name": "create_student",
                "label": "新增学生",
                "arguments": {"student_no": "TEST2026001"},
                "status": "success",
                "summary": "新增学生 测试同学（学号 TEST2026001，八年级一班）",
                "data": {
                    "student_no": "TEST2026001",
                    "name": "测试同学",
                    "class_name": "八年级一班",
                },
            }
        ],
        "query": None,
        "report": None,
        "degraded": False,
        "model_mode": "mock",
    }


def assistant_entry_error() -> dict:
    return {
        "question": "新增学生",
        "intent": "data_entry",
        "intent_label": "数据录入",
        "intent_confidence": 1.0,
        "reply": "操作未完成：缺少必填参数：student_no",
        "tool_calls": [
            {
                "name": "create_student",
                "label": "新增学生",
                "arguments": {},
                "status": "error",
                "summary": "缺少必填参数：student_no",
                "data": {},
            }
        ],
        "query": None,
        "report": None,
        "degraded": False,
        "model_mode": "mock",
    }


def test_faithfulness_grounds_numbers_and_dates_in_rows():
    result = evaluation.faithfulness_score(
        "共统计 2 条记录，2026-11-03 的出勤率为 98.5%。",
        columns=[{"key": "日期"}, {"key": "出勤率"}],
        rows=[
            {"日期": "2026-11-03", "出勤率": 98.5},
            {"日期": "2026-11-04", "出勤率": 97.0},
        ],
        row_count=2,
    )
    assert result.score == 1.0
    assert result.ungrounded == ()


def test_faithfulness_flags_fabricated_number():
    result = evaluation.faithfulness_score(
        "共有 120 名学生。",
        columns=[{"key": "学生人数"}],
        rows=[{"学生人数": 30}],
        row_count=1,
    )
    assert result.score == 0.0
    assert "120" in result.ungrounded


def test_faithfulness_accepts_numbers_from_identifiers():
    result = evaluation.faithfulness_score(
        "新增学生 测试同学（学号 TEST2026001）。",
        columns=[],
        rows=[],
        row_count=1,
        extra_grounded=["TEST2026001"],
    )
    assert result.score == 1.0


def test_faithfulness_without_numbers_is_grounded():
    result = evaluation.faithfulness_score(
        "没有查询到符合条件的记录。",
        columns=[],
        rows=[],
        row_count=0,
    )
    assert result.score == 1.0


def test_evaluate_query_success():
    scores = evaluation.evaluate_query(query_success())
    assert scores["task_completion"] == 1.0
    assert scores["faithfulness"] == 1.0
    assert scores["passed"] is True
    assert scores["failure"] is None


def test_evaluate_query_clarification_is_failure():
    scores = evaluation.evaluate_query(query_clarification())
    assert scores["task_completion"] == 0.0
    assert scores["passed"] is False
    assert scores["failure"]["category"] == "clarification"


def test_evaluate_query_unfaithful_answer_is_failure():
    scores = evaluation.evaluate_query(query_unfaithful())
    assert scores["task_completion"] == 1.0
    assert scores["faithfulness"] < 1.0
    assert scores["failure"]["category"] == "unfaithful_answer"


def test_evaluate_assistant_data_query_uses_query_scores():
    scores = evaluation.evaluate_assistant(assistant_query())
    assert scores["task_completion"] == 1.0
    assert scores["tool_call_accuracy"] is None
    assert scores["faithfulness"] == 1.0
    assert scores["passed"] is True


def test_evaluate_assistant_entry_success():
    scores = evaluation.evaluate_assistant(assistant_entry_success())
    assert scores["task_completion"] == 1.0
    assert scores["tool_call_accuracy"] == 1.0
    assert scores["faithfulness"] == 1.0


def test_evaluate_assistant_entry_error_flags_tool_call():
    scores = evaluation.evaluate_assistant(assistant_entry_error())
    assert scores["task_completion"] == 0.0
    assert scores["tool_call_accuracy"] == 0.0
    assert scores["failure"]["category"] == "tool_call_failed"


def test_evaluate_assistant_missing_report_is_failure():
    result = {
        "question": "生成八年级出勤报表",
        "intent": "report_generation",
        "reply": "暂时无法生成报表。",
        "tool_calls": [],
        "query": None,
        "report": None,
        "degraded": True,
        "model_mode": "mock",
    }
    scores = evaluation.evaluate_assistant(result)
    assert scores["task_completion"] == 0.0
    assert scores["tool_call_accuracy"] == 0.0
    assert scores["failure"]["category"] == "tool_call_failed"


def test_evaluate_assistant_chitchat_is_complete():
    result = {
        "question": "你好",
        "intent": "chitchat",
        "reply": "我可以做三类事情：查数据、录数据、出报表。",
        "tool_calls": [],
        "query": None,
        "report": None,
        "degraded": False,
        "model_mode": "mock",
    }
    scores = evaluation.evaluate_assistant(result)
    assert scores["task_completion"] == 1.0
    assert scores["tool_call_accuracy"] is None
    assert scores["faithfulness"] == 1.0


def test_evaluate_assistant_unrecognized_chitchat_is_clarification_failure():
    result = {
        "question": "阿巴阿巴阿巴",
        "intent": "chitchat",
        "reply": "我可以做三类事情：查数据、录数据、出报表。",
        "tool_calls": [],
        "query": None,
        "report": None,
        "degraded": False,
        "model_mode": "mock",
    }
    scores = evaluation.evaluate_assistant(result)
    assert scores["task_completion"] == 0.0
    assert scores["faithfulness"] == 1.0
    assert scores["passed"] is False
    assert scores["failure"]["category"] == "clarification"


def test_summary_tracks_rates_and_tool_calls():
    evaluation.record_query(query_success())
    evaluation.record_assistant(assistant_entry_success())

    summary = evaluation.summary()
    assert summary["queries"]["total"] == 1
    assert summary["queries"]["task_completion_rate"] == 1.0
    assert summary["queries"]["faithfulness_avg"] == 1.0
    assert summary["assistants"]["total"] == 1
    assert summary["assistants"]["tool_calls"] == 1
    assert summary["assistants"]["tool_call_accuracy"] == 1.0
    assert summary["failures"]["total"] == 0


def test_recent_failures_records_clarification():
    evaluation.record_query(query_clarification())

    summary = evaluation.summary()
    assert summary["failures"]["total"] == 1
    assert summary["failures"]["by_category"] == {"clarification": 1}

    failures = evaluation.recent_failures(5)
    assert failures[0]["category"] == "clarification"
    assert failures[0]["scope"] == "query"
    assert failures[0]["question"] == "帮我写一首诗"


def test_eval_summary_api(client):
    evaluation.reset()
    response = client.post(
        "/api/query/nl", json={"question": "各年级学生人数"}
    )
    assert response.status_code == 200

    data = client.get("/api/eval/summary").json()["data"]
    assert data["metrics"]["queries"]["total"] >= 1
    assert data["langfuse"]["enabled"] is False
    assert {item["key"] for item in data["metric_definitions"]} == {
        "task_completion",
        "tool_call_accuracy",
        "faithfulness",
        "schema_recall",
    }


def test_eval_failures_api(client):
    evaluation.reset()
    response = client.post(
        "/api/query/nl", json={"question": "帮我写一首诗"}
    )
    assert response.status_code == 200

    data = client.get("/api/eval/failures?limit=5").json()["data"]
    assert data["count"] >= 1
    assert data["items"][0]["category"] == "clarification"


def test_eval_golden_api(client):
    data = client.get("/api/eval/golden").json()["data"]
    assert "available" in data


def test_eval_golden_run_api(client):
    response = client.post("/api/eval/golden/run")
    assert response.status_code == 200

    data = response.json()["data"]
    assert data["available"] is True
    assert data["path"].endswith("latest.json")
    assert data["summary"]["total"] >= 1


def test_golden_cases_pass_in_mock_mode(seeded_db):
    from app.eval.golden import GOLDEN_CASES, run_golden_cases

    gateway = LLMGateway(mode="mock", api_key="", base_url="")
    result = run_golden_cases(gateway=gateway, output_path=None)

    assert result["summary"]["total"] == len(GOLDEN_CASES)
    assert result["summary"]["pass_rate"] == 1.0
    assert result["summary"]["intent_accuracy"] == 1.0
    assert result["summary"]["tool_call_accuracy"] == 1.0
    assert result["summary"]["faithfulness_avg"] == 1.0
