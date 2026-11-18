"""自然语言查数接口。"""

from urllib.parse import quote

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.schemas.common import ok
from app.schemas.query import ExportRequest, QueryRequest
from app.services import query_export, text2sql

router = APIRouter()


@router.post("/nl")
def natural_language_query(
    payload: QueryRequest,
    session: Session = Depends(get_db),
) -> dict:
    return ok(text2sql.answer_question(session, payload.question))


@router.get("/meta")
def meta() -> dict:
    return ok(text2sql.get_meta())


@router.post("/export")
def export(payload: ExportRequest) -> Response:
    content, media_type, filename = query_export.build_export(
        payload.format,
        payload.columns,
        payload.rows,
        payload.filename,
    )
    disposition = (
        "attachment; filename=\"export\"; "
        f"filename*=UTF-8''{quote(filename)}"
    )
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": disposition},
    )
