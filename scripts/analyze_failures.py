# -*- coding: utf-8 -*-
"""分析本地失败案例 JSONL，按类别汇总并给出优化方向。

用法（仓库根目录）：

    python scripts/analyze_failures.py
    python scripts/analyze_failures.py --limit 20
    python scripts/analyze_failures.py --category unfaithful_answer --json

失败案例由 ``EVAL_FAILURE_LOG=true`` 时的线上请求写入，路径默认
``data/evals/failure_cases.jsonl``；只读本地文件，不访问数据库或外网。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import settings  # noqa: E402

SUGGESTIONS = {
    "clarification": "补充 mock 模板或提示词示例，覆盖未识别的问法。",
    "query_failed": "检查 SQL 校验/执行错误，必要时回退完整表结构重试。",
    "unfaithful_answer": "收紧回答模板，只引用本次 SQL 结果中的数字和日期。",
    "tool_call_failed": "补充意图关键词与工具参数校验，确认必填字段。",
    "assistant_failed": "检查意图路由与工具规划回退逻辑。",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="分析本地失败案例 JSONL")
    parser.add_argument(
        "--path",
        default=None,
        help="失败案例路径，默认 EVAL_FAILURE_LOG_PATH",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="最多展示多少条最近案例，默认 20",
    )
    parser.add_argument(
        "--category",
        default=None,
        help="只分析指定类别，例如 clarification",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="以 JSON 输出完整分析结果",
    )
    return parser


def _resolve_path(raw: str | None) -> Path:
    path = Path(raw or settings.EVAL_FAILURE_LOG_PATH)
    if not path.is_absolute():
        path = ROOT_DIR / path
    return path


def _read_records(path: Path) -> tuple[list[dict], int]:
    if not path.exists():
        return [], 0
    records: list[dict] = []
    invalid = 0
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                invalid += 1
                continue
            if isinstance(item, dict):
                records.append(item)
            else:
                invalid += 1
    return records, invalid


def analyze(
    records: list[dict], *, limit: int, category: str | None = None
) -> dict:
    if category:
        records = [
            record for record in records if record.get("category") == category
        ]
    by_category = Counter(str(record.get("category")) for record in records)
    by_scope = Counter(str(record.get("scope")) for record in records)
    questions = Counter(
        str(record.get("question") or "") for record in records
    )
    latest = list(reversed(records))[: max(1, limit)]
    return {
        "total": len(records),
        "by_category": dict(by_category),
        "by_scope": dict(by_scope),
        "top_questions": [
            {"question": question, "count": count}
            for question, count in questions.most_common(10)
            if question
        ],
        "suggestions": {
            name: SUGGESTIONS.get(name, "人工检查失败详情。")
            for name in by_category
        },
        "items": latest,
    }


def _print_report(path: Path, invalid: int, result: dict) -> None:
    print(f"失败案例文件：{path}")
    print(f"有效案例：{result['total']} 条；无法解析：{invalid} 条")
    if not result["total"]:
        print("暂无失败案例。可设置 EVAL_FAILURE_LOG=true 收集线上失败样本。")
        return
    print("按类别：")
    for name, count in sorted(
        result["by_category"].items(), key=lambda item: -item[1]
    ):
        print(f"  - {name}：{count} 条 —— {result['suggestions'].get(name, '')}")
    print("按来源：")
    for name, count in sorted(
        result["by_scope"].items(), key=lambda item: -item[1]
    ):
        print(f"  - {name}：{count} 条")
    if result["top_questions"]:
        print("高频问题：")
        for item in result["top_questions"]:
            print(f"  - {item['question']}（{item['count']} 次）")
    print("最近案例：")
    for item in result["items"]:
        print(
            f"  - [{item.get('category')}] {item.get('question')} "
            f"({item.get('at')})"
        )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    path = _resolve_path(args.path)
    try:
        records, invalid = _read_records(path)
    except OSError as exc:
        print(
            json.dumps(
                {"ok": False, "error": f"读取失败：{type(exc).__name__}"},
                ensure_ascii=False,
            )
        )
        return 1
    result = analyze(records, limit=args.limit, category=args.category)
    if args.json:
        print(
            json.dumps(
                {"ok": True, "path": str(path), "invalid": invalid, **result},
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
    else:
        _print_report(path, invalid, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
