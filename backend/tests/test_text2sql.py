"""Text2SQL 自然语言问答、模板、真实模型重试与导出。"""

from app.core.llm import LLMResult
from app.services.text2sql import answer_question


def ask(client, question: str) -> dict:
    response = client.post("/api/query/nl", json={"question": question})
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    return body["data"]


def test_meta_lists_exactly_eight_tables(client):
    data = client.get("/api/query/meta").json()["data"]
    assert data["table_count"] == 8
    assert {table["name"] for table in data["tables"]} == {
        "grades",
        "teachers",
        "classes",
        "students",
        "parents",
        "attendance",
        "leave_requests",
        "timetable",
    }
    assert len(data["examples"]) >= 5


def test_attendance_rate_last_week(client):
    data = ask(client, "上周八年级各班出勤率")
    assert data["need_clarification"] is False
    assert len(data["rows"]) == 2
    assert all(0 <= row["出勤率"] <= 100 for row in data["rows"])
    assert "出勤率" in data["answer"]
    assert data["chart"]["type"] == "bar"
    assert "attendance" in data["sql"]


def test_late_ranking_last_two_weeks(client):
    data = ask(client, "最近两周迟到次数最多的班级")
    assert data["rows"]
    assert data["rows"][0]["迟到次数"] >= data["rows"][-1]["迟到次数"]
    assert "迟到次数最多" in data["answer"]
    assert data["chart"]["type"] == "bar"


def test_pending_leave_count_this_week(client):
    data = ask(client, "本周待审批请假有多少")
    assert data["row_count"] == 1
    assert data["rows"][0]["申请数"] == 5
    assert "待审批" in data["answer"]


def test_leave_list_this_week(client):
    data = ask(client, "本周请假名单")
    assert data["row_count"] == 5
    assert {"学生", "班级", "请假类型", "状态"} <= {
        column["key"] for column in data["columns"]
    }
    assert "请假记录" in data["answer"]


def test_class_timetable(client):
    data = ask(client, "八年级二班本周的课程表")
    assert data["row_count"] == 30
    assert all(row["班级"] == "八年级二班" for row in data["rows"])
    assert {"星期", "节次", "科目", "教师"} <= {
        column["key"] for column in data["columns"]
    }
    assert "节课" in data["answer"]
    assert data["chart"]["type"] == "table"


def test_parent_contact(client):
    data = ask(client, "李雷的家长联系方式")
    assert data["row_count"] == 2
    assert "李雷" in data["answer"]
    assert "父亲" in data["answer"]
    assert all(row["联系电话"] for row in data["rows"])


def test_student_count_by_grade(client):
    data = ask(client, "各年级学生人数")
    assert len(data["rows"]) == 3
    assert sum(row["学生人数"] for row in data["rows"]) == 180
    assert data["chart"]["type"] == "bar"


def test_student_count_in_class(client):
    data = ask(client, "八年级一班有多少学生")
    assert data["rows"][0]["学生总数"] == 30
    assert "30" in data["answer"]


def test_head_teacher(client):
    data = ask(client, "八年级一班班主任是谁")
    assert data["row_count"] == 1
    assert "班主任是" in data["answer"]
    assert data["rows"][0]["联系电话"]


def test_teacher_workload(client):
    data = ask(client, "本学期老师课时排名")
    assert data["rows"]
    assert data["rows"][0]["课时数"] >= data["rows"][-1]["课时数"]
    assert "课时最多" in data["answer"]


def test_student_attendance_summary(client):
    data = ask(client, "张小明最近出勤情况")
    assert data["row_count"] == 1
    assert "张小明" in data["answer"]
    assert "出勤率" in data["answer"]


def test_student_late_breakdown(client):
    data = ask(client, "张小明迟到几次")
    assert data["rows"]
    assert "张小明" in data["answer"]
    assert "考勤情况" in data["answer"]


def test_school_totals(client):
    assert ask(client, "全校有多少学生")["rows"][0]["学生总数"] == 180
    assert ask(client, "全校有多少老师")["rows"][0]["教师总数"] == 24
    assert ask(client, "学校有多少个班级")["rows"][0]["班级总数"] == 6


def test_gender_count(client):
    male = ask(client, "有多少男生")["rows"][0]["男生人数"]
    female = ask(client, "有多少女生")["rows"][0]["女生人数"]
    assert male + female == 180


def test_unknown_question_returns_clarification(client):
    data = ask(client, "帮我写一首诗")
    assert data["need_clarification"] is True
    assert data["rows"] == []
    assert "可以试试" in data["answer"]


def test_blank_question_is_rejected(client):
    response = client.post("/api/query/nl", json={"question": "   "})
    assert response.status_code == 422
    assert response.json()["code"] == 1001


def test_export_xlsx_and_csv(client):
    data = ask(client, "各年级学生人数")
    payload = {
        "columns": data["columns"],
        "rows": data["rows"],
        "filename": "各年级学生人数",
    }
    xlsx = client.post(
        "/api/query/export", json={**payload, "format": "xlsx"}
    )
    assert xlsx.status_code == 200
    assert xlsx.content.startswith(b"PK")
    assert "UTF-8" in xlsx.headers["content-disposition"]

    csv = client.post("/api/query/export", json={**payload, "format": "csv"})
    assert csv.status_code == 200
    assert csv.content.startswith(b"\xef\xbb\xbf")
    assert "年级" in csv.content.decode("utf-8-sig")


class FakeGateway:
    effective_mode = "real"
    degraded = False

    def __init__(self, answers: list[str]) -> None:
        self.answers = answers
        self.calls = 0

    def complete(self, messages, **kwargs) -> LLMResult:
        content = self.answers[min(self.calls, len(self.answers) - 1)]
        self.calls += 1
        return LLMResult(
            content=content,
            model="fake",
            mode="real",
            degraded=False,
            input_tokens=1,
            output_tokens=1,
            latency_ms=1,
        )


def test_real_mode_retries_after_unsafe_sql(session):
    gateway = FakeGateway(
        [
            '{"sql": "DELETE FROM students", "explanation": "bad"}',
            '{"sql": "SELECT COUNT(*) AS 学生总数 FROM students", '
            '"explanation": "count", "chart": "table", "title": "学生总数"}',
        ]
    )
    data = answer_question(session, "请统计学生总数", gateway=gateway)
    assert gateway.calls == 2
    assert data["model_mode"] == "real"
    assert data["rows"][0]["学生总数"] == 180


def test_real_mode_builds_answer_from_result_rows(session):
    gateway = FakeGateway(
        [
            '{"sql": "SELECT c.name AS 班级, c.classroom AS 教室 '
            "FROM classes AS c JOIN grades AS g ON c.grade_id = g.id "
            "WHERE g.name = '九年级' ORDER BY c.name\", "
            '"explanation": "查询九年级各班教室", "chart": "table", '
            '"title": "九年级各班教室"}',
        ]
    )
    data = answer_question(session, "列出九年级各班的教室", gateway=gateway)
    assert data["model_mode"] == "real"
    assert data["row_count"] == 2
    assert "九年级一班" in data["answer"]
    assert "教室" in data["answer"]


def test_real_mode_falls_back_to_template(session):
    gateway = FakeGateway(
        [
            '{"sql": "DELETE FROM students"}',
            '{"sql": "DROP TABLE students"}',
        ]
    )
    data = answer_question(session, "全校有多少学生", gateway=gateway)
    assert data["model_mode"] == "mock"
    assert data["degraded"] is True
    assert data["rows"][0]["学生总数"] == 180
