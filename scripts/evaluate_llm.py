# -*- coding: utf-8 -*-
"""运行离线黄金评测，输出任务完成率、工具调用准确率、忠实度与表结构召回率。

用法（仓库根目录）：

    python scripts/evaluate_llm.py
    python scripts/evaluate_llm.py --mode real --json
    python scripts/evaluate_llm.py --push
    python scripts/evaluate_llm.py --no-write

默认使用 mock 网关，完全离线、结果确定；``--push`` 才会把黄金用例分数
写入已开启的 Langfuse。写工具在评测中统一回滚，报表文件随后删除，评测
不新增任何数据库表。
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

from app.core.llm import LLMGateway  # noqa: E402
from app.eval.golden import run_golden_cases  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="校园数据问答黄金评测")
    parser.add_argument(
        "--mode",
        choices=("mock", "real", "auto"),
        default="mock",
        help="LLM 模式；默认 mock，完全离线",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="结果 JSON 路径，默认 EVAL_GOLDEN_OUTPUT（data/evals/latest.json）",
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="只在终端输出，不写入结果文件",
    )
    parser.add_argument(
        "--push",
        action="store_true",
        help="把分数上报到 Langfuse（需要 LANGFUSE_ENABLED=true 与凭据）",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="以 JSON 输出完整结果",
    )
    return parser


def _print_summary(result: dict) -> None:
    summary = result["summary"]
    print(f"评测模式：{result.get('mode')}")
    print(
        f"用例：{summary['passed']}/{summary['total']} 通过，"
        f"通过率 {summary['pass_rate']:.2%}"
    )
    print(f"意图准确率：{summary['intent_accuracy']:.2%}")
    print(f"任务完成率：{summary['task_completion_rate']:.2%}")
    tool_accuracy = summary.get("tool_call_accuracy")
    print(
        "工具调用准确率："
        + ("无工具用例" if tool_accuracy is None else f"{tool_accuracy:.2%}")
    )
    print(f"忠实度：{summary['faithfulness_avg']:.2%}")
    recall = summary.get("schema_recall_avg")
    print(
        "表结构召回率："
        + ("未评估" if recall is None else f"{recall:.2%}")
    )
    if result.get("output_path"):
        print(f"结果文件：{result['output_path']}")
    failures = result.get("failures") or []
    if failures:
        print("失败用例：")
        for case in failures:
            print(f"  - {case['id']}：{case['failure']}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    gateway = LLMGateway(mode=args.mode)

    kwargs: dict = {"gateway": gateway, "push": args.push}
    if args.no_write:
        kwargs["output_path"] = None
    elif args.output:
        kwargs["output_path"] = args.output

    result = run_golden_cases(**kwargs)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        _print_summary(result)
    return 0 if result["summary"]["pass_rate"] >= 1.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
