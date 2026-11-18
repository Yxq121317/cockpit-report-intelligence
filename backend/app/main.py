"""FastAPI 应用入口：8 张校园表 + Text2SQL 自然语言问答。"""

import logging
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import assistant, asr, evaluation, feishu, query, reports
from app.core import observability
from app.core.db import engine, init_db
from app.core.errors import ApiError
from app.core.llm import get_gateway
from app.schemas.common import fail, ok
from app.services.asr import get_asr_service
from app.services.feishu import get_feishu_service
from app.services.feishu_ws import get_feishu_listener

DATABASE_LABEL = (
    "PostgreSQL" if engine.dialect.name == "postgresql" else "SQLite"
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
logger = logging.getLogger("app.startup")


@asynccontextmanager
async def lifespan(application: FastAPI):
    init_db()
    logger.info("数据库就绪 | %s", DATABASE_LABEL)
    logger.info("降级状态 | %s", get_gateway().status_line())
    logger.info("语音状态 | %s", get_asr_service().status_line())
    logger.info("飞书状态 | %s", get_feishu_service().status_line())
    logger.info("观测状态 | %s", observability.status_line())
    listener = get_feishu_listener()
    if listener.start():
        logger.info("飞书长连接 | 已启动（%s）", listener.status())
    else:
        logger.info("飞书长连接 | 未启用（%s）", listener.status())
    try:
        yield
    finally:
        listener.stop()
        observability.shutdown()


app = FastAPI(
    title="校园数据问答 · Text2SQL",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5171", "http://127.0.0.1:5171"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(query.router, prefix="/api/query", tags=["query"])
app.include_router(assistant.router, prefix="/api/assistant", tags=["assistant"])
app.include_router(asr.router, prefix="/api/asr", tags=["asr"])
app.include_router(reports.router, prefix="/api/reports", tags=["reports"])
app.include_router(feishu.router, prefix="/api/feishu", tags=["feishu"])
app.include_router(evaluation.router, prefix="/api/eval", tags=["eval"])


@app.exception_handler(ApiError)
async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=fail(exc.code, exc.message),
    )


@app.exception_handler(HTTPException)
async def handle_http_error(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=fail(exc.status_code, str(exc.detail)),
    )


@app.exception_handler(RequestValidationError)
async def handle_validation_error(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    errors = []
    for error in exc.errors():
        item = {key: value for key, value in error.items() if key != "ctx"}
        if error.get("ctx"):
            item["ctx"] = {key: str(value) for key, value in error["ctx"].items()}
        errors.append(item)
    return JSONResponse(
        status_code=422,
        content=fail(1001, "请求参数校验失败", errors),
    )


@app.get("/api/health")
def health() -> dict:
    return ok({"status": "ok", "trace_id": uuid4().hex})
