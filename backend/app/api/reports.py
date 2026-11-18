"""报表下载接口：只读取 data/reports 下已生成的 XLSX。"""

from urllib.parse import quote

from fastapi import APIRouter
from fastapi.responses import Response

from app.core.errors import ApiError
from app.services.report import ReportError, resolve_report

router = APIRouter()

_XLSX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


@router.get("/{report_id}/download")
def download(report_id: str) -> Response:
    try:
        path, metadata = resolve_report(report_id)
    except ReportError as exc:
        raise ApiError(1003, str(exc), status_code=404) from exc
    filename = metadata.get("filename") or f"{metadata.get('title', '校园报表')}.xlsx"
    disposition = (
        "attachment; filename=\"report.xlsx\"; "
        f"filename*=UTF-8''{quote(filename)}"
    )
    return Response(
        content=path.read_bytes(),
        media_type=_XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": disposition},
    )
