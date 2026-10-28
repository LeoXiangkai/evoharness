from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time

import pytest

import aicj_telemetry as tel
from .conftest import HOOKS, spool_events, write_log, write_team

LOGS = "aicj/review-logs"
LES = "aicj/lessons"
posix_only = pytest.mark.skipif(os.name == "nt", reason="uses POSIX shell stubs and process groups")


def _setup(claude):
    write_team(claude, "/nonexistent")
    return tel.load_team(claude)


def test_poison_rows_do_not_abort_collect_or_block_offsets(claude):
    _setup(claude)
    rows = [
        {"ts": "9999-12-31T23:30:00-05:00", "hook": "h", "event": "deny"},
        {"ts": "2026-10-01T00:00:00Z", "hook": "h", "event": "deny", "cwd": "/tmp/x\x00y"},
        {"ts": "2026-10-01T00:00:01Z", "hook": "h", "event": "deny"},
    ]
    write_log(claude / LOGS / "gate-events.jsonl", rows)
    write_log(claude / LOGS / "code-review-gates.jsonl", [
        {"ts": "2026-10-01T00:00:00Z", "kind": "gate", "branch": "b", "common_dir": "/a\x00b", "files": ["a"],
         "lines": 1, "review_gate": "skip"},
        {"ts": "2026-10-01T00:00:02Z", "kind": "gate", "branch": "b", "common_dir": "/ok/.git", "files": ["a"],
         "lines": 1, "review_gate": "skip"},
    ])
    first = tel.collect(claude)
    assert first >= 2
    assert tel._load_state(claude)["offsets"]["gate-events"] > 0
    assert tel._load_state(claude)["offsets"]["code-review-gates"] > 0
    assert any(e["ts"] == "2026-10-01T00:00:01Z" for e in spool_events(claude))
    assert tel.collect(claude) == 0


def test_norm_ts_never_raises():
    assert tel._norm_ts("9999-12-31T23:30:00-05:00") is None
    assert tel._norm_ts("2026-10-01T00:00:00Z") == "2026-10-01T00:00:00Z"


def test_norm_path_survives_nul():
    assert isinstance(tel._norm_path("/a\x00b"), str)


def test_cards_not_respooled_when_user_hash_changes(claude, tmp_path, isolated_git, monkeypatch):
    _setup(claude)
    write_log(claude / LES / "cards.jsonl", [{"id": f"c{i}", "level": "warn", "status": "active"} for i in range(2)])
    empty = tmp_path / "empty-gitconfig"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    team = tel.load_team(claude)
    u1 = tel.identity(claude, team)["user"]
    assert u1.startswith("anon-")
    assert tel.collect(claude) == 2
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(isolated_git))
    p = claude / "aicj" / "telemetry" / "identity.json"
    d = json.loads(p.read_text())
    d["email_checked"] = 0
    p.write_text(json.dumps(d))
    assert tel.identity(claude, team)["user"] != u1
    assert tel.collect(claude) == 0
    cards = [e for e in spool_events(claude) if e["type"] == "lesson_card"]
    assert len(cards) == len({e["card"] for e in cards}) == 2


def test_cards_changed_row_is_still_respooled(claude):
    _setup(claude)
    write_log(claude / LES / "cards.jsonl", [{"id": "c1", "level": "warn", "status": "active"}])
    assert tel.collect(claude) == 1
    write_log(claude / LES / "cards.jsonl", [{"id": "c1", "level": "warn", "status": "retired"}])
    assert tel.collect(claude) == 1


@posix_only
def test_git_timeout_kills_whole_process_tree(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    pidfile = tmp_path / "pid"
    fake = bindir / "git"
    fake.write_text(f"#!/bin/sh\nsleep 30 &\necho $! > {pidfile}\nwait\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(tel, "KILL_DRAIN_TIMEOUT", 2)
    start = time.time()
    with pytest.raises(subprocess.TimeoutExpired):
        tel._git(["status"], None, timeout=1)
    assert time.time() - start < 6
    pid = int(pidfile.read_text())
    deadline = time.time() + 3
    alive = True
    while time.time() < deadline and alive:
        try:
            os.kill(pid, 0)
            time.sleep(0.1)
        except OSError:
            alive = False
    assert not alive, "grandchild survived the timeout"


def test_ssh_command_preserved_when_user_set_it(monkeypatch):
    tel._SSH_CFG_CACHE.clear()
    monkeypatch.setenv("GIT_SSH_COMMAND", "ssh -i /my/key")
    assert tel._git_env()["GIT_SSH_COMMAND"] == "ssh -i /my/key"
    monkeypatch.delenv("GIT_SSH_COMMAND")
    monkeypatch.setenv("GIT_SSH", "/usr/bin/myssh")
    assert "GIT_SSH_COMMAND" not in tel._git_env()


def test_ssh_command_preserved_when_core_sshcommand_configured(isolated_git, monkeypatch):
    tel._SSH_CFG_CACHE.clear()
    monkeypatch.delenv("GIT_SSH_COMMAND", raising=False)
    monkeypatch.delenv("GIT_SSH", raising=False)
    isolated_git.write_text(isolated_git.read_text() + "[core]\n\tsshCommand = ssh -i /k\n", encoding="utf-8")
    assert "GIT_SSH_COMMAND" not in tel._git_env()


def test_ssh_batchmode_set_when_nothing_configured(monkeypatch):
    tel._SSH_CFG_CACHE.clear()
    monkeypatch.delenv("GIT_SSH_COMMAND", raising=False)
    monkeypatch.delenv("GIT_SSH", raising=False)
    env = tel._git_env()
    assert env["GIT_SSH_COMMAND"] == "ssh -o BatchMode=yes"
    assert env["GIT_TERMINAL_PROMPT"] == "0" and env["GCM_INTERACTIVE"] == "never"


@posix_only
def test_concurrent_emits_keep_spool_lines_intact(claude, tmp_path):
    _setup(claude)
    code = textwrap.dedent(f"""
        import sys; sys.path.insert(0, {str(HOOKS)!r})
        import aicj_telemetry as t
        from pathlib import Path
        c = Path({str(claude)!r})
        for i in range(200):
            t._spool_write(c, {{"ts": "2026-10-01T00:00:00Z", "pad": "x" * 3000, "n": i}})
    """)
    env = dict(os.environ, HOME=str(tmp_path / "home"))
    procs = [subprocess.Popen([sys.executable, "-c", code], env=env) for _ in range(4)]
    assert all(p.wait(timeout=120) == 0 for p in procs)
    lines = (claude / "aicj" / "telemetry" / "spool" / "2026-10-01.jsonl").read_text().splitlines()
    assert len(lines) == 800
    assert all(json.loads(ln)["pad"] == "x" * 3000 for ln in lines)
