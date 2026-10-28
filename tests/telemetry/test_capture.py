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


MVN_REACTOR = """\
[INFO] Results:
[INFO]
[INFO] Tests run: 10, Failures: 1, Errors: 0, Skipped: 2
[INFO]
[INFO] --- surefire:3.0 (default-test) @ b ---
[ERROR] Results:
[ERROR]
[ERROR] Tests run: 5, Failures: 0, Errors: 1, Skipped: 0
"""


def test_maven_sums_reactor_modules():
    pos, row = cap._maven(MVN_REACTOR)
    # run 15, failed 1, errors 1, skipped 2 -> passed 11
    assert (row["passed"], row["failed"], row["skipped"], row["errors"]) == (11, 1, 2, 1)


def test_maven_without_results_header_uses_last_summary():
    pos, row = cap._maven("Tests run: 3, Failures: 0, Errors: 0, Skipped: 0\nTests run: 4, Failures: 1, Errors: 0, Skipped: 0\n")
    assert row["failed"] == 1


@pytest.mark.parametrize("cmd", ["mvn.cmd test", "mvnw.cmd verify", "MVN.CMD test", "gradle.bat build",
                                 "gradlew.bat test", "py -m pytest", "py -3 -m pytest -q", "PYTHON.EXE -m pytest"])
def test_windows_test_command_names(cmd):
    assert cap.is_test_command(cmd)


@pytest.mark.parametrize("command,stdout,event,framework,green", [
    ("./gradlew test", "BUILD SUCCESSFUL in 3s", "PostToolUse", "gradle", True),
    ("./gradlew test", "BUILD FAILED in 3s", "PostToolUse", "gradle", False),
    ("./gradlew test", "", "PostToolUseFailure", "gradle", False),
    ("mvn -q test", "", "PostToolUse", "maven", True),
])
def test_quiet_test_run_emits_event_without_counts(claude, team, command, stdout, event, framework, green):
    r = run_hook(claude, post(stdout, event, command))
    assert r.returncode == 0 and r.stdout == ""
    runs = [e for e in spool_events(claude) if e["type"] == "test_run"]
    assert len(runs) == 1
    assert runs[0]["framework"] == framework and runs[0]["green"] is green
    assert not {"passed", "failed", "skipped", "errors"} & set(runs[0])


def test_quiet_run_shares_event_id_and_aggregates_as_zero_counts(claude, team):
    base = {**post("", command="mvn -q test"), "session_id": "s1", "tool_use_id": "t1"}
    run_hook(claude, base)
    run_hook(claude, {**base, "hook_event_name": "PostToolUseFailure", "error": "exit 1"})
    runs = [e for e in spool_events(claude) if e["type"] == "test_run"]
    assert len({e["event_id"] for e in runs}) == 1
    import importlib.util as u
    spec = u.spec_from_file_location("agg", HOOK.parents[2] / "scripts" / "telemetry_aggregate.py")
    agg = u.module_from_spec(spec)
    spec.loader.exec_module(agg)
    d = agg.detail(runs[:1])
    assert d["tests_passed"] == 0 and d["test_runs"] == 1


@pytest.mark.parametrize("command", [
    "mvn package -DskipTests", "mvn -q install -Dmaven.test.skip=true", "gradle build -x test",
    "./gradlew build --exclude-task test", "mvnw.cmd -q package -DskipTests", "gradle build -xtest",
    "mvn -q clean", "gradle assemble",
])
def test_quiet_build_that_skips_tests_emits_no_test_run(claude, team, command):
    r = run_hook(claude, post("", "PostToolUse", command))
    assert r.returncode == 0
    assert [e for e in spool_events(claude) if e["type"] == "test_run"] == []


@pytest.mark.parametrize("command,framework", [
    ("mvn -q test", "maven"), ("mvn -q deploy", "maven"), ("gradle build -x javadoc", "gradle")])
def test_quiet_build_that_runs_tests_emits_green_test_run(claude, team, command, framework):
    run_hook(claude, post("", "PostToolUse", command))
    runs = [e for e in spool_events(claude) if e["type"] == "test_run"]
    assert len(runs) == 1 and runs[0]["framework"] == framework and runs[0]["green"] is True


@pytest.mark.parametrize("command,framework", [
    ("gradlew.bat build", "gradle"), ("mvnw.cmd -q test", "maven"), ("go.exe test ./...", "go"),
    ("GRADLEW.BAT build", "gradle")])
def test_framework_strips_windows_extensions(command, framework):
    assert cap.test_framework(command) == framework


def test_build_failed_matches_gradle_failure_banner():
    assert cap._BUILD_FAILED.search("FAILURE: Build failed with an exception.")


def test_mvnw_cmd_skip_tests_is_bypass():
    assert cap.bypass_kinds("mvnw.cmd package -DskipTests") == ["skip_tests"]


@pytest.mark.parametrize("command,framework", [
    ("mvn -q test -DskipTests && pytest -q", "pytest"),
    ("./gradlew -q build -x test && npm test", "jest"),
    ("mvn -q test -Dmaven.test.skip.exec=true; go test ./...", "go"),
    ("mvn -q test -DskipTests", ""),
    ("gradle build -x :app:test", ""),
])
def test_framework_ignores_segments_that_skip_tests(command, framework):
    assert cap.test_framework(command) == framework


@pytest.mark.parametrize("command", [
    "gradle build -x :app:test", "./gradlew build -x :test", "gradle build --exclude-task=:test",
    "gradle build --exclude-task :mod:test", "gradle build -x test", "gradle build -xtest",
    "gradle build --exclude-task=test",
])
def test_gradle_qualified_test_exclusion_is_not_a_test_run(command):
    assert cap.fallback_runs_tests(command) is False


@pytest.mark.parametrize("command", [
    "gradle build -x lint", "gradle build -x :app:lint", "gradle build --exclude-task=:app:javadoc",
])
def test_gradle_other_exclusions_still_run_tests(command):
    assert cap.fallback_runs_tests(command) is True


@pytest.mark.parametrize("command,skips", [
    ("mvn test -Dmaven.test.skip.exec=false", False), ("mvn test -DskipTests=false", False),
    ("mvn test -Dmaven.test.skip=false", False), ("mvn test -DskipTests=FALSE", False),
    ("mvn test -Dmaven.test.skip.exec", True), ("mvn test -Dmaven.test.skip.exec=true", True),
    ("mvn test -DskipTests", True), ("mvn test -DskipTests=true", True),
    ("mvn test -Dmaven.test.skip=true", True), ("mvn test -Dmaven.test.skip", True),
])
def test_maven_skip_flag_values(command, skips):
    assert cap.fallback_runs_tests(command) is (not skips)
    assert (cap.bypass_kinds(command) == ["skip_tests"]) is skips
