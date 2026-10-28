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
    env = dict(os.environ, AICJ_CLAUDE_DIR=str(claude), HOME=str(claude),
               GIT_CONFIG_GLOBAL=str(claude / "gitconfig"))
    return subprocess.run([sys.executable, str(HOOK)], input=raw if raw is not None else json.dumps(payload),
                          env=env, capture_output=True, text=True, timeout=60)


def pre(command: str) -> dict:
    return {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command}}


def post(stdout: str, event: str = "PostToolUse", command: str = "pytest -q") -> dict:
    return {"hook_event_name": event, "tool_name": "Bash", "tool_input": {"command": command},
            "tool_response": {"stdout": stdout}}


@pytest.mark.parametrize("command,kinds", [
    ("git commit --no-verify -m x", ["no_verify"]),
    ("git commit -n -m x", ["no_verify"]),
    ("git commit -an -m x", ["no_verify"]),
    ("git commit -mn", []),
    ("git commit -uno -m x", []),
    ("git commit -m \"mention --no-verify\"", []),
    ("git merge --no-verify topic", ["no_verify"]),
    ("grep -r AICJ_HOOK_MODE= .", []),
    ("export AICJ_HOOK_MODE=warn", ["hook_mode_override"]),
    ("env FOO=1 AICJ_HOOK_MODE=warn git push", ["hook_mode_override"]),
    ("echo -DskipTests", []),
    ("mvn -DskipTests package", ["skip_tests"]),
    ("./mvnw -Dmaven.test.skip=true install", ["skip_tests"]),
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


@pytest.mark.parametrize("text,expect", [
    ("FAIL\tpkg/b\t0.1s\nFAIL\n", ("go", 0, 1)),
    ("ok  \tpkg/a\t0.1s\nFAIL\tpkg/b\t0.1s\nFAIL\n", ("go", 1, 1)),
    ("FAIL\n", ("go", 0, 1)),
    ("ok  \tpkg/a\t0.1s\n", ("go", 1, 0)),
])
def test_go_counts_each_package_once(text, expect):
    row = cap.parse_test_summary(text)
    assert (row["framework"], row["passed"], row["failed"]) == expect


@pytest.mark.parametrize("command,expected", [
    ("cat build.log", False), ("echo ok", False), ("pytest -q", True),
    ("python3 -m pytest tests", True), ("cd x && ./mvnw -q verify", True), ("mvn clean", False),
    ("CI=1 gradlew check", True), ("npm run test", True), ("yarn test", True), ("go test ./... | tail", True),
    ("vitest run", True), ("git status; npm install", False),
])
def test_is_test_command(command, expected):
    assert cap.is_test_command(command) is expected


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


def test_non_test_command_with_summary_text_emits_nothing(claude, team):
    out = "Tests run: 10, Failures: 0, Errors: 0, Skipped: 0"
    run_hook(claude, post(out, command="cat build.log"))
    run_hook(claude, post("ok done", command='echo "ok done"'))
    assert spool_events(claude) == []


def test_real_pytest_command_emits_one_test_run(claude, team):
    r = run_hook(claude, post("3 passed, 1 failed in 0.2s", command="pytest -q"))
    assert r.returncode == 0 and r.stdout == ""
    runs = [e for e in spool_events(claude) if e["type"] == "test_run"]
    assert len(runs) == 1 and runs[0]["passed"] == 3 and runs[0]["failed"] == 1


def test_same_tool_call_shares_event_id_across_post_events(claude, team):
    base = {**post("== 2 passed, 1 failed in 0.1s =="), "session_id": "s1", "tool_use_id": "t1"}
    run_hook(claude, base)
    run_hook(claude, {**base, "hook_event_name": "PostToolUseFailure", "error": "exit 1"})
    runs = [e for e in spool_events(claude) if e["type"] == "test_run"]
    assert len(runs) == 2
    assert len({e["event_id"] for e in runs}) == 1
