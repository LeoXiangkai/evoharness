from __future__ import annotations

import builtins
import os
import stat
import subprocess
import sys

import pytest

import aicj_telemetry as tel
from .conftest import remote_files, spool_events, write_log

CORR = "aicj/lessons/corrections.jsonl"


def _seed(claude, n=2):
    write_log(claude / CORR, [{"ts": f"2026-10-27T10:0{i}:00Z"} for i in range(n)])


def _all_public(claude):
    out = [tel.load_team(claude), tel.emit(claude, "correction", {}), tel.collect(claude),
           tel.run_report(claude, force=True), tel.status(claude)]
    team = tel.load_team(claude)
    if team:
        out += [tel.identity(claude, team), tel.task_key(claude, str(claude), team)]
    out.append(tel.sanitize({"type": "x"}))
    return out


def test_garbage_arguments_never_raise(claude, team):
    for bad in (None, 5, {}):
        tel.sanitize(bad)
    assert tel.sanitize({}) is None
    assert tel.emit(claude, "correction", None) in (True, False)
    assert tel.emit(None, "correction", {}) is False
    assert tel.collect(None) == 0
    assert tel.run_report(None)["status"] in ("skipped", "failed")
    assert tel.status(None)["enabled"] is False


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="needs POSIX non-root")
def test_unwritable_dir_does_not_raise(claude, team):
    _seed(claude)
    tdir = claude / "aicj" / "telemetry"
    tdir.mkdir(parents=True)
    tdir.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        _all_public(claude)
        assert tel.emit(claude, "correction", {}) is False
    finally:
        tdir.chmod(stat.S_IRWXU)


def test_open_raising_does_not_escape(claude, team, monkeypatch):
    _seed(claude)
    real = builtins.open

    def boom(*a, **k):
        raise OSError("disk on fire")

    monkeypatch.setattr(builtins, "open", boom)
    monkeypatch.setattr(os, "open", lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))
    try:
        _all_public(claude)
    finally:
        monkeypatch.setattr(builtins, "open", real)


def test_interrupted_upload_catches_up(claude, team, remote, monkeypatch):
    _seed(claude)
    real = tel._git

    def flaky(args, cwd, timeout=tel.GIT_TIMEOUT):
        if "push" in args:
            raise subprocess.TimeoutExpired("git", 1)
        return real(args, cwd, timeout)

    monkeypatch.setattr(tel, "_git", flaky)
    res = tel.run_report(claude, force=True)
    assert res["status"] == "failed" and res["reason"] == "timeout"
    assert tel.status(claude)["pending"] == 2 and not (claude / "aicj/telemetry/report.lock").exists()
    monkeypatch.setattr(tel, "_git", real)
    res = tel.run_report(claude, force=True)
    assert res["status"] == "ok"
    evs = [l for v in remote_files(remote).values() for l in v.splitlines() if l.strip()]
    assert len(evs) == 2


def test_git_missing_is_reported_not_raised(claude, team, monkeypatch):
    _seed(claude)

    def nogit(*a, **k):
        raise FileNotFoundError("git")

    monkeypatch.setattr(tel, "_git", nogit)
    res = tel.run_report(claude, force=True)
    assert res["status"] == "failed"
    assert len(spool_events(claude)) == 2  # collect used no git for corrections
    assert tel.status(claude)["last_error"] == "git_missing"


def test_internal_exception_in_report_is_contained(claude, team, monkeypatch):
    monkeypatch.setattr(tel, "_prepare_repo", lambda *a, **k: 1 / 0)
    assert tel.run_report(claude, force=True) == {"status": "failed", "reason": "internal", "uploaded": 0}
    assert not (claude / "aicj/telemetry/report.lock").exists()


def test_broken_collect_does_not_block_upload(claude, team, remote, monkeypatch):
    _seed(claude)
    tel.collect(claude)
    monkeypatch.setattr(tel, "_collect_locked", lambda *a, **k: 1 / 0)
    assert tel.run_report(claude, force=True)["uploaded"] == 2


def test_corrupt_state_and_identity_files_recover(claude, team):
    tdir = claude / "aicj" / "telemetry"
    tdir.mkdir(parents=True)
    (tdir / "state.json").write_text("{{{", encoding="utf-8")
    (tdir / "identity.json").write_text("[]", encoding="utf-8")
    (tdir / "tasks.json").write_text("nope", encoding="utf-8")
    _seed(claude)
    assert tel.collect(claude) == 2
    ident = tel.identity(claude, team)
    assert len(ident["install"]) == 8


def test_does_not_touch_real_home(claude, team, tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    _seed(claude)
    tel.collect(claude)
    assert list(home.iterdir()) == []
