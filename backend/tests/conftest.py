import os
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

_TMP_DIR = tempfile.mkdtemp(prefix="p1-test-")
os.environ["P1_DATABASE_URL"] = (
    f"sqlite:///{(Path(_TMP_DIR) / 'p1-test.db').as_posix()}"
)
os.environ["LLM_MODE"] = "mock"
os.environ["API_KEY"] = ""
os.environ["BASE_URL"] = ""
os.environ["ASR_MODE"] = "mock"
os.environ["ASR_API_KEY"] = ""
os.environ["FEISHU_MODE"] = "mock"
os.environ["FEISHU_APP_ID"] = ""
os.environ["FEISHU_APP_SECRET"] = ""
os.environ["FEISHU_VERIFICATION_TOKEN"] = ""
os.environ["FEISHU_ENCRYPT_KEY"] = ""
os.environ["FEISHU_PUSH_TOKEN"] = ""
os.environ["FEISHU_DEFAULT_CHAT_ID"] = ""
os.environ["LANGFUSE_ENABLED"] = "false"
os.environ["LANGFUSE_PUBLIC_KEY"] = ""
os.environ["LANGFUSE_SECRET_KEY"] = ""
os.environ["LANGFUSE_HOST"] = "https://cloud.langfuse.com"
os.environ["EVAL_FAILURE_LOG"] = "false"
os.environ["EVAL_FAILURE_LOG_PATH"] = str(
    Path(_TMP_DIR) / "failure_cases.jsonl"
)
os.environ["EVAL_GOLDEN_OUTPUT"] = str(Path(_TMP_DIR) / "latest.json")
os.environ["TEXT2SQL_SCHEMA_RETRIEVAL"] = "true"

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="session")
def seeded_db():
    from app.core.db import SessionLocal, drop_all_tables, init_db
    from app.seed.seed import seed_database

    drop_all_tables()
    init_db()
    with SessionLocal() as session:
        seed_database(session)
    yield


@pytest.fixture(scope="session")
def client(seeded_db):
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def session(seeded_db):
    from app.core.db import SessionLocal

    with SessionLocal() as db_session:
        yield db_session


@pytest.fixture()
def feishu(seeded_db):
    from app.services.feishu import get_feishu_service

    service = get_feishu_service()
    service.mock_outbox.clear()
    service.clear_seen()
    return service
