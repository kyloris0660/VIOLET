#!/usr/bin/env python3
"""Tiny local OpenAI-compatible translation server for isolated E2E tests."""

from __future__ import annotations

import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


def _extract_tags(payload: dict[str, Any]) -> list[dict[str, str]]:
    messages = payload.get("messages") or []
    text = "\n".join(str(message.get("content") or "") for message in messages if isinstance(message, dict))
    user_payloads = [
        str(message.get("content") or "")
        for message in messages
        if isinstance(message, dict) and str(message.get("role") or "") == "user"
    ]

    def balanced_json_arrays(source: str) -> list[str]:
        arrays: list[str] = []
        for start, char in enumerate(source):
            if char != "[":
                continue
            stack = ["["]
            in_string = False
            escaped = False
            for index in range(start + 1, len(source)):
                current = source[index]
                if in_string:
                    if escaped:
                        escaped = False
                    elif current == "\\":
                        escaped = True
                    elif current == '"':
                        in_string = False
                    continue
                if current == '"':
                    in_string = True
                elif current == "[":
                    stack.append("[")
                elif current == "]":
                    stack.pop()
                    if not stack:
                        arrays.append(source[start:index + 1])
                        break
        return arrays

    candidates: list[str] = []
    for content in user_payloads:
        candidates.extend(balanced_json_arrays(content))
    candidates.extend(balanced_json_arrays(text))
    for match in reversed(candidates):
        try:
            parsed = json.loads(match)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
    return []


class Handler(BaseHTTPRequestHandler):
    server_version = "VioletFakeOpenAITranslation/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
        if self.path.rstrip("/") == "/health":
            self._json({"ok": True})
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
        if self.path.rstrip("/") != "/v1/chat/completions":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length") or "0")
        body = self.rfile.read(length)
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            payload = {}
        tags = _extract_tags(payload)
        translations = [
            {
                "canonical_name": str(tag.get("name") or ""),
                "display_name_zh": f"测试翻译-{str(tag.get('name') or '')}",
                "aliases_zh": [],
                "notes": "local isolated E2E fake provider",
                "needs_review": False,
            }
            for tag in tags
            if str(tag.get("name") or "")
        ]
        self._json(
            {
                "id": "violet-local-e2e",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": json.dumps(translations, ensure_ascii=False)},
                        "finish_reason": "stop",
                    }
                ],
            }
        )

    def _json(self, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8025)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(json.dumps({"ok": True, "host": args.host, "port": args.port}, ensure_ascii=False), flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
