#!/usr/bin/env python3
"""PreToolUse/PostToolUse/PostToolUseFailure(Bash) hook: record bypass and test-run telemetry.

Pre: git commit/push --no-verify or commit -n, AICJ_HOOK_MODE= overrides, -DskipTests style flags.
Post: pytest / maven surefire / jest / vitest / gradle / go test summaries -> test_run.
Never blocks, never prints, always exits 0. Does nothing unless <claude>/aicj/team.json exists.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path


def _claude_dir() -> Path:
    env = os.environ.get("AICJ_CLAUDE_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


_SEGMENT_SPLIT = re.compile(r"&&|\|\||[;|\n]")
_VALUE_FLAGS = {"-m", "-F", "-C", "-c", "--message", "--file", "--author", "--date"}
_SKIP_TESTS = re.compile(r"-DskipTests(?!=false)\b|-Dmaven\.test\.skip(?!=false)\b", re.IGNORECASE)
_HOOK_MODE = re.compile(r"\bAICJ_HOOK_MODE=")
_GIT_OPTS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}


def _tokens(segment: str) -> list[str]:
    try:
        return shlex.split(segment)
    except ValueError:
        return segment.split()


def _git_bypass(segment: str) -> bool:
    toks = _tokens(segment)
    i = 0
    while i < len(toks) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", toks[i]):
        i += 1
    if i >= len(toks) or Path(toks[i]).name.lower() not in ("git", "git.exe"):
        return False
    i += 1
    while i < len(toks) and toks[i].startswith("-"):  # git global options
        i += 2 if toks[i] in _GIT_OPTS_WITH_VALUE else 1
    if i >= len(toks) or toks[i].strip().lower() not in ("commit", "push"):
        return False
    sub = toks[i].strip().lower()
    skip_next = False
    for tok in toks[i + 1:]:
        if skip_next:
            skip_next = False
            continue
        if tok == "--":
            break
        if tok == "--no-verify":
            return True
        if tok in _VALUE_FLAGS:
            skip_next = True
            continue
        # push -n is --dry-run; only commit -n means --no-verify
        if sub == "commit" and re.fullmatch(r"-[A-Za-z]+", tok) and "n" in tok[1:]:
            return True
    return False


def bypass_kinds(command: str) -> list[str]:
    kinds: list[str] = []
    if any(_git_bypass(seg) for seg in _SEGMENT_SPLIT.split(command)):
        kinds.append("no_verify")
    if _HOOK_MODE.search(command):
        kinds.append("hook_mode_override")
    if _SKIP_TESTS.search(command):
        kinds.append("skip_tests")
    return kinds


# ---------------------------------------------------------------- test summaries
_PYTEST_LINE = re.compile(
    r"^[= ]*((?:\d+ (?:passed|failed|skipped|errors?|xfailed|xpassed|deselected|warnings?)(?:, | and )?)+)"
    r"\s+in\s+[\d.]+s", re.MULTILINE)
_COUNT = re.compile(r"(\d+) (passed|failed|skipped|errors?|xfailed|xpassed)")
_MAVEN_LINE = re.compile(
    r"Tests run:\s*(\d+),\s*Failures:\s*(\d+),\s*Errors:\s*(\d+),\s*Skipped:\s*(\d+)\s*$", re.MULTILINE)
_JS_LINE = re.compile(r"^\s*Tests:?\s+(\d+ (?:failed|passed|skipped|todo).*)$", re.MULTILINE)
_JS_COUNT = re.compile(r"(\d+) (failed|passed|skipped|todo)")
_GRADLE_LINE = re.compile(r"(\d+) tests? completed(?:, (\d+) failed)?(?:, (\d+) skipped)?")
_GO_LINE = re.compile(r"^(--- (?:PASS|FAIL)|ok\s|FAIL\s)", re.MULTILINE)


def _row(framework: str, passed: int, failed: int, skipped: int, errors: int) -> dict:
    return {"framework": framework, "passed": passed, "failed": failed, "skipped": skipped,
            "errors": errors, "green": failed == 0 and errors == 0}


def _pytest(text: str):
    last = None
    for m in _PYTEST_LINE.finditer(text):
        last = m
    if not last:
        return None
    c = {"passed": 0, "failed": 0, "skipped": 0, "error": 0}
    for n, kind in _COUNT.findall(last.group(1)):
        key = "error" if kind.startswith("error") else kind
        if key in c:
            c[key] += int(n)
    return last.start(), _row("pytest", c["passed"], c["failed"], c["skipped"], c["error"])


def _maven(text: str):
    last = None
    for m in _MAVEN_LINE.finditer(text):
        last = m
    if not last:
        return None
    run, fail, err, skip = (int(g) for g in last.groups())
    return last.start(), _row("maven", max(run - fail - err - skip, 0), fail, skip, err)


def _js(text: str):
    last = None
    for m in _JS_LINE.finditer(text):
        last = m
    if not last:
        return None
    c = {"passed": 0, "failed": 0, "skipped": 0, "todo": 0}
    for n, kind in _JS_COUNT.findall(last.group(1)):
        c[kind] += int(n)
    return last.start(), _row("jest", c["passed"], c["failed"], c["skipped"], 0)


def _gradle(text: str):
    last = None
    for m in _GRADLE_LINE.finditer(text):
        last = m
    if not last:
        return None
    total, fail, skip = (int(g or 0) for g in last.groups())
    return last.start(), _row("gradle", max(total - fail - skip, 0), fail, skip, 0)


def _go(text: str):
    marks = list(_GO_LINE.finditer(text))
    if not marks:
        return None
    verbose = any(m.group(1).startswith("---") for m in marks)
    passed = failed = 0
    for m in marks:
        head = m.group(1)
        if verbose and not head.startswith("---"):
            continue
        if head.startswith("--- PASS") or head.startswith("ok"):
            passed += 1
        else:
            failed += 1
    return marks[-1].start(), _row("go", passed, failed, 0, 0)


def parse_test_summary(text: str) -> dict | None:
    found = [r for r in (p(text) for p in (_pytest, _maven, _js, _gradle, _go)) if r]
    if not found:
        return None
    return max(found, key=lambda r: r[0])[1]


def _response_text(payload: dict) -> str:
    parts: list[str] = []
    resp = payload.get("tool_response")
    if isinstance(resp, dict):
        for key in ("stdout", "stderr", "output", "error"):
            if isinstance(resp.get(key), str):
                parts.append(resp[key])
    elif isinstance(resp, str):
        parts.append(resp)
    if isinstance(payload.get("error"), str):
        parts.append(payload["error"])
    return "\n".join(parts)


def main() -> None:
    claude = _claude_dir()
    if not (claude / "aicj" / "team.json").is_file():
        return
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        return
    if not isinstance(payload, dict) or str(payload.get("tool_name") or "").strip() != "Bash":
        return
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    command = tool_input.get("command") if isinstance(tool_input.get("command"), str) else ""
    cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) and payload.get("cwd") else None
    event = str(payload.get("hook_event_name") or "").strip()

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import aicj_telemetry as tel

    if event == "PreToolUse":
        for kind in bypass_kinds(command):
            tel.emit(claude, "bypass", {"kind": kind}, cwd=cwd)
    elif event in ("PostToolUse", "PostToolUseFailure"):
        row = parse_test_summary(_response_text(payload))
        if row:
            tel.emit(claude, "test_run", row, cwd=cwd)


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        pass
    sys.exit(0)
