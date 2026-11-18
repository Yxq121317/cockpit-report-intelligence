# -*- coding: utf-8 -*-
"""生成校园数据总览模板报表（8 张业务表各一个 Sheet）。

用法（仓库根目录）：

    python scripts/generate_campus_report.py --json
    python scripts/generate_campus_report.py --target 八年级 --json
    python scripts/generate_campus_report.py --start-date 2026-09-01 \\
        --end-date 2026-11-03 --json
    python scripts/generate_campus_report.py --push --json

报表只读取现有 8 张业务表，文件写入 ``data/reports/{report_id}.xlsx``，
不新增数据库表。``--push`` 需要本地 ``.env`` 中配置可用的飞书应用凭据。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.db import SessionLocal  # noqa: E402
from app.services.report import ReportError, generate_report  # noqa: E402

PUBLIC_FIELDS = (
    "report_id",
    "report_type",
    "title",
    "filename",
    "file_path",
    "download_url",
    "row_count",
    "sheets",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="生成校园数据总览模板报表（8 个 Sheet）",
    )
    parser.add_argument(
        "--target",
        default=None,
        help="统计范围：全校（默认）、八年级或八年级一班",
    )
    parser.add_argument(
        "--start-date",
        default=None,
        help="出勤 / 请假统计开始日期 YYYY-MM-DD，默认最近 30 天",
    )
    parser.add_argument(
        "--end-date",
        default=None,
        help="出勤 / 请假统计结束日期 YYYY-MM-DD，默认最新考勤日期",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="报表输出目录，默认 data/reports",
    )
    parser.add_argument(
        "--push",
        action="store_true",
        help="生成后推送到飞书群（需要 FEISHU_MODE=real 与凭据）",
    )
    parser.add_argument(
        "--chat-id",
        default=None,
        help="飞书群 chat_id；省略时使用 FEISHU_DEFAULT_CHAT_ID",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="以 JSON 输出生成结果，便于脚本或 Skill 解析",
    )
    return parser


def _public_report(report: dict) -> dict:
    return {key: report.get(key) for key in PUBLIC_FIELDS if key in report}


def _print_result(payload: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    print(f"报表已生成：{payload.get('title')}")
    print(f"文件路径：{payload.get('file_path')}")
    print(f"下载地址：{payload.get('download_url')}")
    print(f"总行数：{payload.get('row_count')}")
    for sheet in payload.get("sheets", []):
        print(f"  - {sheet['name']}：{sheet['row_count']} 行")
    if "push" in payload:
        pushed = payload["push"]
        print(
            "飞书推送："
            f"mode={pushed.get('mode')}，chat_id={pushed.get('chat_id')}"
        )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output_dir = (
        Path(args.output_dir).expanduser().resolve()
        if args.output_dir
        else None
    )

    try:
        with SessionLocal() as session:
            report = generate_report(
                session,
                report_type="campus_overview",
                target=args.target,
                start_date=args.start_date,
                end_date=args.end_date,
                output_dir=output_dir,
            )
    except ReportError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1

    payload: dict = {"ok": True, **_public_report(report)}
    if args.push:
        from app.services.feishu import FeishuError, get_feishu_service

        try:
            payload["push"] = get_feishu_service().push_report(
                report["report_id"], args.chat_id
            )
        except FeishuError as exc:
            payload["ok"] = False
            payload["error"] = str(exc)
            _print_result(payload, args.json)
            return 2

    _print_result(payload, args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
