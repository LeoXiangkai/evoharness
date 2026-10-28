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


def _user_repo(tmp_path):
    from .conftest import git
    user_repo = tmp_path / "userrepo"
    git(tmp_path, "init", "-b", "main", str(user_repo))
    (user_repo / "secret.txt").write_text("proprietary\n", encoding="utf-8")
    git(user_repo, "add", "-A")
    git(user_repo, "-c", "user.name=u", "-c", "user.email=u" + "@x.test", "commit", "-qm", "user work")
    git(user_repo, "remote", "add", "origin", "https://example.invalid/user/repo.git")
    return user_repo


def test_inherited_git_dir_does_not_touch_user_repo(claude, team, remote, tmp_path, monkeypatch):
    from .conftest import git
    user_repo = _user_repo(tmp_path)
    head, origin = git(user_repo, "rev-parse", "HEAD"), git(user_repo, "remote", "get-url", "origin")
    _seed(claude)
    monkeypatch.setenv("GIT_DIR", str(user_repo / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(user_repo))
    res = tel.run_report(claude, force=True)
    monkeypatch.delenv("GIT_DIR")
    monkeypatch.delenv("GIT_WORK_TREE")
    assert res["status"] == "ok", res
    assert git(user_repo, "rev-parse", "HEAD") == head
    assert git(user_repo, "remote", "get-url", "origin") == origin
    assert "user work" not in git(remote, "log", "main", "--format=%s")
    assert "secret.txt" not in git(remote, "ls-tree", "-r", "--name-only", "main")
    assert remote_files(remote)


def test_inherited_git_identity_env_is_ignored(claude, team, remote, monkeypatch):
    from .conftest import git
    _seed(claude)
    mail = "real" + "@corp.example"
    for k, v in (("GIT_AUTHOR_NAME", "Real Name"), ("GIT_AUTHOR_EMAIL", mail),
                 ("GIT_COMMITTER_NAME", "Real Name"), ("GIT_COMMITTER_EMAIL", mail),
                 ("GIT_CONFIG_COUNT", "1"), ("GIT_CONFIG_KEY_0", "user.email"), ("GIT_CONFIG_VALUE_0", mail)):
        monkeypatch.setenv(k, v)
    res = tel.run_report(claude, force=True)
    assert res["status"] == "ok", res
    who = git(remote, "log", "-1", "--format=%an <%ae> | %cn <%ce>", "main").strip()
    assert "Real Name" not in who and "corp.example" not in who
    assert who.count("aicj-telemetry <") == 2 and who.count("@telemetry.invalid>") == 2


def test_git_env_allowlist(monkeypatch):
    for k in ("GIT_DIR", "GIT_INDEX_FILE", "GIT_CONFIG_PARAMETERS", "GIT_SSH_COMMAND", "GIT_CONFIG_GLOBAL"):
        monkeypatch.setenv(k, "x")
    env = tel._git_env(None, "u1")
    assert "GIT_DIR" not in env and "GIT_INDEX_FILE" not in env and "GIT_CONFIG_PARAMETERS" not in env
    assert env["GIT_SSH_COMMAND"] == "x" and env["GIT_CONFIG_GLOBAL"] == "x"


def test_git_env_keeps_tls_auth_and_proxy_vars(monkeypatch):
    keep = ("GIT_SSL_CAINFO", "GIT_SSL_CAPATH", "GIT_SSL_CERT", "GIT_SSL_KEY", "GIT_SSL_CERT_PASSWORD_PROTECTED",
            "GIT_SSL_NO_VERIFY", "GIT_PROXY_COMMAND", "GIT_HTTP_PROXY_AUTHMETHOD",
            "GIT_HTTP_LOW_SPEED_LIMIT", "GIT_HTTP_LOW_SPEED_TIME")
    for k in keep:
        monkeypatch.setenv(k, "v-" + k)
    monkeypatch.setenv("GIT_DIR", "x")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "x")
    env = tel._git_env(None, "u1")
    assert all(env[k] == "v-" + k for k in keep)
    assert "GIT_DIR" not in env and env["GIT_AUTHOR_NAME"] == "aicj-telemetry"


def test_repo_task_lookup_honours_user_git_dir_but_upload_env_does_not(tmp_path, monkeypatch):
    user_repo = _user_repo(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv("GIT_DIR", str(user_repo / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(user_repo))
    assert tel._local_git(str(elsewhere), "rev-parse", "--show-toplevel", user_env=True) == str(user_repo.resolve())
    assert tel._local_git(str(elsewhere), "rev-parse", "--show-toplevel") is None
    assert tel._repo_task(str(elsewhere), "salt") is not None
    env = tel._git_env(None, "u1")
    assert "GIT_DIR" not in env and "GIT_WORK_TREE" not in env


def test_upload_path_never_uses_user_env():
    import inspect
    assert "user_env=True" not in inspect.getsource(tel._run)
    assert inspect.signature(tel._git).parameters["user_env"].default is False
    assert inspect.signature(tel._local_git).parameters["user_env"].default is False


def test_git_env_drops_askpass(monkeypatch):
    monkeypatch.setenv("GIT_ASKPASS", "/usr/bin/some-ide-askpass")
    assert "GIT_ASKPASS" not in tel._git_env(None, "u1")
