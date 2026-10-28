from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from .conftest import REPO_ROOT as REPO, spool_events

sys.path.insert(0, str(REPO / "installer"))
from lib import telemetry as inst_tel  # noqa: E402
from lib.util import UserError  # noqa: E402

REAL_LIB = REPO / "core" / "hooks" / "aicj_telemetry.py"

AICJ = REPO / "installer" / "aicj.py"


def run_aicj(*args, home):
    env = dict(os.environ, AICJ_SKIP_HUD="1")
    return subprocess.run([sys.executable, str(AICJ), *args, "--home", str(home), "--source", str(REPO)],
                          capture_output=True, text=True, encoding="utf-8", cwd=str(REPO), env=env, timeout=180)


def _join(tmp_path, home):
    team = tmp_path / "team-in.json"
    team.write_text(json.dumps({"team_id": "team1", "collect_repo": str(tmp_path / "none.git"),
                                "salt": "salt-abc", "branch": "main"}), encoding="utf-8")
    r = run_aicj("install", "--packs", "none", "--team-config", str(team), home=home)
    assert r.returncode == 0, r.stdout + r.stderr


def _installs(home):
    return [e for e in spool_events(home / ".claude") if e["type"] == "install"]


def test_failed_reinstall_on_team_emits_ok_false_with_error_kind(tmp_path):
    home = tmp_path / "home"
    _join(tmp_path, home)
    before = len(_installs(home))
    (home / ".claude" / "settings.json").write_text("{not json", encoding="utf-8")
    r = run_aicj("install", "--packs", "none", home=home)
    assert r.returncode != 0
    new = _installs(home)[before:]
    assert [(e["command"], e["ok"], e["error_kind"]) for e in new] == [("install", False, "UserError")]


def test_failed_uninstall_on_team_emits_ok_false(tmp_path):
    home = tmp_path / "home"
    _join(tmp_path, home)
    before = len(_installs(home))
    (home / ".claude" / "settings.json").write_text("{not json", encoding="utf-8")
    r = run_aicj("uninstall", home=home)
    assert r.returncode != 0
    new = _installs(home)[before:]
    assert [(e["command"], e["ok"], e["error_kind"]) for e in new] == [("uninstall", False, "UserError")]


def test_failed_install_without_team_leaves_no_telemetry_footprint(tmp_path):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text("{not json", encoding="utf-8")
    r = run_aicj("install", "--packs", "none", home=home)
    assert r.returncode != 0
    assert not (home / ".claude" / "aicj").exists()


def _team(**over):
    team = {"team_id": "team1", "collect_repo": "https://example.test/x.git", "salt": "s", "branch": "main"}
    team.update(over)
    return json.dumps(team).encode()


def test_validate_team_bytes_rejects_trailing_newline_team_id():
    with pytest.raises(UserError):
        inst_tel.validate_team_bytes(_team(team_id="demo\n"))
    assert b"team1" in inst_tel.validate_team_bytes(_team())


def test_validate_team_bytes_rejects_dash_collect_repo_and_unsafe_branch():
    with pytest.raises(UserError):
        inst_tel.validate_team_bytes(_team(collect_repo="--upload-pack=x"))
    for branch in ("-x", "a..b", "a b", "x.lock", "/x", "a@{b"):
        with pytest.raises(UserError):
            inst_tel.validate_team_bytes(_team(branch=branch))
    inst_tel.validate_team_bytes(_team(branch="feature/ok"))


def test_safe_ref_mirror_agrees_with_library():
    sys.path.insert(0, str(REPO / "core" / "hooks"))
    import aicj_telemetry as tel
    for name in ("main", "feature/ok", "-x", "a..b", "a b", "x.lock", "/x", "a/", "a@{b", "a~1", "a\x01", "", "x."):
        assert inst_tel._safe_ref(name) == tel._safe_ref(name), name


def test_existing_team_bytes_validates(tmp_path):
    p = tmp_path / ".claude" / "aicj"
    p.mkdir(parents=True)
    (p / "team.json").write_bytes(_team(collect_repo="-bad"))
    assert inst_tel.existing_team_bytes(tmp_path) is None
    (p / "team.json").write_bytes(_team())
    assert inst_tel.existing_team_bytes(tmp_path) is not None


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group kill; Windows uses taskkill /T")
def test_uninstall_timeout_kills_grandchildren(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".claude" / "aicj").mkdir(parents=True)
    (home / ".claude" / "bin").mkdir(parents=True)
    (home / ".claude" / "hooks" / "aicj").mkdir(parents=True)
    (home / ".claude" / "aicj" / "team.json").write_text("{}", encoding="utf-8")
    (home / ".claude" / inst_tel.LIB_REL).write_text("", encoding="utf-8")
    pidfile = tmp_path / "gc.pid"
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    fake_git = fakebin / "git"
    fake_git.write_text(f"#!/bin/sh\n[ \"$1\" = status ] || exit 1\necho $$ > {str(pidfile)!r}\nexec sleep 30\n", encoding="utf-8")
    fake_git.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fakebin}{os.pathsep}{os.environ['PATH']}")
    (home / ".claude" / "bin" / "aicj-telemetry").write_text(
        "import importlib.util, sys, time\n"
        f"spec = importlib.util.spec_from_file_location('aicj_telemetry', {str(REAL_LIB)!r})\n"
        "mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)\n"
        "mod._git(['status'], None, 60)\n"  # real chain: _git -> (fake) git grandchild
        "time.sleep(30)\n",
        encoding="utf-8")
    inst_tel.uninstall_report(home, None, timeout=2)
    import time
    for _ in range(20):
        if pidfile.exists() and pidfile.read_text().strip():
            break
        time.sleep(0.1)
    pid = int(pidfile.read_text())
    for _ in range(20):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.2)
    os.kill(pid, 9)
    pytest.fail("grandchild survived uninstall timeout")


def test_run_killing_tree_kills_group_on_keyboard_interrupt(monkeypatch):
    killed = []

    class FakeProc:
        pid = 424242

        def wait(self, timeout=None):
            if killed:
                return 0
            raise KeyboardInterrupt

        def kill(self):
            killed.append("kill")

    monkeypatch.setattr(inst_tel.subprocess, "Popen", lambda *a, **k: FakeProc())
    if os.name == "nt":
        monkeypatch.setattr(inst_tel.subprocess, "run", lambda cmd, **k: killed.append(cmd[0]))
    else:
        monkeypatch.setattr(inst_tel.os, "killpg", lambda pid, sig: killed.append(("killpg", pid)))
    with pytest.raises(KeyboardInterrupt):
        inst_tel._run_killing_tree(["x"], {}, 5)
    assert killed and killed[0] in (("killpg", 424242), "taskkill")
