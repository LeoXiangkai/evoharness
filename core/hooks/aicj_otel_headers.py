#!/usr/bin/env python3
"""Claude Code otelHeadersHelper: prints {"Authorization": "Bearer <key>"} for the team OTel collector.

The key is read from the process environment first, then from env of <home>/.claude/settings.json
(this script lives in <home>/.claude/hooks/aicj/). It is only ever written to stdout as that header.
Always exits 0; prints {} when no key is found.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

KEYS = ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY")


def _clean(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _first(source: object) -> str:
    if not isinstance(source, dict):
        return ""
    return next((t for t in (_clean(source.get(k)) for k in KEYS) if t), "")


def find_token(script: Path) -> str:
    token = _first(dict(os.environ))
    if token:
        return token
    try:
        payload = json.loads((Path(os.path.abspath(script)).parent.parent.parent / "settings.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, RecursionError):
        return ""
    return _first(payload.get("env")) if isinstance(payload, dict) else ""


def main() -> int:
    try:
        token = find_token(Path(__file__))
    except Exception:  # never let a helper failure surface a traceback near the key
        token = ""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    sys.stdout.write(json.dumps(headers))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
