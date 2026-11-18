"""Prompt 版本文件加载。

Prompt 正文放在 ``app/prompts/{task}/v{n}.md``，文件头用 YAML 元数据记录
变更说明与基线；运行时通过 :func:`load_prompt` 读取，缺文件时回退到代码内
的兜底文案，保证离线可用。
"""

from __future__ import annotations

import re
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"

_FRONT_MATTER = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.DOTALL)


def _strip_front_matter(text: str) -> str:
    return _FRONT_MATTER.sub("", text, count=1).strip()


def load_prompt(task: str, version: str, fallback: str = "") -> str:
    """读取 ``prompts/{task}/{version}.md`` 正文；读取失败返回 fallback。"""

    path = PROMPTS_DIR / task / f"{version}.md"
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return fallback
    body = _strip_front_matter(content)
    return body or fallback


def render_prompt(
    task: str,
    version: str,
    fallback: str,
    **values: object,
) -> str:
    """替换 ``{name}`` 占位符；避免 str.format 与 Markdown 花括号冲突。"""

    text = load_prompt(task, version, fallback)
    for key, value in values.items():
        text = text.replace("{" + key + "}", str(value))
    return text
