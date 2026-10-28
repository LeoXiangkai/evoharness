#!/usr/bin/env python3
"""Shared helper for PreToolUse gate hooks: append deny/ask_dropped events to gate-events.jsonl.

Usage (from gate hooks):
    python3 <claude>/hooks/aicj/gate_log.py <hook_name> deny "<rule_first_line>" <stdin_json>
    python3 <claude>/hooks/aicj/gate_log.py <hook_name> ask_dropped "<rule_first_line>" <stdin_json>

Log dir: AICJ_REVIEW_LOG_DIR overrides; default <claude>/aicj/review-logs where <claude>
comes from AICJ_CLAUDE_DIR or is derived from this script (hooks/aicj/<x>.py -> parents[2]).
Failures are swallowed and must not affect the caller's exit code or stderr.
"""
import datetime
import json
import os
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

LOG_DIR_ENV = "AICJ_REVIEW_LOG_DIR"
CLAUDE_DIR_ENV = "AICJ_CLAUDE_DIR"
LOG_FILE = "gate-events.jsonl"


def claude_dir() -> Path:
    env = os.environ.get(CLAUDE_DIR_ENV)
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def review_log_dir() -> Path:
    env = os.environ.get(LOG_DIR_ENV)
    if env:
        return Path(env).expanduser().resolve()
    return (claude_dir() / "aicj" / "review-logs").resolve()


def iso8601() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def sanitize(text: str, max_len: int) -> str:
    text = text.replace("\n", " ").replace("\r", " ").replace("\t", " ")
    if len(text) > max_len:
        text = text[: max_len - 1] + "…"
    return text


def cmd_or_path(payload: dict) -> str:
    ti = payload.get("tool_input") or {}
    cmd = ti.get("command") or ""
    if cmd:
        return cmd
    fp = ti.get("file_path") or ""
    if fp:
        return fp
    return ""


def _mode() -> str:
    try:
        from hook_mode import hook_mode
        return str(hook_mode()) or "unknown"
    except Exception:
        return "unknown"


def append_event(hook_name: str, event: str, rule: str, raw_input: str) -> None:
    try:
        payload = json.loads(raw_input) if raw_input else {}
    except Exception:
        payload = {}

    tool = payload.get("tool_name") or ""
    cmd = cmd_or_path(payload)

    record = {
        "ts": iso8601(),
        "hook": hook_name,
        "event": event,
        "mode": _mode(),
        "rule": sanitize(rule, 80),
        "tool": tool,
        "cmd": sanitize(cmd, 160),
        "session_id": payload.get("session_id") or "",
        "cwd": payload.get("cwd") or "",
    }

    log_dir = review_log_dir()
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / LOG_FILE
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


if __name__ == "__main__":
    if len(sys.argv) < 5:
        sys.exit(0)
    hook_name, event, rule, raw_input = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
    append_event(hook_name, event, rule, raw_input)
