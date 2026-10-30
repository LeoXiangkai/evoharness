from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import aicj_telemetry as tel

from .conftest import REPO_ROOT as REPO, git, make_remote, remote_files, write_log

AICJ = REPO / "installer" / "aicj.py"
AGG = REPO / "scripts" / "telemetry_aggregate.py"
DAY = "2026-10-27"
FAMILIES = ("install", "hook", "task_days", "rework", "lesson", "skill", "ask_user")
WEEKLY = REPO / "scripts" / "team_weekly.py"
CAPTURE = "telemetry-capture.py"


def run_aicj(*args: str, home: Path, source: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ, AICJ_SKIP_HUD="1")
    env.pop("AICJ_REVIEW_LOG_DIR", None)
    return subprocess.run([sys.executable, str(AICJ), *args, "--home", str(home), "--source", str(source)],
                          capture_output=True, text=True, encoding="utf-8", cwd=str(REPO), env=env)


def _env(claude: Path) -> dict:
    env = dict(os.environ, AICJ_CLAUDE_DIR=str(claude))
    env.pop("AICJ_REVIEW_LOG_DIR", None)  # leaked by another test module at import time
    return env


def _install(home: Path, *extra: str):
    return run_aicj("install", "--packs", "none", *extra, home=home, source=REPO)


def _team_file(tmp_path: Path, remote: Path) -> Path:
    p = tmp_path / "team-in.json"
    p.write_text(json.dumps({"team_id": "team1", "collect_repo": str(remote), "salt": "salt-abc",
                             "branch": "main"}), encoding="utf-8")
    return p


def _hook(claude: Path, payload: dict) -> subprocess.CompletedProcess:
    script = claude / "hooks" / "aicj" / CAPTURE
    return subprocess.run([sys.executable, str(script)], input=json.dumps(payload), env=_env(claude),
                          capture_output=True, text=True, timeout=60)


def _settings_cmds(home: Path) -> str:
    p = home / ".claude" / "settings.json"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _agg(remote_clone: Path, fmt: str):
    r = subprocess.run([sys.executable, str(AGG), "--repo", str(remote_clone), "--format", fmt],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout) if fmt == "json" else r.stdout


def _build_task_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "proj"
    git(tmp_path, "init", "-q", "-b", "main", str(repo))
    cfg = ["-c", "user.name=t", "-c", "user.email=t" + "@x.test"]
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, *cfg, "commit", "-qm", "init")
    git(repo, "checkout", "-q", "-b", "feature/x")
    (repo / "b.txt").write_text("1\n", encoding="utf-8")
    (repo / "c.txt").write_text("1\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, *cfg, "commit", "-qm", "one")
    (repo / "b.txt").write_text("2\n", encoding="utf-8")
    (repo / "d.txt").write_text("1\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, *cfg, "commit", "-qm", "two")
    return repo


def _collect_clone(tmp_path: Path, remote: Path) -> Path:
    clone = tmp_path / "admin-clone"
    git(tmp_path, "clone", "-q", str(remote), str(clone))
    return clone


def test_team_flow_end_to_end(tmp_path):
    home = tmp_path / "h"
    home.mkdir()
    claude = home / ".claude"
    remote = make_remote(tmp_path)
    team_in = _team_file(tmp_path, remote)

    # install without team: zero telemetry artifacts, hooks not registered
    r = _install(home)
    assert r.returncode == 0, r.stdout + r.stderr
    assert not (claude / "aicj" / "team.json").exists()
    assert not (claude / "aicj" / "telemetry").exists()
    assert "telemetry-" not in _settings_cmds(home)

    # joining later on the existing install registers the hooks
    r = _install(home, "--team-config", str(team_in))
    assert r.returncode == 0, r.stdout + r.stderr
    assert (claude / "aicj" / "team.json").is_file()
    settings = _settings_cmds(home)
    assert "telemetry-capture.py" in settings and "telemetry-report.py" in settings
    assert "lesson-capture.py" in settings  # a team install adds the lesson adapter for rework causes

    # reinstall without the flag keeps the team
    r = _install(home)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (claude / "aicj" / "team.json").is_file()
    assert "telemetry-capture.py" in _settings_cmds(home)

    # invalid team config: exit 2, team.json untouched
    bad = _install(home, "--team-config", str(tmp_path / "missing.json"))
    assert bad.returncode == 2

    team = tel.load_team(claude)
    assert team is not None
    repo = _build_task_repo(tmp_path)
    assert tel.task_key(claude, str(repo), team)

    # hook gate rows: the real writer, then fixed-day rows for deterministic days
    gate_py = (
        "import sys; sys.path.insert(0, %r); import gate_log; "
        "gate_log.append_event('pre-bash','deny','[git] never skip hooks', "
        "'{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"x\"},\"cwd\":%s}')"
    ) % (str(claude / "hooks" / "aicj"), json.dumps(str(repo)))
    g = subprocess.run([sys.executable, "-c", gate_py], env=_env(claude), capture_output=True, text=True)
    assert g.returncode == 0, g.stderr
    write_log(claude / "aicj/review-logs/code-review-gates.jsonl", [
        {"ts": DAY + "T10:01:00+00:00", "kind": "gate", "repo_root": str(repo), "branch": "feature/x",
         "files": ["a.py", "b.py"], "lines": 40, "review_gate": "required", "risk_signals": ["new_api"]},
        {"ts": DAY + "T10:05:00+00:00", "kind": "outcome", "repo_root": str(repo), "branch": "feature/x",
         "reviewers": 1, "findings": 3, "confirmed": 2, "review_gate": "required"}])
    write_log(claude / "aicj/lessons/corrections.jsonl",
              [{"ts": DAY + "T10:02:00Z", "session_id": "s1", "cwd": str(repo), "prompt": "wrong, 漏了 other-callers"}])
    write_log(claude / "aicj/lessons/cards.jsonl", [
        {"id": "card-1", "scope": "global", "trigger": "t", "lesson": "text", "level": "warn",
         "status": "active", "created": "2026-10-01"}])
    write_log(claude / "aicj/lessons/recalls.jsonl",
              [{"ts": DAY + "T10:03:00Z", "card": "card-1", "session_id": "s1", "cwd": str(repo)}])

    # capture hook: bypass + failing then green test run
    base = {"tool_name": "Bash", "cwd": str(repo)}
    for payload in (
        {**base, "hook_event_name": "PreToolUse", "tool_input": {"command": "git commit --no-verify -m x"}},
        {**base, "hook_event_name": "PostToolUseFailure", "tool_input": {"command": "pytest"},
         "tool_response": {"stdout": "== 1 passed, 2 failed in 0.1s =="}},
        {**base, "hook_event_name": "PostToolUse", "tool_input": {"command": "pytest"},
         "tool_response": {"stdout": "== 3 passed in 0.1s =="}},
    ):
        h = _hook(claude, payload)
        assert h.returncode == 0 and h.stdout == ""
        time.sleep(1.1)  # ts has 1s resolution; keep the failing and green runs ordered
    sig = {"cwd": str(repo), "session_id": "s1"}
    for payload in (
        {**sig, "hook_event_name": "PostToolUse", "tool_name": "Skill", "tool_use_id": "k1",
         "tool_input": {"skill": "code-review"}},
        {**sig, "hook_event_name": "UserPromptSubmit", "prompt": "/private-team-skill go"},
        {**sig, "hook_event_name": "UserPromptSubmit", "prompt": "/clear"},
        {**sig, "hook_event_name": "PostToolUse", "tool_name": "AskUserQuestion", "tool_use_id": "q1",
         "tool_input": {"questions": [{"question": "which-private-option?"}]}},
    ):
        h = _hook(claude, payload)
        assert h.returncode == 0 and h.stdout == ""

    tool = claude / "bin" / "aicj-telemetry"
    rep = subprocess.run([sys.executable, str(tool), "report", "--force"], env=_env(claude),
                         capture_output=True, text=True, timeout=180)
    assert rep.returncode == 0, rep.stdout + rep.stderr
    files = remote_files(remote)
    assert files and all(f.count("/") == 1 for f in files)
    content = "".join(files.values())
    rows = [json.loads(x) for x in content.splitlines() if x.strip()]
    types = Counter(x["type"] for x in rows)
    # join + reinstall + the rejected config on an existing team (ok=false); the no-team install emits nothing
    assert types["install"] == 3
    installs = [x for x in rows if x["type"] == "install"]
    assert sorted(x["ok"] for x in installs) == [False, True, True]
    assert [x["error_kind"] for x in installs if not x["ok"]] == ["UserError"]
    exact = {k: v for k, v in types.items() if k != "install"}
    assert exact == {"hook_gate": 1, "bypass": 1, "test_run": 2, "review": 2, "task_snapshot": 1,
                     "correction": 1, "lesson_card": 1, "lesson_recall": 1, "skill_use": 2, "ask_user": 1}
    assert sorted(x["green"] for x in rows if x["type"] == "test_run") == [False, True]
    snap = next(x for x in rows if x["type"] == "task_snapshot")
    assert (snap["commits"], snap["files_changed"], snap["files_touched_repeatedly"]) == (2, 3, 1)
    blob = content
    assert [(x["cause"], x["cause_rules"]) for x in rows if x["type"] == "correction"] == [("B", "r2")]
    assert sorted((x["skill"], x["via"]) for x in rows if x["type"] == "skill_use") == [
        ("code-review", "tool"), ("private-team-skill", "slash")]
    assert [x["questions"] for x in rows if x["type"] == "ask_user"] == [1]
    for leak in (str(repo), "never skip hooks", "no-verify", "wrong", "漏了", "other-callers", "salt-abc",
                 "which-private-option"):
        assert leak not in blob
    user_hash = rows[0]["user"]
    assert {x["user"] for x in rows} == {user_hash}
    assert all(f.split("/")[1].startswith(user_hash + "-") for f in files)

    # aggregate: by day and by user hash
    clone = _collect_clone(tmp_path, remote)
    data = _agg(clone, "json")
    n_install = types["install"]
    totals = {"install": n_install, "hook": 2, "task_days": 1, "rework": 4, "lesson": 3, "skill": 2, "ask_user": 1}
    assert data["summary"]["events"] == sum(types.values())
    for fam in FAMILIES:
        assert sum(d["families"][fam] for d in data["by_day"].values()) == totals[fam], fam
        assert sum(u["families"][fam] for u in data["by_user"].values()) == totals[fam], fam
    assert DAY in data["by_day"]
    assert data["by_day"][DAY]["families"]["rework"] == 2
    assert data["by_day"][DAY]["families"]["lesson"] == 2
    assert list(data["by_user"]) == [user_hash]
    assert [(t["test_runs"], t["first_green_index"]) for t in data["by_task"].values()] == [(2, 2)]
    assert [(c["recalls"], c["corrections_after_recall"]) for c in data["by_lesson_card"].values()] == [(1, 0)]
    detail = data["by_user"][user_hash]["detail"]
    assert {k: v for k, v in detail.items() if k.startswith("gate_mode_")} == {"gate_mode_warn": 1}
    assert (detail["tests_passed"], detail["tests_failed"]) == (4, 2)
    md = _agg(clone, "md")
    assert "## By day" in md and "## By user hash" in md and "## By task" in md and "## By lesson card" in md and DAY in md and user_hash in md
    assert str(repo) not in md and "event_id" not in md

    assert data["rework_cause"]["r2"]["counts"] == {"B": 1}
    assert data["skill"]["code-review"]["calls"] == 1 and data["ask_user"]["questions"] == 1
    wk = subprocess.run([sys.executable, str(WEEKLY), "--repo", str(clone), "--week", "2026-W44", "--format", "json",
                         "--min-n", "1"], capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert wk.returncode == 0, wk.stderr
    weekly = json.loads(wk.stdout)
    assert weekly["metrics"]["rework_share:B"]["value"] == 1.0
    stages = {r["stage"]: " ".join(r["cells"]) for r in weekly["stages"]}
    assert "rework_share:A=" in stages["需求"] and "rework_share:E=" in stages["验收"]

    # second report is idempotent
    rep2 = subprocess.run([sys.executable, str(tool), "report", "--force"], env=_env(claude),
                          capture_output=True, text=True, timeout=180)
    assert rep2.returncode == 0, rep2.stdout + rep2.stderr
    git(clone, "pull", "-q")
    assert _agg(clone, "json")["summary"]["events"] == data["summary"]["events"]

    # doctor: telemetry rows normal
    d = run_aicj("doctor", home=home, source=REPO)
    assert "team-telemetry" in d.stdout, d.stdout
    for line in d.stdout.splitlines():
        if "team-telemetry" in line or "telemetry-identity" in line:
            assert "FAIL" not in line, line

    # uninstall: hooks, team.json, collect-repo clone and lock gone; spool/state/identity kept
    (claude / "aicj" / "telemetry" / "collect-repo" / ".git").mkdir(parents=True, exist_ok=True)
    (claude / "aicj" / "telemetry" / "report.lock").write_text("x", encoding="utf-8")
    u = run_aicj("uninstall", home=home, source=REPO)
    assert u.returncode == 0, u.stdout + u.stderr
    assert "telemetry-" not in _settings_cmds(home)
    assert not (claude / "aicj" / "team.json").exists()
    assert (claude / "aicj" / "telemetry").is_dir()
    assert not (claude / "aicj" / "telemetry" / "collect-repo").exists()
    assert not (claude / "aicj" / "telemetry" / "report.lock").exists()
    assert (claude / "aicj" / "telemetry" / "identity.json").is_file()


def test_unreachable_collect_repo_never_breaks_hooks_or_git(tmp_path):
    home = tmp_path / "h"
    home.mkdir()
    claude = home / ".claude"
    team_in = tmp_path / "team-in.json"
    team_in.write_text(json.dumps({"team_id": "team1", "collect_repo": str(tmp_path / "nowhere.git"),
                                   "salt": "salt-abc", "branch": "main"}), encoding="utf-8")
    r = _install(home, "--team-config", str(team_in))
    assert r.returncode == 0, r.stdout + r.stderr
    repo = _build_task_repo(tmp_path)
    h = _hook(claude, {"tool_name": "Bash", "cwd": str(repo), "hook_event_name": "PreToolUse",
                       "tool_input": {"command": "git commit -n -m x"}})
    assert h.returncode == 0 and h.stdout == ""
    rep = subprocess.run([sys.executable, str(claude / "hooks" / "aicj" / "telemetry-report.py")],
                         input="{}", env=_env(claude), capture_output=True, text=True, timeout=60)
    assert rep.returncode == 0 and rep.stdout == ""
    # the SessionStart hook's detached child may still hold the report lock
    forced = None
    for _ in range(60):
        forced = subprocess.run([sys.executable, str(claude / "bin" / "aicj-telemetry"), "report", "--force"],
                                env=_env(claude), capture_output=True, text=True, timeout=180)
        if '"locked"' not in forced.stdout:
            break
        time.sleep(0.5)
    assert "failed" in forced.stdout and forced.returncode == 1, forced.stdout
    (repo / "e.txt").write_text("x\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t" + "@x.test", "commit", "-qm", "still works")
