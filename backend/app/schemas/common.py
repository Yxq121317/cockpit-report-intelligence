"""统一响应结构与通用 schema 辅助。"""

from typing import Any
from uuid import uuid4


def ok(data: Any = None, message: str = "ok") -> dict:
    return {
        "code": 0,
        "message": message,
        "data": data,
        "trace_id": uuid4().hex,
    }


def fail(code: int, message: str, data: Any = None) -> dict:
    return {
        "code": code,
        "message": message,
        "data": data,
        "trace_id": uuid4().hex,
    }
