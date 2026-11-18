"""LLM 效果评测接口：指标汇总、失败案例与离线黄金评测。"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Query

from app.core.config import settings
from app.eval import golden
from app.schemas.common import ok
from app.services import evaluation

router = APIRouter()

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _golden_path() -> Path:
    path = Path(settings.EVAL_GOLDEN_OUTPUT)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def _display_path(path: Path) -> str:
    """接口只返回仓库内相对路径，避免暴露服务器绝对路径。"""

    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return path.name


@router.get("/summary")
def summary() -> dict:
    return ok(
        {
            "metrics": evaluation.summary(),
            **evaluation.meta(),
        }
    )


@router.get("/failures")
def failures(limit: int = Query(50, ge=1, le=200)) -> dict:
    items = evaluation.recent_failures(limit)
    return ok({"count": len(items), "limit": limit, "items": items})


@router.get("/golden")
def golden_latest() -> dict:
    path = _golden_path()
    if not path.exists():
        return ok(
            {
                "available": False,
                "path": _display_path(path),
                "summary": None,
                "cases": [],
                "failures": [],
            }
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ok(
            {
                "available": False,
                "path": _display_path(path),
                "summary": None,
                "cases": [],
                "failures": [],
            }
        )
    return ok({**payload, "available": True, "path": _display_path(path)})


@router.post("/golden/run")
def golden_run(push: bool = False) -> dict:
    """重新运行黄金用例；push=true 才会把分数上报到 Langfuse。"""

    result = golden.run_golden_cases(push=push)
    if result.get("output_path"):
        result["output_path"] = _display_path(Path(result["output_path"]))
    # 与 GET /golden 保持同样的读取字段，前端运行后可直接渲染。
    result["available"] = True
    result["path"] = _display_path(_golden_path())
    return ok(result)
