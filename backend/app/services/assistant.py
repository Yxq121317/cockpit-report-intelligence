"""智能助手：语音问题 -> 意图分类 -> Function Call -> 内部工具。

- mock 模式：关键词规则确定性分类与工具规划，完全离线。
- real 模式：先让 LLM 输出意图 JSON，再通过 OpenAI 兼容 tools 触发工具。
- 真实模型失败时自动回退到规则规划，并标记 degraded。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import observability
from app.core.llm import LLMGateway, get_gateway
from app.core.prompts import render_prompt
from app.models import Class, Student, Teacher
from app.services import evaluation, text2sql
from app.services.asr import get_asr_service
from app.services.school_tools import (
    REPORT_TYPES,
    TOOL_DEFINITIONS,
    ToolError,
    execute_tool,
    tool_catalog,
)

INTENT_PROMPT_VERSION = "v2"
TOOL_PLAN_PROMPT_VERSION = "v2"

INTENT_LABELS = {
    "data_query": "数据查询",
    "data_entry": "数据录入",
    "report_generation": "报表生成",
    "chitchat": "闲聊",
}

INTENT_DESCRIPTIONS = {
    "data_query": "查询、统计、排名、名单、联系方式等只读问题",
    "data_entry": "新增学生/家长、录入考勤、提交请假、录入课程表",
    "report_generation": "生成或导出出勤、请假、花名册、课程表、年级概况报表",
    "chitchat": "问候、帮助说明或与校园数据无关的问题",
}

AGENT_EXAMPLES = [
    {"question": "上周八年级各班出勤率", "category": "语音查询"},
    {"question": "生成校园数据总览模板报表", "category": "报表"},
    {"question": "生成八年级出勤报表", "category": "报表"},
    {"question": "给李雷登记今天的正常考勤", "category": "录入"},
    {"question": "新增学生 测试同学，学号 TEST2026001，性别男，班级八年级一班", "category": "录入"},
    {"question": "本周待审批请假有多少", "category": "语音查询"},
]

AGENT_HELP = (
    "我可以做三类事情：① 查数据，例如“上周八年级各班出勤率”；"
    "② 录数据，例如“给李雷登记今天的正常考勤”；"
    "③ 出报表，例如“生成八年级出勤报表”。"
)

_QUERY_MARKERS = (
    "查",
    "统计",
    "多少",
    "几",
    "名单",
    "排名",
    "出勤率",
    "出勤",
    "考勤",
    "课程表",
    "课表",
    "家长",
    "联系方式",
    "是谁",
    "情况",
    "分布",
    "人数",
    "课时",
    "最近",
    "上周",
    "本周",
    "本月",
    "今天",
    "昨天",
    "全校",
    "年级",
    "班级",
    "学生",
    "老师",
    "教师",
)

_ENTRY_VERBS = (
    "新增",
    "添加",
    "录入",
    "登记",
    "创建",
    "提交",
    "申请",
    "排课",
    "加课",
    "补录",
    "修改",
    "更新",
)
_ENTRY_OBJECTS = (
    "学生",
    "家长",
    "监护人",
    "考勤",
    "出勤",
    "请假",
    "病假",
    "事假",
    "课程",
    "课表",
    "排课",
    "教师",
)
_LEAVE_MARKERS = ("请假", "病假", "事假")

_REPORT_MARKERS = ("报表", "报告")
_REPORT_VERBS = ("生成", "导出", "做一份", "做一张", "出一份", "出一张")
_REPORT_OBJECTS = (
    "花名册",
    "名单",
    "统计表",
    "汇总表",
    "概况",
    "课程表",
    "课表",
)
_CHITCHAT_MARKERS = (
    "你好",
    "您好",
    "嗨",
    "谢谢",
    "再见",
    "你是谁",
    "你能做什么",
    "帮助",
    "写诗",
    "天气",
    "笑话",
)
_DATA_TERMS = (
    "学生",
    "家长",
    "教师",
    "老师",
    "班级",
    "年级",
    "考勤",
    "出勤",
    "请假",
    "成绩",
    "课程",
    "课表",
    "报表",
    "报告",
    "全校",
)

_CLASS_RE = re.compile(r"([一二三四五六七八九])年级([一二三四五六七八九])班")
_GRADE_RE = re.compile(r"([一二三四五六七八九])年级")
_MOBILE_RE = re.compile(r"1[3-9]\d{9}")
_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_STUDENT_NO_RE = re.compile(r"学号\s*(?:为|是|[:：])?\s*([A-Za-z0-9-]{4,})")
_PERIOD_RE = re.compile(r"第\s*([1-6一二三四五六])\s*节")
_CHINESE_NUMBERS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
}
_WEEKDAYS = {
    "周一": 1,
    "星期一": 1,
    "周二": 2,
    "星期二": 2,
    "周三": 3,
    "星期三": 3,
    "周四": 4,
    "星期四": 4,
    "周五": 5,
    "星期五": 5,
}
_SUBJECTS = (
    "语文",
    "数学",
    "英语",
    "物理",
    "化学",
    "生物",
    "历史",
    "地理",
    "道法",
    "体育",
    "音乐",
    "美术",
    "信息",
)


@dataclass(frozen=True)
class IntentResult:
    intent: str
    label: str
    confidence: float
    reason: str
    mode: str
    degraded: bool


def _extract_json(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("模型未返回 JSON")
    payload = json.loads(text[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("模型 JSON 不是对象")
    return payload


def classify_intent(
    question: str,
    *,
    gateway: LLMGateway | None = None,
) -> IntentResult:
    question = (question or "").strip()
    gateway = gateway or get_gateway()
    fallback = _classify_by_keywords(question)

    if gateway.effective_mode != "real" or not question:
        return IntentResult(
            **fallback,
            mode="mock",
            degraded=gateway.degraded,
        )

    system = render_prompt(
        "intent",
        INTENT_PROMPT_VERSION,
        (
            "你是校园数据助手的意图分类器。把用户问题分类为："
            "data_query（查询数据）、data_entry（新增或修改业务数据）、"
            "report_generation（生成报表或报告）、chitchat（问候或无关问题）。"
            "只输出 JSON："
            '{"intent": "...", "confidence": 0.0, "reason": "中文原因"}。'
        ),
        intents="\n".join(
            f"- {key}：{value}" for key, value in INTENT_DESCRIPTIONS.items()
        ),
    )
    result = gateway.complete(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": question},
        ],
        task="intent",
        prompt_version=f"intent-{INTENT_PROMPT_VERSION}",
        temperature=0,
        max_tokens=200,
    )
    if result.mode != "real":
        return IntentResult(
            **fallback,
            mode="mock",
            degraded=True,
        )
    try:
        payload = _extract_json(result.content)
        intent = str(payload.get("intent") or "").strip()
        if intent not in INTENT_LABELS:
            raise ValueError("未知意图")
        confidence = float(payload.get("confidence", 0.8))
        reason = str(payload.get("reason") or "由模型分类")
    except (ValueError, TypeError, json.JSONDecodeError):
        return IntentResult(
            **fallback,
            mode="mock",
            degraded=True,
        )
    return IntentResult(
        intent=intent,
        label=INTENT_LABELS[intent],
        confidence=max(0.0, min(1.0, confidence)),
        reason=reason,
        mode="real",
        degraded=False,
    )


def _is_report_request(text: str) -> bool:
    if any(marker in text for marker in _REPORT_MARKERS):
        return True
    return any(verb in text for verb in _REPORT_VERBS) and any(
        noun in text for noun in _REPORT_OBJECTS
    )


def _classify_by_keywords(question: str) -> dict:
    text = re.sub(r"\s+", "", question)
    if _is_report_request(text):
        return {
            "intent": "report_generation",
            "label": INTENT_LABELS["report_generation"],
            "confidence": 1.0,
            "reason": "问题包含报表或报告",
        }
    if any(marker in text for marker in _LEAVE_MARKERS) and (
        any(
            marker in text
            for marker in ("提交", "申请", "请个假", "录入", "登记", "补录")
        )
        or (
            re.search(r"帮.{0,4}(?:请假|病假|事假)", text)
            and not any(
                marker in text
                for marker in ("查", "查询", "统计", "多少", "名单")
            )
        )
    ):
        return {
            "intent": "data_entry",
            "label": INTENT_LABELS["data_entry"],
            "confidence": 1.0,
            "reason": "问题包含请假写入表达",
        }
    has_verb = any(marker in text for marker in _ENTRY_VERBS)
    has_object = any(marker in text for marker in _ENTRY_OBJECTS)
    if has_verb and has_object and not (
        "查询" in text or "统计" in text or "多少" in text or "名单" in text
    ):
        return {
            "intent": "data_entry",
            "label": INTENT_LABELS["data_entry"],
            "confidence": 1.0,
            "reason": "问题包含录入动词和业务对象",
        }
    if any(marker in text for marker in _CHITCHAT_MARKERS) and not any(
        marker in text for marker in _DATA_TERMS
    ):
        return {
            "intent": "chitchat",
            "label": INTENT_LABELS["chitchat"],
            "confidence": 1.0,
            "reason": "问候或与校园数据无关",
        }
    if any(marker in text for marker in _QUERY_MARKERS):
        return {
            "intent": "data_query",
            "label": INTENT_LABELS["data_query"],
            "confidence": 1.0,
            "reason": "问题包含查询或统计表达",
        }
    return {
        "intent": "chitchat",
        "label": INTENT_LABELS["chitchat"],
        "confidence": 0.6,
        "reason": "未匹配到明确的校园数据意图",
    }


def _scope(text: str) -> tuple[str | None, str | None]:
    match = _CLASS_RE.search(text)
    if match:
        grade = f"{match.group(1)}年级"
        return grade, f"{grade}{match.group(2)}班"
    match = _GRADE_RE.search(text)
    if match:
        return f"{match.group(1)}年级", None
    return None, None


def _student_names(session: Session) -> list[str]:
    names = session.scalars(select(Student.name).distinct()).all()
    return sorted((name for name in names if name), key=len, reverse=True)


def _teacher_names(session: Session) -> list[str]:
    names = session.scalars(select(Teacher.name).distinct()).all()
    return sorted((name for name in names if name), key=len, reverse=True)


def _find_name(text: str, names: list[str]) -> str | None:
    for name in names:
        if name in text:
            return name
    return None


def _relative_date(text: str, as_of: date) -> date | None:
    dates = _ISO_DATE_RE.findall(text)
    if dates:
        try:
            return date.fromisoformat(dates[0])
        except ValueError:
            return None
    if "今天" in text:
        return as_of
    if "明天" in text:
        return as_of + timedelta(days=1)
    if "后天" in text:
        return as_of + timedelta(days=2)
    if "昨天" in text:
        return as_of - timedelta(days=1)
    return None


def _report_date_range(text: str, as_of: date) -> tuple[str | None, str | None]:
    monday = as_of - timedelta(days=as_of.weekday())
    if "上周" in text:
        return (
            (monday - timedelta(days=7)).isoformat(),
            (monday - timedelta(days=1)).isoformat(),
        )
    if "本周" in text:
        return monday.isoformat(), as_of.isoformat()
    if "本月" in text:
        return as_of.replace(day=1).isoformat(), as_of.isoformat()
    dates = _ISO_DATE_RE.findall(text)
    if len(dates) >= 2:
        return dates[0], dates[1]
    if dates:
        return dates[0], dates[0]
    return None, None


def _plan_create_student(text: str) -> dict[str, Any]:
    class_name = _scope(text)[1]
    match = re.search(
        r"(?:新增|添加|录入|创建)(?:一名|一个|位)?学生\s*[，,：:]?\s*"
        r"([一-龥]{2,4})",
        text,
    )
    if match is None:
        match = re.search(r"(?:姓名|学生姓名)\s*[：:]?\s*([一-龥]{2,4})", text)
    number_match = _STUDENT_NO_RE.search(text)
    if number_match is None:
        number_match = re.search(r"\b([A-Za-z]{2,}\d{4,})\b", text)
    gender = (
        "男"
        if "男生" in text or re.search(r"性别\s*[：:]?\s*男", text)
        else "女"
        if "女生" in text or re.search(r"性别\s*[：:]?\s*女", text)
        else "男"
    )
    arguments: dict[str, Any] = {
        "name": match.group(1) if match else "",
        "student_no": number_match.group(1) if number_match else "",
        "gender": gender,
        "class_name": class_name or "",
    }
    phone = _MOBILE_RE.search(text)
    if phone:
        arguments["phone"] = phone.group(0)
    return {"name": "create_student", "arguments": arguments}


def _plan_create_parent(text: str, session: Session, as_of: date) -> dict[str, Any]:
    del as_of
    student_name = _find_name(text, _student_names(session)) or ""
    relation = "其他"
    for marker, mapped in (
        ("爸爸", "父亲"),
        ("父亲", "父亲"),
        ("妈妈", "母亲"),
        ("母亲", "母亲"),
        ("爷爷", "祖父"),
        ("奶奶", "祖母"),
        ("外公", "外祖父"),
        ("外婆", "外祖母"),
    ):
        if marker in text:
            relation = mapped
            break
    parent_match = re.search(
        r"(?:家长|监护人|联系人)(?:姓名)?\s*[：:]?\s*([一-龥]{2,4})",
        text,
    )
    if parent_match is None:
        parent_match = re.search(r"姓名\s*[：:]?\s*([一-龥]{2,4})", text)
    phone = _MOBILE_RE.search(text)
    return {
        "name": "create_parent",
        "arguments": {
            "student_name": student_name,
            "name": parent_match.group(1) if parent_match else "",
            "relation": relation,
            "phone": phone.group(0) if phone else "",
            "is_primary": True,
        },
    }


def _plan_attendance(text: str, session: Session, as_of: date) -> dict[str, Any]:
    student_name = _find_name(text, _student_names(session)) or ""
    status = "正常"
    for candidate in ("迟到", "早退", "缺勤", "请假", "正常"):
        if candidate in text:
            status = candidate
            break
    attendance_date = _relative_date(text, as_of) or as_of
    return {
        "name": "record_attendance",
        "arguments": {
            "student_name": student_name,
            "date": attendance_date.isoformat(),
            "status": status,
        },
    }


def _plan_leave(text: str, session: Session, as_of: date) -> dict[str, Any]:
    student_name = _find_name(text, _student_names(session)) or ""
    leave_type = "其他"
    for candidate in ("病假", "事假"):
        if candidate in text:
            leave_type = candidate
            break
    dates = _ISO_DATE_RE.findall(text)
    if len(dates) >= 2:
        start_date, end_date = dates[0], dates[1]
    elif dates:
        start_date = end_date = dates[0]
    elif "明天" in text and "后天" in text:
        start_date = (as_of + timedelta(days=1)).isoformat()
        end_date = (as_of + timedelta(days=2)).isoformat()
    else:
        days_match = re.search(r"请假\s*([1-9]\d*)\s*天", text)
        days = int(days_match.group(1)) if days_match else 1
        start_date = as_of.isoformat()
        end_date = (as_of + timedelta(days=days - 1)).isoformat()
    return {
        "name": "create_leave_request",
        "arguments": {
            "student_name": student_name,
            "leave_type": leave_type,
            "start_date": start_date,
            "end_date": end_date,
            "reason": "",
        },
    }


def _plan_timetable(text: str, session: Session, as_of: date) -> dict[str, Any]:
    del as_of
    class_name = _scope(text)[1] or ""
    weekday = next(
        (value for label, value in _WEEKDAYS.items() if label in text), 1
    )
    period_match = _PERIOD_RE.search(text)
    period = 1
    if period_match:
        raw = period_match.group(1)
        period = _CHINESE_NUMBERS.get(raw, int(raw) if raw.isdigit() else 1)
    subject = next((item for item in _SUBJECTS if item in text), "自习")
    teacher_name = _find_name(text, _teacher_names(session)) or ""
    return {
        "name": "create_timetable_entry",
        "arguments": {
            "class_name": class_name,
            "weekday": weekday,
            "period": period,
            "subject": subject,
            "teacher_name": teacher_name,
        },
    }


def _plan_report(text: str, session: Session, as_of: date) -> dict[str, Any]:
    if any(
        marker in text
        for marker in (
            "数据总览",
            "总览报表",
            "模板报表",
            "校园数据报表",
            "全校数据",
        )
    ):
        report_type = "campus_overview"
    elif "请假" in text:
        report_type = "leave_summary"
    elif "花名册" in text or ("学生" in text and "名单" in text):
        report_type = "student_roster"
    elif "课程表" in text or "课表" in text:
        report_type = "timetable"
    elif "年级" in text and ("概况" in text or "总览" in text):
        report_type = "grade_overview"
    else:
        report_type = "attendance_summary"
    _grade, class_name = _scope(text)
    target = class_name or _scope(text)[0]
    start_date, end_date = _report_date_range(text, as_of)
    return {
        "name": "generate_report",
        "arguments": {
            "report_type": report_type,
            "target": target or "",
            "start_date": start_date or "",
            "end_date": end_date or "",
        },
    }


def plan_mock_tools(
    session: Session, question: str, intent: str
) -> list[dict[str, Any]]:
    text = re.sub(r"\s+", "", question)
    as_of = text2sql.resolve_as_of(session)
    if intent == "report_generation":
        return [_plan_report(text, session, as_of)]
    if intent != "data_entry":
        return []
    if any(marker in text for marker in ("家长", "监护人", "联系人")):
        return [_plan_create_parent(text, session, as_of)]
    if any(marker in text for marker in _LEAVE_MARKERS) and any(
        marker in text for marker in ("提交", "申请", "帮", "请个假", "请假")
    ):
        return [_plan_leave(text, session, as_of)]
    if any(marker in text for marker in ("课程", "课表", "排课")):
        return [_plan_timetable(text, session, as_of)]
    if any(marker in text for marker in ("考勤", "出勤", "迟到", "缺勤")):
        return [_plan_attendance(text, session, as_of)]
    return [_plan_create_student(text)]


def _tool_plan_messages(question: str, session: Session) -> list[dict[str, str]]:
    as_of = text2sql.resolve_as_of(session)
    system = render_prompt(
        "tool_plan",
        TOOL_PLAN_PROMPT_VERSION,
        (
            "你是校园数据助手的工具规划器。根据用户请求选择工具并填写参数，"
            "只能使用提供的工具；班级名使用完整名称（例如八年级一班），"
            "学生和教师必须使用数据库中的姓名。日期使用 YYYY-MM-DD。"
            "不确定的字段留空，让工具返回校验提示。"
            "当前业务日期：{today}。"
        ),
        today=as_of.isoformat(),
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": question},
    ]


def plan_real_tools(
    session: Session,
    question: str,
    gateway: LLMGateway,
) -> tuple[list[dict[str, Any]], bool]:
    result = gateway.complete(
        _tool_plan_messages(question, session),
        task="tool_plan",
        prompt_version=f"tool-plan-{TOOL_PLAN_PROMPT_VERSION}",
        temperature=0,
        max_tokens=800,
        tools=TOOL_DEFINITIONS,
        tool_choice="auto",
    )
    if result.mode != "real":
        return [], True
    calls = []
    for item in result.tool_calls or []:
        name = item.get("name")
        if not isinstance(name, str):
            continue
        calls.append(
            {
                "name": name,
                "arguments": item.get("arguments") or "{}",
            }
        )
    return calls[:5], result.degraded


def _is_report_call(call: dict[str, Any]) -> bool:
    """判断工具调用是否为参数合法的报表生成。"""
    if call.get("name") != "generate_report":
        return False
    arguments = call.get("arguments")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            return False
    return (
        isinstance(arguments, dict)
        and str(arguments.get("report_type") or "") in REPORT_TYPES
    )


def _plan_forced_report(
    session: Session,
    question: str,
    gateway: LLMGateway,
) -> tuple[list[dict[str, Any]], bool]:
    """按问题强制生成报表：real 模式优先用模型规划，失败回退确定性规则。"""
    fallback = plan_mock_tools(session, question, "report_generation")
    if gateway.effective_mode != "real":
        return fallback, False
    plan, degraded = plan_real_tools(session, question, gateway)
    report_calls = [call for call in plan if _is_report_call(call)]
    if report_calls:
        return report_calls[:1], degraded
    return fallback, True


def _intent_fields(intent: str) -> dict[str, Any]:
    return {
        "intent": intent,
        "intent_label": INTENT_LABELS[intent],
        "intent_description": INTENT_DESCRIPTIONS[intent],
    }


def _base_agent_result(
    question: str,
    started: float,
    intent: IntentResult,
    *,
    gateway: LLMGateway,
    reply: str,
    tool_calls: list[dict] | None = None,
    query: dict | None = None,
    report: dict | None = None,
    degraded: bool | None = None,
    model_mode: str | None = None,
) -> dict[str, Any]:
    return {
        "question": question,
        **_intent_fields(intent.intent),
        "intent_confidence": intent.confidence,
        "intent_reason": intent.reason,
        "reply": reply,
        "tool_calls": tool_calls or [],
        "query": query,
        "report": report,
        "degraded": gateway.degraded if degraded is None else degraded,
        "model_mode": model_mode
        or ("real" if gateway.effective_mode == "real" else "mock"),
        "elapsed_ms": max(0, int((time.perf_counter() - started) * 1000)),
    }


def _chat(
    session: Session,
    question: str,
    *,
    gateway: LLMGateway | None = None,
    commit: bool = True,
    force_report: bool = False,
) -> dict[str, Any]:
    started = time.perf_counter()
    question = (question or "").strip()
    gateway = gateway or get_gateway()
    if force_report and question:
        intent = IntentResult(
            intent="report_generation",
            label=INTENT_LABELS["report_generation"],
            confidence=1.0,
            reason="用户选择按问题直接生成报表",
            mode="real" if gateway.effective_mode == "real" else "mock",
            degraded=gateway.degraded,
        )
    else:
        intent = classify_intent(question, gateway=gateway)

    if not question:
        return _base_agent_result(
            question,
            started,
            intent,
            gateway=gateway,
            reply=AGENT_HELP,
        )

    if intent.intent == "chitchat":
        return _base_agent_result(
            question,
            started,
            intent,
            gateway=gateway,
            reply=AGENT_HELP,
            degraded=gateway.degraded,
        )

    if intent.intent == "data_query":
        query = text2sql.answer_question(
            session, question, gateway=gateway, record=False
        )
        return _base_agent_result(
            question,
            started,
            intent,
            gateway=gateway,
            reply=query["answer"],
            query=query,
            degraded=gateway.degraded or query.get("degraded", False),
            model_mode=query.get("model_mode", "mock"),
        )

    plan: list[dict[str, Any]]
    plan_fallback = False
    if force_report:
        plan, plan_fallback = _plan_forced_report(session, question, gateway)
    elif gateway.effective_mode == "real":
        plan, plan_fallback = plan_real_tools(session, question, gateway)
    else:
        plan = plan_mock_tools(session, question, intent.intent)

    if not plan:
        plan = plan_mock_tools(session, question, intent.intent)
        plan_fallback = gateway.effective_mode == "real"

    if not plan:
        return _base_agent_result(
            question,
            started,
            intent,
            gateway=gateway,
            reply=AGENT_HELP,
            degraded=gateway.degraded or plan_fallback,
        )

    outcomes = []
    try:
        with observability.span(
            "assistant.tools",
            input={"plan": plan},
            metadata={"intent": intent.intent, "commit": commit},
        ) as tools_span:
            for call in plan:
                outcomes.append(
                    execute_tool(
                        session,
                        call["name"],
                        call.get("arguments", {}),
                        commit=False,
                    )
                )
            if commit:
                session.commit()
            tools_span.end(
                output={
                    "tools": [outcome.name for outcome in outcomes],
                    "committed": commit,
                }
            )
    except (ToolError, ValueError, TypeError) as exc:
        session.rollback()
        failed_calls = [
            {
                "name": call["name"],
                "label": tool_catalog_label(call["name"]),
                "arguments": call.get("arguments", {}),
                "status": "error",
                "summary": str(exc),
                "data": {},
            }
            for call in plan
        ]
        return _base_agent_result(
            question,
            started,
            intent,
            gateway=gateway,
            reply=f"操作未完成：{exc}",
            tool_calls=failed_calls,
            degraded=gateway.degraded or plan_fallback,
            model_mode="mock" if plan_fallback else None,
        )

    public_calls = [outcome.public() for outcome in outcomes]
    report = next(
        (
            outcome.data
            for outcome in outcomes
            if outcome.name == "generate_report"
        ),
        None,
    )
    summaries = [outcome.summary for outcome in outcomes]
    reply = "；".join(summaries) + "。"
    if report:
        reply += " 报表已生成，可直接下载。"
    return _base_agent_result(
        question,
        started,
        intent,
        gateway=gateway,
        reply=reply,
        tool_calls=public_calls,
        report=report,
        degraded=gateway.degraded or plan_fallback,
        model_mode="mock" if plan_fallback else None,
    )


def chat(
    session: Session,
    question: str,
    *,
    gateway: LLMGateway | None = None,
    commit: bool = True,
    force_report: bool = False,
    record: bool = True,
) -> dict[str, Any]:
    """助手入口：统一 Langfuse trace 与任务完成率/工具准确率记录。

    ``record=False`` 供黄金评测使用，避免离线评测污染线上指标统计。
    """

    with observability.trace(
        "assistant.chat",
        input={"question": question, "force_report": force_report},
        metadata={"commit": commit},
        tags=["assistant"],
    ) as trace:
        result = _chat(
            session,
            question,
            gateway=gateway,
            commit=commit,
            force_report=force_report,
        )
        query = result.get("query") or {}
        trace.update(
            output={
                "intent": result.get("intent"),
                "reply": result.get("reply"),
                "tools": [
                    {"name": call.get("name"), "status": call.get("status")}
                    for call in result.get("tool_calls") or []
                ],
                "row_count": query.get("row_count"),
                "report_id": (result.get("report") or {}).get("report_id"),
            },
            metadata={
                "model_mode": result.get("model_mode"),
                "degraded": result.get("degraded"),
                "elapsed_ms": result.get("elapsed_ms"),
            },
        )
        observability.add_metadata(
            intent=result.get("intent"),
            intent_confidence=result.get("intent_confidence"),
            tool_count=len(result.get("tool_calls") or []),
        )
        if record:
            evaluation.record_assistant(result)
        return result


def tool_catalog_label(name: str) -> str:
    for item in tool_catalog():
        if item["name"] == name:
            return item["label"]
    return name


def get_meta() -> dict[str, Any]:
    gateway = get_gateway()
    asr = get_asr_service()
    return {
        "intents": [
            {"key": key, "label": INTENT_LABELS[key], "description": value}
            for key, value in INTENT_DESCRIPTIONS.items()
        ],
        "tools": tool_catalog(),
        "examples": AGENT_EXAMPLES,
        "asr": asr.meta(),
        "llm_mode": gateway.effective_mode,
        "llm_degraded": gateway.degraded,
    }
