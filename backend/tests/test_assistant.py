"""语音转写、意图识别、Function Call 工具与报表生成。"""

from __future__ import annotations

import io
import wave
from pathlib import Path

import httpx
import pytest
from openpyxl import load_workbook
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql

from app.core.llm import LLMGateway, LLMResult
from app.models import Student
from app.services.assistant import chat, classify_intent
from app.services.asr import ASRService
from app.services.report import generate_report
from app.services.school_tools import (
    TOOL_DEFINITIONS,
    ToolError,
    execute_tool,
)


def make_wav(seconds: float = 0.2) -> bytes:
    frames = int(16000 * seconds)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\x00\x00" * frames)
    return buffer.getvalue()


def mock_gateway() -> LLMGateway:
    return LLMGateway(mode="mock", api_key="", base_url="")


def test_intent_classification_is_deterministic_in_mock_mode():
    gateway = mock_gateway()
    assert classify_intent("上周八年级各班出勤率", gateway=gateway).intent == "data_query"
    assert (
        classify_intent(
            "给李雷新增一位家长，姓名李强，电话13800000000",
            gateway=gateway,
        ).intent
        == "data_entry"
    )
    assert (
        classify_intent("生成八年级出勤报表", gateway=gateway).intent
        == "report_generation"
    )
    assert (
        classify_intent("生成八年级一班学生花名册", gateway=gateway).intent
        == "report_generation"
    )
    assert (
        classify_intent("生成八年级一班课程表", gateway=gateway).intent
        == "report_generation"
    )
    assert classify_intent("你好，你是谁", gateway=gateway).intent == "chitchat"


def test_help_wording_does_not_turn_query_into_write():
    gateway = mock_gateway()
    assert (
        classify_intent("帮我查本周请假名单", gateway=gateway).intent
        == "data_query"
    )


def test_assistant_api_does_not_expose_server_file_path(client):
    response = client.post(
        "/api/assistant/chat",
        json={"question": "生成七年级出勤报表"},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["report"]
    assert "file_path" not in data["report"]
    for call in data["tool_calls"]:
        assert "file_path" not in call.get("data", {})

    from app.services.report import REPORTS_DIR

    report_id = data["report"]["report_id"]
    (REPORTS_DIR / f"{report_id}.xlsx").unlink(missing_ok=True)
    (REPORTS_DIR / f"{report_id}.json").unlink(missing_ok=True)


def test_agent_data_query_returns_query_result(session):
    result = chat(
        session,
        "上周八年级各班出勤率",
        gateway=mock_gateway(),
    )
    assert result["intent"] == "data_query"
    assert result["query"] is not None
    assert result["query"]["row_count"] == 2
    assert result["tool_calls"] == []
    assert "出勤率" in result["reply"]


def test_agent_data_entry_uses_tool_and_can_rollback(session):
    result = chat(
        session,
        "新增学生 测试同学，学号 TEST2026001，性别男，班级八年级一班",
        gateway=mock_gateway(),
        commit=False,
    )
    assert result["intent"] == "data_entry"
    assert result["tool_calls"]
    assert result["tool_calls"][0]["name"] == "create_student"
    assert result["tool_calls"][0]["status"] == "success"
    student = session.scalar(
        select(Student).where(Student.student_no == "TEST2026001")
    )
    assert student is not None
    assert student.name == "测试同学"
    session.rollback()
    assert (
        session.scalar(select(Student).where(Student.student_no == "TEST2026001"))
        is None
    )


def test_create_student_tool_validates_required_arguments(session):
    with pytest.raises(ToolError, match="student_no"):
        execute_tool(session, "create_student", {"name": "缺少学号"}, commit=False)


def test_create_leave_request_tool_computes_days(session):
    result = execute_tool(
        session,
        "create_leave_request",
        {
            "student_name": "李雷",
            "leave_type": "病假",
            "start_date": "2026-11-04",
            "end_date": "2026-11-06",
            "reason": "感冒发烧",
        },
        commit=False,
    )
    assert result.data["days"] == 3
    assert result.data["status"] == "待审批"
    session.rollback()


def test_report_rate_compiles_numeric_cast_for_postgresql():
    from app.services.report import _round_ratio

    statement = select(
        _round_ratio(func.count() * 100.0 / func.count())
    )
    sql = str(statement.compile(dialect=postgresql.dialect())).upper()
    assert "CAST" in sql
    assert "NUMERIC" in sql


def test_generate_report_creates_chinese_xlsx(session, tmp_path):
    result = generate_report(
        session,
        report_type="attendance_summary",
        target="八年级",
        output_dir=tmp_path,
    )
    path = Path(result["file_path"])
    assert path.exists()
    assert path.suffix == ".xlsx"
    assert path.read_bytes().startswith(b"PK")
    assert result["row_count"] == 2
    assert "八年级出勤报表" in result["title"]


def test_campus_overview_report_has_eight_sheets(session, tmp_path):
    result = generate_report(
        session,
        report_type="campus_overview",
        start_date="2026-09-01",
        end_date="2026-11-03",
        output_dir=tmp_path,
    )
    assert result["report_type"] == "campus_overview"
    assert result["title"].startswith("全校校园数据总览报表")
    counts = {item["name"]: item["row_count"] for item in result["sheets"]}
    assert list(counts) == [
        "年级概况",
        "教师名录",
        "班级概况",
        "学生花名册",
        "家长联系表",
        "出勤汇总",
        "请假记录",
        "课程表",
    ]
    assert counts == {
        "年级概况": 3,
        "教师名录": 24,
        "班级概况": 6,
        "学生花名册": 180,
        "家长联系表": 360,
        "出勤汇总": 6,
        "请假记录": 60,
        "课程表": 180,
    }
    assert result["row_count"] == sum(counts.values())

    workbook = load_workbook(Path(result["file_path"]))
    assert workbook.sheetnames == list(counts)
    roster = workbook["学生花名册"]
    assert [cell.value for cell in roster[1]] == [
        "学号",
        "姓名",
        "性别",
        "出生日期",
        "年级",
        "班级",
        "入学日期",
        "学籍状态",
        "联系电话",
        "家庭住址",
    ]
    assert roster.freeze_panes == "A2"
    assert roster["A1"].fill.fgColor.rgb == "001F4E79"
    workbook.close()


def test_campus_overview_report_respects_target_scope(session, tmp_path):
    result = generate_report(
        session,
        report_type="campus_overview",
        target="八年级",
        output_dir=tmp_path,
    )
    counts = {item["name"]: item["row_count"] for item in result["sheets"]}
    assert "八年级校园数据总览报表" in result["title"]
    assert counts["年级概况"] == 1
    assert counts["班级概况"] == 2
    assert counts["学生花名册"] == 60
    assert counts["家长联系表"] == 120
    assert counts["出勤汇总"] == 2
    assert counts["课程表"] == 60
    # 教师名录保持全校口径，方便跨年级查询任课教师。
    assert counts["教师名录"] == 24


def test_agent_campus_overview_report_wiring(session):
    result = chat(
        session,
        "生成校园数据总览模板报表",
        gateway=mock_gateway(),
    )
    assert result["intent"] == "report_generation"
    assert result["report"] is not None
    assert result["report"]["download_url"].startswith("/api/reports/")
    assert len(result["report"]["sheets"]) == 8
    assert result["tool_calls"][0]["name"] == "generate_report"
    assert result["tool_calls"][0]["arguments"]["report_type"] == "campus_overview"
    report_path = Path(result["report"]["file_path"])
    assert report_path.exists()
    report_path.unlink(missing_ok=True)
    report_path.with_suffix(".json").unlink(missing_ok=True)


def test_agent_report_generation_returns_download_link(session):
    result = chat(
        session,
        "生成八年级出勤报表",
        gateway=mock_gateway(),
    )
    assert result["intent"] == "report_generation"
    assert result["report"] is not None
    assert result["report"]["download_url"].startswith("/api/reports/")
    assert result["tool_calls"][0]["name"] == "generate_report"
    # 下载接口由 API 测试覆盖；这里确认报表文件确实已经生成。
    report_path = Path(result["report"]["file_path"])
    assert report_path.exists()
    report_path.unlink(missing_ok=True)
    report_path.with_suffix(".json").unlink(missing_ok=True)


def test_forced_report_uses_question_scope(session):
    """按问题生成报表：即使问题本身是查询口吻，也强制生成对应报表。"""
    result = chat(
        session,
        "八年级各班出勤率",
        gateway=mock_gateway(),
        force_report=True,
    )
    assert result["intent"] == "report_generation"
    assert result["intent_reason"] == "用户选择按问题直接生成报表"
    assert result["report"] is not None
    assert result["report"]["title"].startswith("八年级出勤报表")
    call = result["tool_calls"][0]
    assert call["name"] == "generate_report"
    assert call["arguments"]["report_type"] == "attendance_summary"
    assert call["arguments"]["target"] == "八年级"
    report_path = Path(result["report"]["file_path"])
    report_path.unlink(missing_ok=True)
    report_path.with_suffix(".json").unlink(missing_ok=True)


def test_assistant_api_force_report_uses_question(client):
    response = client.post(
        "/api/assistant/chat",
        json={"question": "本周请假情况", "force_report": True},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["intent"] == "report_generation"
    assert data["report"] is not None
    call = data["tool_calls"][0]
    assert call["name"] == "generate_report"
    assert call["arguments"]["report_type"] == "leave_summary"

    from app.services.report import REPORTS_DIR

    report_id = data["report"]["report_id"]
    (REPORTS_DIR / f"{report_id}.xlsx").unlink(missing_ok=True)
    (REPORTS_DIR / f"{report_id}.json").unlink(missing_ok=True)


def test_assistant_meta_lists_tools_and_asr(client):
    data = client.get("/api/assistant/meta").json()["data"]
    assert {tool["name"] for tool in data["tools"]} == {
        "create_student",
        "create_parent",
        "record_attendance",
        "create_leave_request",
        "create_timetable_entry",
        "generate_report",
    }
    assert data["asr"]["mode"] in {"mock", "real"}
    assert data["intents"]


def test_asr_mock_service_returns_transcript():
    service = ASRService(mode="mock", api_key="")
    result = service.transcribe(make_wav(), language="zh")
    assert result.text == "上周八年级各班出勤率"
    assert result.mode == "mock"


def test_asr_route_accepts_wav_upload(client):
    response = client.post(
        "/api/asr/transcribe",
        files={"audio": ("voice.wav", make_wav(), "audio/wav")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    assert body["data"]["text"]
    assert body["data"]["mode"] == "mock"


def test_report_download_route(client, session):
    result = chat(
        session,
        "生成八年级出勤报表",
        gateway=mock_gateway(),
    )
    url = result["report"]["download_url"]
    response = client.get(url)
    assert response.status_code == 200
    assert response.content.startswith(b"PK")
    assert "UTF-8" in response.headers["content-disposition"]
    path = Path(result["report"]["file_path"])
    path.unlink(missing_ok=True)
    path.with_suffix(".json").unlink(missing_ok=True)


class FakeToolGateway:
    """真实模式替身：先返回意图 JSON，再返回一个 Function Call。"""

    effective_mode = "real"
    degraded = False

    def __init__(self, tool_name: str, arguments: dict | str) -> None:
        self.tool_name = tool_name
        self.arguments = arguments
        self.tool_requests = 0

    def complete(self, messages, **kwargs) -> LLMResult:
        if kwargs.get("tools"):
            self.tool_requests += 1
            arguments = (
                self.arguments
                if isinstance(self.arguments, str)
                else __import__("json").dumps(
                    self.arguments, ensure_ascii=False
                )
            )
            return LLMResult(
                content="",
                model="fake",
                mode="real",
                degraded=False,
                input_tokens=1,
                output_tokens=1,
                latency_ms=1,
                tool_calls=[
                    {
                        "id": "call_1",
                        "name": self.tool_name,
                        "arguments": arguments,
                    }
                ],
            )
        return LLMResult(
            content=(
                '{"intent": "data_entry", "confidence": 0.98, '
                '"reason": "用户要求新增学生"}'
            ),
            model="fake",
            mode="real",
            degraded=False,
            input_tokens=1,
            output_tokens=1,
            latency_ms=1,
        )


def test_real_mode_function_call_creates_student(session):
    gateway = FakeToolGateway(
        "create_student",
        {
            "student_no": "TESTFC001",
            "name": "函数同学",
            "gender": "男",
            "class_name": "八年级一班",
        },
    )
    result = chat(
        session,
        "帮我新增一名学生函数同学",
        gateway=gateway,
        commit=False,
    )
    assert gateway.tool_requests == 1
    assert result["intent"] == "data_entry"
    assert result["tool_calls"][0]["name"] == "create_student"
    assert session.scalar(
        select(Student).where(Student.student_no == "TESTFC001")
    ) is not None
    session.rollback()


def test_llm_gateway_parses_tool_calls(monkeypatch):
    captured: dict = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_9",
                                    "type": "function",
                                    "function": {
                                        "name": "create_student",
                                        "arguments": '{"name": "小明"}',
                                    },
                                }
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2},
            }

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs["json"]
        return FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    gateway = LLMGateway(
        mode="real",
        api_key="test-key",
        base_url="http://llm.local",
        model_name="fake-model",
    )
    result = gateway.complete(
        [{"role": "user", "content": "新增学生"}],
        tools=TOOL_DEFINITIONS,
        tool_choice="auto",
    )
    assert captured["json"]["tools"]
    assert result.mode == "real"
    assert result.tool_calls == [
        {
            "id": "call_9",
            "name": "create_student",
            "arguments": '{"name": "小明"}',
        }
    ]
