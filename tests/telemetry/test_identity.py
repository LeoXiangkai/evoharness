from __future__ import annotations

import json
import subprocess

import aicj_telemetry as tel
from .conftest import write_team


def test_load_team_none_when_missing_or_invalid(claude):
    assert tel.load_team(claude) is None
    (claude / "aicj").mkdir()
    (claude / "aicj" / "team.json").write_text("{not json", encoding="utf-8")
    assert tel.load_team(claude) is None
    (claude / "aicj" / "team.json").write_text(json.dumps({"team_id": "t"}), encoding="utf-8")
    assert tel.load_team(claude) is None


def test_load_team_defaults_and_bom_crlf(claude, remote):
    p = claude / "aicj"
    p.mkdir()
    raw = json.dumps({"team_id": "t1", "collect_repo": str(remote), "salt": "s"}, indent=2)
    (p / "team.json").write_bytes(b"\xef\xbb\xbf" + raw.replace("\n", "\r\n").encode())
    team = tel.load_team(claude)
    assert team["branch"] == "main" and team["upload_interval_hours"] == 24.0 and team["spool_max_mb"] == 20.0


def test_no_team_creates_no_files(claude):
    assert tel.emit(claude, "correction", {}) is False
    assert tel.collect(claude) == 0
    assert tel.run_report(claude, force=True)["status"] == "skipped"
    assert tel.status(claude) == {"enabled": False}
    assert list(claude.rglob("*")) == []


def test_identity_stable_hashed_and_persisted(claude, team):
    a = tel.identity(claude, team)
    b = tel.identity(claude, team)
    assert a == b and len(a["user"]) == 16 and len(a["install"]) == 8
    assert "dev" not in a["user"] and "example" not in a["user"]
    assert (claude / "aicj" / "telemetry" / "identity.json").is_file()


def test_same_email_different_installs_same_user(tmp_path, remote):
    users, installs = set(), set()
    for n in ("a", "b"):
        d = tmp_path / n
        d.mkdir()
        write_team(d, remote)
        ident = tel.identity(d, tel.load_team(d))
        users.add(ident["user"])
        installs.add(ident["install"])
    assert len(users) == 1 and len(installs) == 2


def test_salt_changes_user(tmp_path, remote):
    out = []
    for salt in ("s1", "s2"):
        d = tmp_path / salt
        d.mkdir()
        write_team(d, remote, salt=salt)
        out.append(tel.identity(d, tel.load_team(d))["user"])
    assert out[0] != out[1]


def test_anonymous_without_email(claude, team, tmp_path, monkeypatch):
    empty = tmp_path / "empty-gitconfig"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    ident = tel.identity(claude, team)
    assert ident["user"].startswith("anon-")
    assert tel.status(claude)["anonymous"] is True


def _repo(tmp_path, name="proj", branch="feature/x"):
    r = tmp_path / name
    subprocess.run(["git", "init", "-q", "-b", branch, str(r)], check=True)
    return r


def test_task_key_stable_and_distinct_per_branch(claude, team, tmp_path):
    r = _repo(tmp_path)
    k1 = tel.task_key(claude, str(r), team)
    k2 = tel.task_key(claude, str(r), team)
    assert k1 == k2 and len(k1[0]) == 12 and len(k1[1]) == 12
    subprocess.run(["git", "-C", str(r), "checkout", "-q", "-b", "feature/y"], check=True)
    k3 = tel.task_key(claude, str(r), team)
    assert k3[0] == k1[0] and k3[1] != k1[1]
    tasks = json.loads((claude / "aicj" / "telemetry" / "tasks.json").read_text(encoding="utf-8"))
    assert tasks[k1[1]]["branch"] == "feature/x"


def test_task_key_non_repo_and_bad_cwd(claude, team, tmp_path):
    assert tel.task_key(claude, str(tmp_path), team) is None
    assert tel.task_key(claude, str(tmp_path / "missing"), team) is None
    assert tel.task_key(claude, "", team) is None


def test_task_key_same_origin_shares_repo_hash(claude, team, tmp_path):
    a, b = _repo(tmp_path, "a"), _repo(tmp_path, "b")
    for r in (a, b):
        subprocess.run(["git", "-C", str(r), "remote", "add", "origin", "git@host.test:org/app.git"], check=True)
    assert tel.task_key(claude, str(a), team)[0] == tel.task_key(claude, str(b), team)[0]


def test_origin_forms_normalised():
    n = tel._norm_origin
    assert n("git@host.test:org/app.git") == n("https://host.test/org/app")
