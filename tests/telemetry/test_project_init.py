from __future__ import annotations

import os
import shutil
import subprocess
import sys

from .conftest import HOOKS, REPO_ROOT, spool_events

INIT = REPO_ROOT / "scripts" / "init_target_project.py"


def _install_lib(claude):
    lib = claude / "hooks" / "aicj"
    lib.mkdir(parents=True)
    shutil.copy2(HOOKS / "aicj_telemetry.py", lib / "aicj_telemetry.py")


def _init(claude, target):
    env = dict(os.environ, AICJ_CLAUDE_DIR=str(claude))
    return subprocess.run([sys.executable, str(INIT), str(target), "--hooks", "skip", "--claude-entry", "skip"],
                          env=env, capture_output=True, text=True, timeout=120)


def test_project_init_emits_event_with_team(claude, team, tmp_path):
    _install_lib(claude)
    target = tmp_path / "proj"
    target.mkdir()
    r = _init(claude, target)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (target / ".ai-coding-java").is_dir()
    evs = spool_events(claude)
    assert [(e["type"], e["ok"]) for e in evs] == [("project_init", True)]


def test_project_init_without_team_writes_no_telemetry(claude, tmp_path):
    _install_lib(claude)
    target = tmp_path / "proj"
    target.mkdir()
    r = _init(claude, target)
    assert r.returncode == 0, r.stdout + r.stderr
    assert not (claude / "aicj").exists()


def test_project_init_without_installed_library_still_succeeds(claude, team, tmp_path):
    target = tmp_path / "proj"
    target.mkdir()
    assert _init(claude, target).returncode == 0
    assert spool_events(claude) == []


def test_project_init_failure_with_team_emits_ok_false(claude, team, tmp_path):
    _install_lib(claude)
    target = tmp_path / "not-a-dir"
    target.write_text("file", encoding="utf-8")
    r = _init(claude, target)
    assert r.returncode == 1
    evs = spool_events(claude)
    assert [(e["type"], e["ok"], e.get("error_kind")) for e in evs] == [("project_init", False, "TargetNotDirectory")]


def test_project_init_failure_without_team_writes_no_telemetry(claude, tmp_path):
    _install_lib(claude)
    target = tmp_path / "missing"
    assert _init(claude, target).returncode == 1
    assert not (claude / "aicj").exists()
