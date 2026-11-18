"""初始化 8 张校园业务表并生成确定性示例数据。

用法：
    python -m app.seed.seed
    python -m app.seed.seed --reset
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import SessionLocal, drop_all_tables, init_db
from app.models import (
    Attendance,
    Class,
    Grade,
    LeaveRequest,
    Parent,
    Student,
    Teacher,
    Timetable,
)

SEED = 20261103
SEMESTER = "2026-2027学年第一学期"
TERM_START = date(2026, 9, 1)
TERM_END = date(2026, 11, 3)

GRADE_NAMES = ["七年级", "八年级", "九年级"]
CLASS_LABELS = ["一班", "二班"]
SUBJECTS = [
    "语文",
    "数学",
    "英语",
    "物理",
    "化学",
    "历史",
    "地理",
    "生物",
    "道德与法治",
    "体育",
    "音乐",
    "美术",
    "信息技术",
]
SUBJECT_PATTERNS = {
    "七年级": [
        "语文",
        "数学",
        "英语",
        "历史",
        "地理",
        "生物",
        "道德与法治",
        "体育",
    ],
    "八年级": [
        "语文",
        "数学",
        "英语",
        "物理",
        "历史",
        "地理",
        "生物",
        "道德与法治",
        "体育",
    ],
    "九年级": [
        "语文",
        "数学",
        "英语",
        "物理",
        "化学",
        "历史",
        "道德与法治",
        "体育",
    ],
}
PERIOD_TIMES = [
    ("08:00", "08:45"),
    ("08:55", "09:40"),
    ("10:00", "10:45"),
    ("10:55", "11:40"),
    ("14:00", "14:45"),
    ("14:55", "15:40"),
]

SURNAMES = list(
    "王李张刘陈杨赵黄周吴徐孙胡朱高林何郭马罗梁宋郑谢韩唐冯于董萧"
    "程曹袁邓许傅沈曾彭吕苏卢蒋蔡贾丁魏薛叶阎余潘杜戴夏钟汪田任姜范"
)
MALE_GIVEN = [
    "子轩",
    "浩然",
    "宇航",
    "俊杰",
    "明轩",
    "天佑",
    "嘉豪",
    "志远",
    "文博",
    "鹏飞",
    "建国",
    "伟",
    "强",
    "磊",
    "军",
    "洋",
    "勇",
    "涛",
    "杰",
    "鹏",
]
FEMALE_GIVEN = [
    "雨涵",
    "欣怡",
    "梓萱",
    "诗涵",
    "梦琪",
    "佳怡",
    "思雨",
    "语嫣",
    "若曦",
    "静怡",
    "秀英",
    "芳",
    "娜",
    "敏",
    "静",
    "丽",
    "娟",
    "艳",
    "婷",
    "雪",
]
OCCUPATIONS = ["工程师", "教师", "医生", "会计", "个体经营", "公务员", "职员", "自由职业"]
LEAVE_REASONS = [
    "身体不适，需在家休息",
    "家中有事，需请假一天",
    "感冒发烧，医院就诊",
    "参加家庭重要活动",
    "牙科复诊",
    "校外比赛集训",
]
LEAVE_TYPES = ["病假", "事假", "其他"]


def weekdays(start: date, end: date) -> list[date]:
    days: list[date] = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def _student_names(rng: random.Random, count: int) -> list[tuple[str, str]]:
    """返回 count 个不重复的（姓名，性别）。"""
    used = {"张小明", "李雷"}
    names: list[tuple[str, str]] = []
    while len(names) < count:
        gender = "男" if rng.random() < 0.52 else "女"
        given = rng.choice(MALE_GIVEN if gender == "男" else FEMALE_GIVEN)
        surname = rng.choice(SURNAMES)
        name = surname + given
        if name in used:
            continue
        used.add(name)
        names.append((name, gender))
    return names


def _teacher_names(rng: random.Random, count: int) -> list[tuple[str, str]]:
    names: list[tuple[str, str]] = []
    used: set[str] = set()
    while len(names) < count:
        gender = "男" if rng.random() < 0.5 else "女"
        given = rng.choice(MALE_GIVEN if gender == "男" else FEMALE_GIVEN)
        name = rng.choice(SURNAMES) + given
        if name in used:
            continue
        used.add(name)
        names.append((name, gender))
    return names


def _make_data(session: Session) -> dict[str, int]:
    rng = random.Random(SEED)

    grades: list[Grade] = []
    for index, name in enumerate(GRADE_NAMES, start=1):
        grade = Grade(name=name, level=index + 6, sort=index)
        grades.append(grade)
        session.add(grade)
    session.flush()

    teacher_names = _teacher_names(rng, 24)
    teachers: list[Teacher] = []
    for index, (name, gender) in enumerate(teacher_names):
        teacher = Teacher(
            teacher_no=f"T2026{index + 1:03d}",
            name=name,
            gender=gender,
            subject=SUBJECTS[index % len(SUBJECTS)],
            title=["高级教师", "一级教师", "二级教师"][index % 3],
            phone=f"137{rng.randrange(10**8):08d}",
            hire_date=date(2015 + index % 9, (index % 12) + 1, 10),
        )
        teachers.append(teacher)
        session.add(teacher)
    session.flush()

    classes: list[Class] = []
    for grade_index, grade in enumerate(grades):
        for class_index, label in enumerate(CLASS_LABELS):
            head_teacher = teachers[(grade_index * 2 + class_index) % len(teachers)]
            classroom = f"教学楼{grade.level}-{(grade_index * 2 + class_index + 1):02d}"
            klass = Class(
                grade_id=grade.id,
                name=f"{grade.name}{label}",
                head_teacher_id=head_teacher.id,
                classroom=classroom,
            )
            classes.append(klass)
            session.add(klass)
    session.flush()

    student_names = _student_names(rng, 180)
    students: list[Student] = []
    student_index = 0
    for class_index, klass in enumerate(classes):
        grade = grades[class_index // 2]
        for offset in range(30):
            if klass.name == "八年级一班" and offset == 0:
                name, gender = "张小明", "男"
            elif klass.name == "八年级二班" and offset == 0:
                name, gender = "李雷", "男"
            else:
                name, gender = student_names[student_index]
            student_index += 1
            student = Student(
                student_no=f"2026{student_index:04d}",
                name=name,
                gender=gender,
                birth_date=date(
                    2009 + class_index // 2,
                    (student_index % 12) + 1,
                    (student_index % 27) + 1,
                ),
                grade_id=grade.id,
                class_id=klass.id,
                enrollment_date=TERM_START,
                status="在读",
                phone=f"139{rng.randrange(10**8):08d}",
                address=f"示例市{grade.name}{klass.name}学区",
            )
            students.append(student)
            session.add(student)
    session.flush()

    parents: list[dict] = []
    for student in students:
        surname = student.name[0]
        father_given = rng.choice(MALE_GIVEN)
        mother_given = rng.choice(FEMALE_GIVEN)
        parents.append(
            {
                "student_id": student.id,
                "name": surname + father_given,
                "relation": "父亲",
                "phone": f"138{rng.randrange(10**8):08d}",
                "occupation": rng.choice(OCCUPATIONS),
                "is_primary": True,
            }
        )
        parents.append(
            {
                "student_id": student.id,
                "name": rng.choice(SURNAMES) + mother_given,
                "relation": "母亲",
                "phone": f"136{rng.randrange(10**8):08d}",
                "occupation": rng.choice(OCCUPATIONS),
                "is_primary": False,
            }
        )
    session.bulk_insert_mappings(Parent, parents)

    class_head_map = {klass.id: klass.head_teacher_id for klass in classes}
    attendance_rows: list[dict] = []
    for day in weekdays(TERM_START, TERM_END):
        for student in students:
            roll = rng.random()
            if roll < 0.90:
                status = "正常"
            elif roll < 0.94:
                status = "迟到"
            elif roll < 0.95:
                status = "早退"
            elif roll < 0.985:
                status = "缺勤"
            else:
                status = "请假"
            if status == "正常":
                check_in = f"07:{rng.randrange(35, 60):02d}"
                remark = ""
            elif status == "迟到":
                check_in = f"08:{rng.randrange(1, 31):02d}"
                remark = "迟到"
            elif status == "早退":
                check_in = "07:45"
                remark = "下午早退"
            elif status == "缺勤":
                check_in = None
                remark = "未到校"
            else:
                check_in = None
                remark = "已办理请假"
            attendance_rows.append(
                {
                    "student_id": student.id,
                    "class_id": student.class_id,
                    "date": day,
                    "status": status,
                    "check_in_time": check_in,
                    "remark": remark,
                }
            )
    session.bulk_insert_mappings(Attendance, attendance_rows)

    term_days = weekdays(TERM_START, TERM_END)
    leave_rows: list[dict] = []
    for index in range(60):
        student = students[(index * 7 + 3) % len(students)]
        if index < 5:
            start = date(2026, 11, 2) + timedelta(days=index % 2)
            status = "待审批"
        else:
            start = rng.choice(term_days[:-3])
            status = rng.choices(
                ["已批准", "待审批", "已驳回"], weights=[0.6, 0.2, 0.2]
            )[0]
        end = start + timedelta(days=rng.randrange(0, 3))
        applied = datetime.combine(
            start - timedelta(days=1), time(9, 0)
        ) + timedelta(minutes=rng.randrange(0, 180))
        approved = status != "待审批"
        leave_rows.append(
            {
                "student_id": student.id,
                "class_id": student.class_id,
                "leave_type": rng.choice(LEAVE_TYPES),
                "start_date": start,
                "end_date": end,
                "days": (end - start).days + 1,
                "reason": rng.choice(LEAVE_REASONS),
                "status": status,
                "applied_at": applied,
                "approver_teacher_id": (
                    class_head_map.get(student.class_id) if approved else None
                ),
                "approved_at": (
                    applied + timedelta(days=1) if approved else None
                ),
                "remark": "",
            }
        )
    session.bulk_insert_mappings(LeaveRequest, leave_rows)

    subject_teachers: dict[str, list[int]] = {}
    for teacher in teachers:
        subject_teachers.setdefault(teacher.subject, []).append(teacher.id)

    timetable_rows: list[dict] = []
    for class_index, klass in enumerate(classes):
        grade = grades[class_index // 2]
        pattern = SUBJECT_PATTERNS[grade.name]
        for weekday_index in range(1, 6):
            for period_index in range(1, 7):
                subject = pattern[
                    (weekday_index + period_index + class_index) % len(pattern)
                ]
                teacher_id = subject_teachers[subject][
                    class_index % len(subject_teachers[subject])
                ]
                start_time, end_time = PERIOD_TIMES[period_index - 1]
                timetable_rows.append(
                    {
                        "class_id": klass.id,
                        "weekday": weekday_index,
                        "period": period_index,
                        "subject": subject,
                        "teacher_id": teacher_id,
                        "classroom": klass.classroom,
                        "semester": SEMESTER,
                        "start_time": start_time,
                        "end_time": end_time,
                    }
                )
    session.bulk_insert_mappings(Timetable, timetable_rows)

    session.commit()
    return _stats(session)


def _stats(session: Session) -> dict[str, int]:
    models = {
        "grades": Grade,
        "teachers": Teacher,
        "classes": Class,
        "students": Student,
        "parents": Parent,
        "attendance": Attendance,
        "leave_requests": LeaveRequest,
        "timetable": Timetable,
    }
    return {
        name: int(
            session.scalar(select(func.count()).select_from(model)) or 0
        )
        for name, model in models.items()
    }


def seed_database(session: Session) -> dict[str, int]:
    """幂等初始化：已有年级数据时直接返回现有统计。"""
    existing = session.scalar(select(func.count()).select_from(Grade)) or 0
    if existing:
        return _stats(session)
    return _make_data(session)


def main() -> None:
    parser = argparse.ArgumentParser(description="初始化 8 张校园业务表")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="删除当前库全部表后重建（会清空旧数据）",
    )
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    if args.reset:
        dropped = drop_all_tables()
        print(f"已删除旧表 {len(dropped)} 张")
    init_db()
    with SessionLocal() as session:
        stats = seed_database(session)
    print("种子数据就绪：")
    for name, count in stats.items():
        print(f"  {name}: {count}")


if __name__ == "__main__":
    main()
