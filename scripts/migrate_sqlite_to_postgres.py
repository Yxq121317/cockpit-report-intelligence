# -*- coding: utf-8 -*-
"""把现有 SQLite 演示数据一次性迁移到 PostgreSQL。

用法（仓库根目录）：

    python scripts/migrate_sqlite_to_postgres.py
    python scripts/migrate_sqlite_to_postgres.py --source data/p1.db --target postgresql://...

目标库优先读取 ``P1_DATABASE_URL``（.env 或环境变量）。目标库非空时默认拒绝，
确认要覆盖时显式加 ``--truncate``。脚本只打印表名与行数，不打印连接串中的密码。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import settings  # noqa: E402
from app.core.db import Base  # noqa: E402
import app.models  # noqa: E402,F401  确保全部模型已注册

BATCH_SIZE = 1000


def _safe_url(url: str) -> str:
    try:
        return make_url(url).render_as_string(hide_password=True)
    except Exception:  # noqa: BLE001 - 仅用于日志
        return "<无法解析的连接串>"


def _reset_sequences(connection) -> None:
    """让 PostgreSQL 的自增序列从已迁移数据的最大 id 继续。"""
    for table in Base.metadata.sorted_tables:
        id_column = table.c.get("id")
        if id_column is None or not id_column.primary_key:
            continue
        sequence = connection.execute(
            text("SELECT pg_get_serial_sequence(:table_name, 'id')"),
            {"table_name": table.name},
        ).scalar()
        if not sequence:
            continue
        max_id = connection.execute(
            text(f"SELECT MAX(id) FROM {table.name}")
        ).scalar()
        if max_id is None:
            continue
        connection.execute(
            text("SELECT setval(:sequence, :value, true)"),
            {"sequence": sequence, "value": max_id},
        )


def migrate(source: Path, target_url: str, *, truncate: bool) -> None:
    if not source.exists():
        raise SystemExit(f"源 SQLite 不存在：{source}")
    if target_url.startswith("sqlite"):
        raise SystemExit("目标库不能是 SQLite")

    source_engine = create_engine(f"sqlite:///{source.as_posix()}")
    target_engine = create_engine(target_url, pool_pre_ping=True)
    Base.metadata.create_all(bind=target_engine)

    tables = list(Base.metadata.sorted_tables)
    with target_engine.connect() as connection:
        nonempty = [
            table.name
            for table in tables
            if connection.scalar(select(func.count()).select_from(table))
        ]
    if nonempty and not truncate:
        raise SystemExit(
            "目标库已有数据，已停止迁移。确认覆盖后加 --truncate："
            + ", ".join(nonempty)
        )

    print(f"源库：{source}")
    print(f"目标库：{_safe_url(target_url)}")
    print(f"表数量：{len(tables)}")

    with target_engine.begin() as target:
        if truncate:
            names = ", ".join(table.name for table in tables)
            target.execute(text(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE"))
            print("已清空目标库现有表")

        with source_engine.connect() as source_connection:
            for table in tables:
                batch: list[dict] = []
                copied = 0
                result = source_connection.execute(
                    select(table).execution_options(yield_per=BATCH_SIZE)
                )
                for row in result.mappings():
                    batch.append(dict(row))
                    if len(batch) >= BATCH_SIZE:
                        target.execute(table.insert(), batch)
                        copied += len(batch)
                        batch.clear()
                if batch:
                    target.execute(table.insert(), batch)
                    copied += len(batch)
                print(f"  {table.name}: {copied} 行")

        _reset_sequences(target)

    with target_engine.connect() as connection:
        for table in tables:
            expected = 0
            with source_engine.connect() as source_connection:
                expected = source_connection.scalar(
                    select(func.count()).select_from(table)
                )
            actual = connection.scalar(select(func.count()).select_from(table))
            if actual != expected:
                raise SystemExit(
                    f"校验失败：{table.name} 源 {expected} 行，目标 {actual} 行"
                )
    source_engine.dispose()
    target_engine.dispose()
    print("迁移完成，行数校验全部通过。")


def main() -> None:
    parser = argparse.ArgumentParser(description="SQLite → PostgreSQL 数据迁移")
    parser.add_argument(
        "--source",
        type=Path,
        default=ROOT_DIR / "data" / "p1.db",
        help="源 SQLite 文件（默认 data/p1.db）",
    )
    parser.add_argument(
        "--target",
        default=settings.P1_DATABASE_URL,
        help="目标 PostgreSQL 连接串（默认读取 P1_DATABASE_URL）",
    )
    parser.add_argument(
        "--truncate",
        action="store_true",
        help="目标库非空时先清空再迁移（默认拒绝）",
    )
    args = parser.parse_args()
    if not args.target:
        raise SystemExit("未配置 P1_DATABASE_URL，也未传入 --target")
    migrate(args.source, args.target, truncate=args.truncate)


if __name__ == "__main__":
    main()
