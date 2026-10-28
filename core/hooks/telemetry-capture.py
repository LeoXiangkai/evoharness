#!/usr/bin/env python3
"""PreToolUse/PostToolUse/PostToolUseFailure(Bash) hook: record bypass and test-run telemetry.
Also: Skill / AskUserQuestion tool calls (skill_use, ask_user) and UserPromptSubmit leading slash commands (skill_use).

Pre: git commit/push --no-verify or commit -n, AICJ_HOOK_MODE= overrides, -DskipTests style flags.
Post: pytest / maven surefire / jest / vitest / gradle / go test summaries -> test_run.
Never blocks, never prints, always exits 0. Does nothing unless <claude>/aicj/team.json exists.
"""
from __future__ import annotations

import hashlib
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
_SKIP_TESTS = re.compile(r"-D(?:skipTests|maven\.test\.skip(?:\.exec)?)(?:=(?!false$).*)?$", re.IGNORECASE)
_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_GIT_OPTS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}
_MAVEN_TEST_GOALS = ("test", "verify", "package", "install", "deploy")
_GRADLE_TEST_TASKS = ("test", "check", "build")
_BUILD_TOOLS = {"mvn", "mvnw", "gradle", "gradlew"}


def _tokens(segment: str) -> list[str]:
    try:
        return shlex.split(segment)
    except ValueError:
        return segment.split()


def _leading(toks: list[str]) -> tuple[list[str], int]:
    """Leading `env`/`export` and VAR=val words; returns (assignments, index of the command)."""
    i = 0
    is_env = bool(toks) and toks[0] == "env"
    if toks and toks[0] in ("env", "export"):
        i = 1
    assigns: list[str] = []
    while i < len(toks):
        if _ASSIGN.match(toks[i]):
            assigns.append(toks[i])
        elif not (is_env and toks[i].startswith("-")):
            break
        i += 1
    return assigns, i


def _command_of(segment: str) -> tuple[list[str], list[str]]:
    toks = _tokens(segment)
    assigns, i = _leading(toks)
    return assigns, toks[i:]


def _git_bypass(segment: str) -> bool:
    _, toks = _command_of(segment)
    if not toks or Path(toks[0]).name.lower() not in ("git", "git.exe"):
        return False
    i = 1
    while i < len(toks) and toks[i].startswith("-"):  # git global options
        i += 2 if toks[i] in _GIT_OPTS_WITH_VALUE else 1
    if i >= len(toks) or toks[i].strip().lower() not in ("commit", "push", "merge"):
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
        if sub == "commit" and _short_cluster_has_n(tok):  # push -n is --dry-run
            return True
    return False


# commit short options whose value may be attached (-uno, -mmsg): the rest of the cluster is that value
_COMMIT_ARG_LETTERS = set("mFcCuSt")


def _short_cluster_has_n(tok: str) -> bool:
    if not tok.startswith("-") or tok.startswith("--") or len(tok) < 2:
        return False
    for ch in tok[1:]:
        if ch == "n":
            return True
        if ch in _COMMIT_ARG_LETTERS:
            return False
    return False


def _build_tool(cmd: list[str]) -> bool:
    return bool(cmd) and _bare_name(cmd) in _BUILD_TOOLS


def bypass_kinds(command: str) -> list[str]:
    segs = [_command_of(seg) for seg in _SEGMENT_SPLIT.split(command)]
    kinds: list[str] = []
    if any(_git_bypass(seg) for seg in _SEGMENT_SPLIT.split(command)):
        kinds.append("no_verify")
    if any(a.startswith("AICJ_HOOK_MODE=") for assigns, _ in segs for a in assigns):
        kinds.append("hook_mode_override")
    if any(_build_tool(cmd) and any(_SKIP_TESTS.search(t) for t in cmd[1:]) for _, cmd in segs):
        kinds.append("skip_tests")
    return kinds


def _bare_name(cmd: list[str]) -> str:
    name = Path(cmd[0].replace("\\", "/")).name.lower()
    for ext in (".exe", ".cmd", ".bat"):
        if name.endswith(ext):
            return name[:-len(ext)]
    return name


def _is_test_segment(cmd: list[str]) -> bool:
    if not cmd:
        return False
    name = _bare_name(cmd)
    rest = cmd[1:]
    if name in ("pytest", "py.test", "jest", "vitest"):
        return True
    if name == "py":  # Windows launcher: py [-3] -m pytest
        while rest and re.fullmatch(r"-\d+(\.\d+)?(-\d+)?", rest[0]):
            rest = rest[1:]
        return rest[:2] == ["-m", "pytest"]
    if name.startswith("python"):
        return rest[:2] == ["-m", "pytest"]
    if name in ("mvn", "mvnw"):
        return any(t in _MAVEN_TEST_GOALS for t in rest)
    if name in ("gradle", "gradlew"):
        return any(t in _GRADLE_TEST_TASKS for t in rest)
    if name in ("npm", "pnpm", "yarn"):
        return rest[:1] == ["test"] or rest[:2] == ["run", "test"]
    if name == "go":
        return rest[:1] == ["test"]
    return False


def _is_test_task(name: str) -> bool:
    """Gradle task `test`, optionally project-qualified (`:test`, `:app:test`)."""
    return name.strip().lower().rsplit(":", 1)[-1] == "test"


def _gradle_excludes_test(rest: list[str]) -> bool:
    for i, t in enumerate(rest):
        low = t.lower()
        if low in ("-x", "--exclude-task"):
            if i + 1 < len(rest) and _is_test_task(rest[i + 1]):
                return True
        elif low.startswith("--exclude-task="):
            if _is_test_task(low[len("--exclude-task="):]):
                return True
        elif low.startswith("-x") and _is_test_task(low[2:]):
            return True
    return False


def _segment_runs_tests(cmd: list[str]) -> bool:
    """True when a build-tool segment really runs tests (goals present and not skipped)."""
    if not cmd:
        return False
    name, rest = _bare_name(cmd), cmd[1:]
    if name in ("mvn", "mvnw"):
        return any(t in _MAVEN_TEST_GOALS for t in rest) and not any(_SKIP_TESTS.search(t) for t in rest)
    if name in ("gradle", "gradlew"):
        return any(t in _GRADLE_TEST_TASKS for t in rest) and not _gradle_excludes_test(rest)
    return _is_test_segment(cmd)


def fallback_runs_tests(command: str) -> bool:
    return any(_is_test_segment(c) and _segment_runs_tests(c)
               for c in (_command_of(seg)[1] for seg in _SEGMENT_SPLIT.split(command)))


def is_test_command(command: str) -> bool:
    return any(_is_test_segment(_command_of(seg)[1]) for seg in _SEGMENT_SPLIT.split(command))


def test_framework(command: str) -> str:
    """Framework name of the first test segment in the command ("" if none)."""
    for seg in _SEGMENT_SPLIT.split(command):
        cmd = _command_of(seg)[1]
        if not (_is_test_segment(cmd) and _segment_runs_tests(cmd)):
            continue
        name = _bare_name(cmd)
        if name.startswith(("pytest", "py")) or name.startswith("python"):
            return "pytest"
        if name in ("mvn", "mvnw"):
            return "maven"
        if name in ("gradle", "gradlew"):
            return "gradle"
        if name == "go":
            return "go"
        return "jest"  # jest / vitest / npm|pnpm|yarn test
    return ""


_BUILD_FAILED = re.compile(r"BUILD (?:FAILED|FAILURE)|FAILURE: Build failed")


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
_GO_LINE = re.compile(r"^(--- (?:PASS|FAIL)|ok\s|FAIL\b)", re.MULTILINE)


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


_MAVEN_RESULTS = re.compile(r"^\[\w+\]\s*Results:\s*$", re.MULTILINE)


def _maven(text: str):
    heads = list(_MAVEN_RESULTS.finditer(text))
    picked = []
    for i, h in enumerate(heads):  # one summary per module, right after its Results header
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        m = _MAVEN_LINE.search(text, h.end(), end)
        if m:
            picked.append(m)
    if not picked:
        last = None
        for m in _MAVEN_LINE.finditer(text):
            last = m
        if not last:
            return None
        picked = [last]
    run = fail = err = skip = 0
    for m in picked:
        r, f, e, k = (int(g) for g in m.groups())
        run, fail, err, skip = run + r, fail + f, err + e, skip + k
    last = picked[-1]
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
    passed = failed = bare_fail = 0
    for m in marks:
        head = m.group(1)
        if head.startswith("---"):
            if head.startswith("--- PASS"):
                passed += 1
            else:
                failed += 1
        elif verbose:
            continue
        elif head.startswith("ok"):
            passed += 1
        else:
            end = text.find("\n", m.start())
            if text[m.start():end if end >= 0 else len(text)].strip() == "FAIL":
                bare_fail += 1
            else:
                failed += 1
    if not verbose and failed == 0 and bare_fail:
        failed = 1  # bare FAIL without a package line
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


_SLASH_RE = re.compile(r"^/([A-Za-z0-9][A-Za-z0-9._:-]{0,63})(?:\s|$)")


def _shared_event_id(kind: str, payload: dict) -> str | None:
    sid, tuid = payload.get("session_id"), payload.get("tool_use_id")
    if isinstance(sid, str) and sid and isinstance(tuid, str) and tuid:
        return hashlib.sha256(f"{kind}\n{sid}\n{tuid}".encode("utf-8")).hexdigest()[:32]
    return None


def slash_command(prompt: object) -> str | None:
    """Leading slash command name of a prompt, None for plain text, paths and built-in commands."""
    if not isinstance(prompt, str):
        return None
    m = _SLASH_RE.match(prompt.lstrip())
    if not m:
        return None
    name = m.group(1)
    if name.lower() in _builtin_slash():
        return None
    return name


def _builtin_slash() -> frozenset:
    import aicj_telemetry as tel
    return tel.BUILTIN_SLASH


def _other_signals(claude: Path, payload: dict, tool: str) -> None:
    event = str(payload.get("hook_event_name") or "").strip()
    if event == "UserPromptSubmit" and not _SLASH_RE.match(str(payload.get("prompt") or "").lstrip()):
        return  # plain prompts: prefix check only, no telemetry import
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import aicj_telemetry as tel

    cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) and payload.get("cwd") else None
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    if event == "UserPromptSubmit":
        name = slash_command(payload.get("prompt"))
        if name:
            tel.emit(claude, "skill_use", {"skill": name, "via": "slash"}, cwd=cwd)
            tel.record_skill(claude, payload.get("session_id"), name)
    elif event in ("PostToolUse", "PostToolUseFailure"):
        if tool == "Skill":
            raw = tool_input.get("skill")
            name = raw.strip() if isinstance(raw, str) else ""
            if name:
                tel.emit(claude, "skill_use", {"skill": name, "via": "tool"}, cwd=cwd,
                         event_id=_shared_event_id("skill_use", payload))
                tel.record_skill(claude, payload.get("session_id"), name)
        elif tool == "AskUserQuestion":
            qs = tool_input.get("questions")
            count = len(qs) if isinstance(qs, list) else 0
            if count > 0:
                tel.emit(claude, "ask_user", {"questions": min(count, 4)}, cwd=cwd,
                         event_id=_shared_event_id("ask_user", payload))


def main() -> None:
    claude = _claude_dir()
    if not (claude / "aicj" / "team.json").is_file():
        return
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        return
    if not isinstance(payload, dict):
        return
    tool = str(payload.get("tool_name") or "").strip()
    if tool != "Bash":
        try:  # new signal paths must never disturb the Bash path or the hook's exit code
            _other_signals(claude, payload, tool)
        except Exception:
            pass
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
        row = None
        if is_test_command(command):
            text = _response_text(payload)
            row = parse_test_summary(text)
            if not row and fallback_runs_tests(command):  # quiet runs (gradle success, mvn -q): only framework + green are known
                row = {"framework": test_framework(command) or "unknown",
                       "green": event == "PostToolUse" and not _BUILD_FAILED.search(text)}
        if row:
            sid, tuid = payload.get("session_id"), payload.get("tool_use_id")
            event_id = None
            if isinstance(sid, str) and sid and isinstance(tuid, str) and tuid:
                event_id = hashlib.sha256(f"test_run\n{sid}\n{tuid}".encode("utf-8")).hexdigest()[:32]
            tel.emit(claude, "test_run", row, cwd=cwd, event_id=event_id)


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        pass
    sys.exit(0)
