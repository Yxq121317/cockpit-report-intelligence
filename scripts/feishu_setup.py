#!/usr/bin/env python
"""安全写入飞书应用凭据并检查连通性。

用法（在仓库根目录执行）：

    python scripts/feishu_setup.py
    python scripts/feishu_setup.py --chat-id oc_xxx
    python scripts/feishu_setup.py --verify-only
    python scripts/feishu_setup.py --verify-only --send-test

App Secret、Verification Token、Encrypt Key 只写入本地未被 Git 跟踪的
``.env``，终端不回显、不打印，也不会出现在日志或接口响应中。
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import sys
from pathlib import Path
from typing import Iterable

import httpx

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
EXAMPLE_PATH = ROOT / ".env.example"
DEFAULT_API_BASE = "https://open.feishu.cn"
KEY_PATTERN = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def parse_env(lines: Iterable[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in lines:
        if line.lstrip().startswith("#"):
            continue
        match = KEY_PATTERN.match(line)
        if match:
            values[match.group(1)] = _unquote(match.group(2))
    return values


def format_value(value: str) -> str:
    if value == "":
        return ""
    if re.search(r"[\s#\"'\\]", value):
        return json.dumps(value, ensure_ascii=False)
    return value


def upsert(lines: list[str], key: str, value: str) -> list[str]:
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    rendered = f"{key}={format_value(value)}"
    result: list[str] = []
    insert_at: int | None = None
    for line in lines:
        if pattern.match(line):
            if insert_at is None:
                insert_at = len(result)
            continue
        result.append(line)
    if insert_at is None:
        result.append(rendered)
    else:
        result.insert(insert_at, rendered)
    return result


def write_env(lines: list[str]) -> None:
    content = "\n".join(lines).rstrip("\n") + "\n"
    temporary = ENV_PATH.with_suffix(".env.tmp")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    os.replace(temporary, ENV_PATH)


def ask_text(label: str, current: str) -> str:
    hint = "（已配置，回车保留）" if current else ""
    value = input(f"{label}{hint}: ").strip()
    return value or current


def ask_secret(label: str, current: str) -> str:
    hint = "（已配置，回车保留）" if current else ""
    try:
        value = getpass.getpass(f"{label}{hint}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n已取消。")
        raise SystemExit(1) from None
    return value or current


def print_status(values: dict[str, str]) -> None:
    print("配置状态（不显示任何密钥）：")
    for key in (
        "FEISHU_MODE",
        "FEISHU_APP_ID",
        "FEISHU_APP_SECRET",
        "FEISHU_VERIFICATION_TOKEN",
        "FEISHU_ENCRYPT_KEY",
        "FEISHU_DEFAULT_CHAT_ID",
        "FEISHU_ALLOW_WRITES",
    ):
        marked = "已配置" if values.get(key) else "未配置"
        print(f"- {key}: {marked}")


def _api_error(data: dict, status: int | None = None) -> str:
    message = data.get("msg") or data.get("message")
    code = data.get("code")
    if message:
        return f"{message}（code={code}）"
    return f"HTTP {status}（code={code}）" if status else f"code={code}"


def verify(
    *,
    app_id: str,
    app_secret: str,
    api_base: str,
    chat_id: str,
    send_test: bool,
) -> int:
    if not app_id or not app_secret:
        print("缺少 App ID 或 App Secret，无法连接飞书。")
        return 1

    base = (api_base or DEFAULT_API_BASE).rstrip("/")
    with httpx.Client(timeout=15.0) as client:
        try:
            response = client.post(
                f"{base}/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": app_id, "app_secret": app_secret},
            )
            data = response.json()
        except Exception:  # noqa: BLE001 - 不输出请求细节，避免凭据进入日志
            print("无法连接飞书开放平台，请检查网络或 FEISHU_API_BASE。")
            return 1

        if response.status_code != 200 or data.get("code") != 0:
            print(f"凭据验证失败：{_api_error(data, response.status_code)}")
            return 1
        token = str(data.get("tenant_access_token") or "")
        if not token:
            print("凭据验证失败：飞书未返回 tenant_access_token。")
            return 1
        print("凭据验证成功，已获取 tenant_access_token。")

        headers = {"Authorization": f"Bearer {token}"}
        try:
            chats_response = client.get(
                f"{base}/open-apis/im/v1/chats",
                params={"page_size": 100},
                headers=headers,
            )
            chats_data = chats_response.json()
        except Exception:  # noqa: BLE001
            print("获取群列表请求失败，请检查网络。")
            return 1

        if chats_data.get("code") != 0:
            print(
                "获取群列表失败："
                f"{_api_error(chats_data, chats_response.status_code)}；"
                "请确认已申请 im:chat:readonly 权限并发布应用。"
            )
        else:
            items = (chats_data.get("data") or {}).get("items") or []
            if not items:
                print("机器人尚未加入任何群；请先在飞书群中添加机器人。")
            else:
                print("机器人所在群：")
                for item in items:
                    name = item.get("name") or "（未命名）"
                    item_chat_id = item.get("chat_id") or ""
                    marker = " ← 当前配置" if chat_id and item_chat_id == chat_id else ""
                    print(f"- {name}  chat_id={item_chat_id}{marker}")

        if send_test:
            if not chat_id:
                print("未配置 chat_id，跳过发送测试。")
                return 1
            payload = {
                "receive_id": chat_id,
                "msg_type": "text",
                "content": json.dumps(
                    {"text": "校园数据问答连接测试成功。"},
                    ensure_ascii=False,
                ),
            }
            try:
                send_response = client.post(
                    f"{base}/open-apis/im/v1/messages",
                    params={"receive_id_type": "chat_id"},
                    headers=headers,
                    json=payload,
                )
                send_data = send_response.json()
            except Exception:  # noqa: BLE001
                print("发送测试消息失败，请检查网络。")
                return 1
            if send_data.get("code") != 0:
                print(
                    "发送测试消息失败："
                    f"{_api_error(send_data, send_response.status_code)}；"
                    "请确认机器人已入群并拥有发送消息权限。"
                )
                return 1
            message_id = (send_data.get("data") or {}).get("message_id") or ""
            print(f"测试消息已发送，message_id={message_id}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="安全配置飞书自建应用凭据并检查群连通性"
    )
    parser.add_argument("--chat-id", default="", help="默认群 chat_id")
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="不修改 .env，只验证已有配置",
    )
    parser.add_argument(
        "--send-test",
        action="store_true",
        help="验证成功后向默认群发送一条测试文本",
    )
    parser.add_argument(
        "--api-base",
        default="",
        help="飞书开放平台地址，默认读取 FEISHU_API_BASE 或 open.feishu.cn",
    )
    args = parser.parse_args()

    source = ENV_PATH if ENV_PATH.exists() else EXAMPLE_PATH
    lines = (
        source.read_text(encoding="utf-8").splitlines() if source.exists() else []
    )
    values = parse_env(lines)

    if args.verify_only:
        api_base = (
            args.api_base
            or values.get("FEISHU_API_BASE")
            or DEFAULT_API_BASE
        )
        return verify(
            app_id=values.get("FEISHU_APP_ID", ""),
            app_secret=values.get("FEISHU_APP_SECRET", ""),
            api_base=api_base,
            chat_id=args.chat_id or values.get("FEISHU_DEFAULT_CHAT_ID", ""),
            send_test=args.send_test,
        )

    print("飞书应用凭据会写入本地 .env；输入不回显，也不会打印。")
    app_id = ask_text("App ID", values.get("FEISHU_APP_ID", ""))
    while not app_id:
        print("App ID 不能为空。")
        app_id = ask_text("App ID", "")
    app_secret = ask_secret("App Secret", values.get("FEISHU_APP_SECRET", ""))
    while not app_secret:
        print("App Secret 不能为空。")
        app_secret = ask_secret("App Secret", "")

    chat_id = (
        args.chat_id.strip()
        or ask_text("默认群 chat_id（可留空）", values.get("FEISHU_DEFAULT_CHAT_ID", ""))
    )
    verification_token = ask_secret(
        "Verification Token（可留空）",
        values.get("FEISHU_VERIFICATION_TOKEN", ""),
    )
    encrypt_key = ask_secret(
        "Encrypt Key（可留空）",
        values.get("FEISHU_ENCRYPT_KEY", ""),
    )
    api_base = (
        args.api_base.strip()
        or values.get("FEISHU_API_BASE")
        or DEFAULT_API_BASE
    )

    print("正在连接飞书验证凭据...")
    result = verify(
        app_id=app_id,
        app_secret=app_secret,
        api_base=api_base,
        chat_id=chat_id,
        send_test=args.send_test,
    )
    if result != 0:
        print("验证未通过，未写入 .env。请检查 App ID / App Secret 后重试。")
        return result

    lines = upsert(lines, "FEISHU_MODE", "real")
    lines = upsert(lines, "FEISHU_APP_ID", app_id)
    lines = upsert(lines, "FEISHU_APP_SECRET", app_secret)
    lines = upsert(lines, "FEISHU_DEFAULT_CHAT_ID", chat_id)
    lines = upsert(lines, "FEISHU_VERIFICATION_TOKEN", verification_token)
    lines = upsert(lines, "FEISHU_ENCRYPT_KEY", encrypt_key)
    lines = upsert(lines, "FEISHU_API_BASE", api_base)
    lines = upsert(
        lines,
        "FEISHU_ALLOW_WRITES",
        values.get("FEISHU_ALLOW_WRITES") or "false",
    )
    write_env(lines)
    print(f"已更新 {ENV_PATH.name}。")

    values = parse_env(lines)
    print_status(values)
    return 0


if __name__ == "__main__":
    sys.exit(main())
