#!/usr/bin/env python3
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from hook_mode import hook_mode
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "core" / "hooks"))
    from hook_mode import hook_mode

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except AttributeError:
        pass


def claude_dir() -> Path:
    value = os.environ.get("AICJ_CLAUDE_DIR")
    if value:
        return Path(value).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def event() -> dict:
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def state_key(session: str, card: str) -> Path:
    digest = hashlib.sha256(f"{session}\n{card}".encode()).hexdigest()
    return claude_dir() / "aicj" / "state" / "lesson-recall" / digest


def main() -> int:
    data = event()
    inp = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    tool = str(data.get("tool_name", ""))
    command = inp.get("command", "") if tool == "Bash" else inp.get("file_path", inp.get("path", ""))
    if not isinstance(command, str):
        return 0
    cwd = str(data.get("cwd") or (inp.get("cwd") if isinstance(inp, dict) else "") or os.getcwd())
    session = str(data.get("session_id") or data.get("sessionId") or "default")
    cards_path = claude_dir() / "aicj" / "lessons" / "cards.jsonl"
    matches: list[dict] = []
    if cards_path.is_file():
        try:
            lines = cards_path.read_bytes().decode("utf-8", errors="ignore").splitlines()
        except OSError:
            lines = []
        for line in lines:
            try:
                card = json.loads(line)
                if not isinstance(card, dict) or not isinstance(card.get("trigger"), dict):
                    continue
                if card.get("status") != "active" or tool not in card.get("trigger", {}).get("tools", []):
                    continue
                scope = card.get("scope", "global")
                if scope != "global" and (not isinstance(scope, str) or not cwd.startswith(scope)):
                    continue
                trigger = card.get("trigger", {})
                if tool == "Bash":
                    pattern = trigger.get("cmd_regex")
                    if not pattern or not re.search(pattern, command):
                        continue
                else:
                    pattern = trigger.get("path_glob")
                    if not pattern or not fnmatch.fnmatch(command, pattern):
                        continue
                matches.append(card)
            except (ValueError, TypeError, AttributeError, re.error):
                continue  # bad row / bad regex: skip this card only
    injections: list[str] = []
    blocks: list[str] = []
    seen_dir = claude_dir() / "aicj" / "state" / "lesson-recall"
    try:
        seen_dir.mkdir(parents=True, exist_ok=True)
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        for old in seen_dir.iterdir():
            try:
                if datetime.fromtimestamp(old.stat().st_mtime, timezone.utc) < cutoff:
                    old.unlink()
            except OSError:
                pass
    except OSError:
        pass
    for card in matches:
        cid = str(card.get("id", ""))
        if not cid:
            continue
        marker = state_key(session, cid)
        if marker.exists():
            continue
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
        except OSError:
            pass
        try:  # raw recall row for team telemetry; nothing is written without a team config
            if not (claude_dir() / "aicj" / "team.json").is_file():
                raise OSError("no team config")
            recalls = claude_dir() / "aicj" / "lessons" / "recalls.jsonl"
            row = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "card": cid,
                   "level": str(card.get("level", "")), "session_id": session, "cwd": cwd}
            with recalls.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError:
            pass
        lesson = str(card.get("lesson", "")).strip()
        line = f"[lesson:{cid}] {lesson}"
        if card.get("level") == "block":
            blocks.append(line)
        else:
            injections.append(line)
    if blocks and hook_mode() == "block":
        sys.stderr.write("\n".join(blocks) + "\n")
        return 2
    lines = blocks + injections
    if lines:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": "\n".join(lines)}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:  # fail-open: a recall failure must never block a tool call
        raise SystemExit(0)
