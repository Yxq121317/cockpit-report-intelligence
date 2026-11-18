"""数据库连接与会话管理。"""

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings

ROOT_DIR = Path(__file__).resolve().parents[3]
DATA_DIR = ROOT_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_DATABASE_URL = f"sqlite:///{(DATA_DIR / 'p1.db').as_posix()}"
DATABASE_URL = (settings.P1_DATABASE_URL or DEFAULT_DATABASE_URL).strip()

_engine_kwargs: dict = {}
if DATABASE_URL.startswith("sqlite"):
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    # PostgreSQL 等网络数据库：连接失效时自动重连。
    _engine_kwargs["pool_pre_ping"] = True

engine = create_engine(DATABASE_URL, **_engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def target_sql_dialect() -> str:
    """Text2SQL 以 SQLite 语法编写，执行前转为当前数据库方言。"""
    return "postgres" if engine.dialect.name == "postgresql" else "sqlite"


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def drop_all_tables() -> list[str]:
    """删除当前库中的全部表，返回被删除的表名。

    只用于显式的 ``seed --reset``：项目要求主库最终只保留 8 张业务表。
    """
    import app.models  # noqa: F401  确保模型已注册

    inspector = inspect(engine)
    tables = inspector.get_table_names()
    if not tables:
        return []

    if engine.dialect.name == "sqlite":
        raw = engine.raw_connection()
        driver = getattr(raw, "driver_connection", None) or raw.connection
        try:
            driver.execute("PRAGMA foreign_keys = OFF")
            for table in tables:
                driver.execute(f'DROP TABLE IF EXISTS "{table}"')
            driver.commit()
            driver.execute("PRAGMA foreign_keys = ON")
        finally:
            raw.close()
    else:
        with engine.begin() as connection:
            for table in tables:
                connection.exec_driver_sql(
                    f'DROP TABLE IF EXISTS "{table}" CASCADE'
                )
    return sorted(tables)


def init_db() -> None:
    import app.models  # noqa: F401  确保所有模型已注册到 Base.metadata

    Base.metadata.create_all(bind=engine)
