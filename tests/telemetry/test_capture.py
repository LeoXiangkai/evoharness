from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from .conftest import HOOKS, spool_events

HOOK = HOOKS / "telemetry-capture.py"


def _load():
    spec = importlib.util.spec_from_file_location("telemetry_capture", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cap = _load()


def run_hook(claude: Path, payload, raw: str | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ, AICJ_CLAUDE_DIR=str(claude))
    return subprocess.run([sys.executable, str(HOOK)], input=raw if raw is not None else json.dumps(payload),
                          env=env, capture_output=True, text=True, timeout=60)


def pre(command: str) -> dict:
    return {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command}}


def post(stdout: str, event: str = "PostToolUse") -> dict:
    return {"hook_event_name": event, "tool_name": "Bash", "tool_input": {"command": "x"},
            "tool_response": {"stdout": stdout}}


@pytest.mark.parametrize("command,kinds", [
    ("git commit --no-verify -m x", ["no_verify"]),
    ("git commit -an -m x", ["no_verify"]),
    ("git -C /tmp/r commit -n", ["no_verify"]),
    ("git push --no-verify origin main", ["no_verify"]),
    ("git push -n origin main", []),
    ("git commit -m 'use --no-verify later'", []),
    ("git status && git commit --no-verify", ["no_verify"]),
    ("AICJ_HOOK_MODE=warn git push", ["hook_mode_override"]),
    ("mvn test -DskipTests", ["skip_tests"]),
    ("mvn test -DskipTests=false", []),
    ("echo hi", []),
])
def test_bypass_detection(command, kinds):
    assert cap.bypass_kinds(command) == kinds


@pytest.mark.parametrize("text,expect", [
    ("===== 3 passed, 1 failed, 2 skipped in 0.5s =====", ("pytest", 3, 1, 2, 0, False)),
    ("== 5 passed in 1.20s ==", ("pytest", 5, 0, 0, 0, True)),
    ("Tests run: 10, Failures: 1, Errors: 0, Skipped: 2", ("maven", 7, 1, 2, 0, False)),
    ("Tests:       1 failed, 4 passed, 5 total", ("jest", 4, 1, 0, 0, False)),
    ("ok  \tpkg/a\t0.1s\nFAIL\tpkg/b\t0.2s", ("go", 1, 1, 0, 0, False)),
])
def test_summary_parsing(text, expect):
    row = cap.parse_test_summary(text)
    assert (row["framework"], row["passed"], row["failed"], row["skipped"], row["errors"], row["green"]) == expect


def test_summary_none_for_plain_output():
    assert cap.parse_test_summary("hello world") is None


def test_no_team_creates_nothing(claude):
    r = run_hook(claude, pre("git commit --no-verify"))
    assert r.returncode == 0 and r.stdout == ""
    assert not (claude / "aicj").exists()


def test_hook_emits_bypass_and_test_run(claude, team):
    for payload in (pre("git commit --no-verify -m x"), pre("mvn -DskipTests package"),
                    post("== 2 passed, 1 failed in 0.1s ==", "PostToolUseFailure"),
                    post("== 4 passed in 0.1s ==")):
        r = run_hook(claude, payload)
        assert r.returncode == 0 and r.stdout == "" and r.stderr == ""
    evs = spool_events(claude)
    assert sorted(e["kind"] for e in evs if e["type"] == "bypass") == ["no_verify", "skip_tests"]
    runs = [e for e in evs if e["type"] == "test_run"]
    assert [e["green"] for e in runs] == [False, True]
    assert "git commit" not in json.dumps(evs)


def test_pre_event_with_test_output_is_ignored_and_non_bash_ignored(claude, team):
    run_hook(claude, {**post("== 2 passed in 0.1s =="), "hook_event_name": "PreToolUse"})
    run_hook(claude, {**pre("git commit --no-verify"), "tool_name": "Edit"})
    assert spool_events(claude) == []


@pytest.mark.parametrize("raw", ["", "not json", "[]", "null", '{"tool_name": 5}'])
def test_garbage_stdin_exits_zero(claude, team, raw):
    r = run_hook(claude, None, raw=raw)
    assert r.returncode == 0 and r.stdout == ""
    assert spool_events(claude) == []
