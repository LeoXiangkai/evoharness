from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import AICJ, FIXTURE, REPO, run_aicj

sys.path.insert(0, str(REPO / "installer"))
from lib import manifest, release  # noqa: E402
from lib.util import UserError  # noqa: E402


def git(cwd: Path, *args: str) -> str:
    env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    result = subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                             "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args],
                            capture_output=True, text=True, encoding="utf-8", env=env)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def release_commit(work: Path, version: str, tag: str | None = None) -> None:
    (work / "VERSION").write_text(version + "\n", encoding="utf-8")
    git(work, "add", "-A")
    git(work, "commit", "-qm", f"chore: release {version}")
    if tag:
        git(work, "tag", "-a", tag, "-m", tag)


def upgrade(clone: Path, home: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, AICJ_SKIP_HUD="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    return subprocess.run([sys.executable, str(AICJ), "upgrade", "--home", str(home), "--source", str(clone), *args],
                          capture_output=True, text=True, encoding="utf-8", env=env, cwd=str(REPO))


def run_install(clone: Path, home: Path, *args: str) -> subprocess.CompletedProcess:
    return run_aicj("install", "--packs", "none", *args, home=home, source=clone)


@pytest.fixture()
def repos(tmp_path: Path):
    """origin (bare) + clone, with tags v0.9.0, v0.10.0, v0.9.1, an invalid tag, and main ahead of the last tag."""
    work = tmp_path / "work"
    shutil.copytree(FIXTURE, work)
    shutil.copytree(REPO / "installer", work / "installer", ignore=shutil.ignore_patterns("__pycache__"))
    (work / "core" / "hooks").mkdir(parents=True, exist_ok=True)
    shutil.copy2(REPO / "core" / "hooks" / "aicj_telemetry.py", work / "core" / "hooks" / "aicj_telemetry.py")
    (work / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    git(work, "init", "-q", "-b", "main")
    release_commit(work, "0.9.0", "v0.9.0")
    release_commit(work, "0.10.0", "v0.10.0")
    git(work, "tag", "-a", "v0.9.1", "v0.9.0", "-m", "v0.9.1")
    git(work, "tag", "-a", "v1.0", "-m", "not semver")
    git(work, "tag", "-a", "v1.0.0-rc1", "-m", "not semver")
    git(work, "tag", "-a", "release-5.0.0", "-m", "not semver")
    release_commit(work, "0.11.0-dev".replace("-dev", ""))  # main ahead, untagged
    origin = tmp_path / "origin.git"
    git(tmp_path, "clone", "-q", "--bare", str(work), str(origin))
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", str(origin), str(clone))
    return work, origin, clone


def head_version(clone: Path) -> str:
    return (clone / "VERSION").read_text(encoding="utf-8").strip()


def test_semver_parsing_picks_numeric_max_and_ignores_others() -> None:
    assert release.highest_tag(["v0.9.0", "v0.10.0", "v0.9.1", "v1.0", "v1.0.0-rc1", "x1.2.3", "v2.0.0\n", " v0.10.1 "]) == "v2.0.0"
    assert release.highest_tag(["v0.9.0", "v0.10.0", "v0.9.1"]) == "v0.10.0"
    assert release.highest_tag(["v1.0", "latest"]) is None
    assert release.parse_tag("v01.2.3") == (1, 2, 3)
    assert release.parse_tag("v1.2.3\nv4.5.6") is None


def test_channel_priority() -> None:
    r = release.resolve_channel
    assert r("", "", "") == ("stable", "")
    assert r("", "", "beta") == ("beta", "")
    assert r("", "stable", "beta") == ("stable", "stable")  # override beats team.json
    assert r("beta", "stable", "") == ("beta", "beta")      # flag beats override
    assert r("auto", "beta", "stable") == ("stable", "")    # auto clears override, falls to team
    assert r("auto", "beta", "") == ("stable", "")
    assert r("", " BETA ", "") == ("beta", "beta")
    assert r("", "bogus", "bogus") == ("stable", "")


def test_stable_check_selects_highest_tag_and_leaves_head(repos, home: Path) -> None:
    _work, _origin, clone = repos
    assert head_version(clone) == "0.11.0"
    before = git(clone, "rev-parse", "HEAD")
    result = upgrade(clone, home, "--check")
    assert result.returncode == 0, result.stderr
    assert "0.11.0" in result.stdout and "0.10.0" in result.stdout
    assert "降级" in result.stdout  # tag v0.10.0 is older than main's 0.11.0
    assert git(clone, "rev-parse", "HEAD") == before
    assert git(clone, "symbolic-ref", "--short", "HEAD") == "main"
    assert not (home / ".claude").exists()


def test_upgrade_no_install_stable_detaches_at_tag_then_beta_returns_to_main(repos, home: Path) -> None:
    _work, _origin, clone = repos
    assert upgrade(clone, home, "--no-install").returncode == 0
    assert head_version(clone) == "0.10.0"
    assert git(clone, "rev-parse", "HEAD") == git(clone, "rev-parse", "refs/tags/v0.10.0^{commit}")
    assert subprocess.run(["git", "-C", str(clone), "symbolic-ref", "-q", "HEAD"], capture_output=True).returncode != 0
    result = upgrade(clone, home, "--no-install", "--channel", "beta")
    assert result.returncode == 0, result.stderr
    assert git(clone, "symbolic-ref", "--short", "HEAD") == "main"
    assert head_version(clone) == "0.11.0"
    assert "降级" not in result.stdout


def test_beta_pulls_new_origin_commits(repos, home: Path) -> None:
    work, origin, clone = repos
    release_commit(work, "0.12.0")
    git(work, "push", "-q", str(origin), "main")
    result = upgrade(clone, home, "--no-install", "--channel", "beta")
    assert result.returncode == 0, result.stderr
    assert head_version(clone) == "0.12.0"
    assert "+" in result.stdout  # beta display version carries the short commit


def test_new_tag_on_origin_is_fetched_for_stable(repos, home: Path) -> None:
    work, origin, clone = repos
    release_commit(work, "0.10.1", "v0.10.1")
    git(work, "push", "-q", str(origin), "main", "--tags")
    assert upgrade(clone, home, "--no-install").returncode == 0
    assert head_version(clone) == "0.10.1"


def test_unpushed_local_tag_is_not_a_stable_target(repos, home: Path) -> None:
    _work, _origin, clone = repos
    git(clone, "tag", "v9.9.9")
    assert upgrade(clone, home, "--no-install").returncode == 0
    assert head_version(clone) == "0.10.0"


def test_beta_refuses_local_main_with_unpushed_commits(repos, home: Path) -> None:
    _work, _origin, clone = repos
    release_commit(clone, "0.99.0", None)
    before = git(clone, "rev-parse", "HEAD")
    result = upgrade(clone, home, "--no-install", "--channel", "beta")
    assert result.returncode == 2 and "origin/main" in result.stderr and "Traceback" not in result.stderr
    assert git(clone, "rev-parse", "HEAD") == before


def test_downgrade_is_reported_and_performed(repos, home: Path) -> None:
    _work, _origin, clone = repos
    result = upgrade(clone, home, "--no-install")
    assert "降级" in result.stdout
    assert head_version(clone) == "0.10.0"


def test_dirty_worktree_is_refused_including_untracked(repos, home: Path) -> None:
    _work, _origin, clone = repos
    (clone / "scratch.txt").write_text("x", encoding="utf-8")
    result = upgrade(clone, home, "--check")
    assert result.returncode == 2
    assert "uncommitted" in result.stderr and "Traceback" not in result.stderr


def test_other_branch_is_refused(repos, home: Path) -> None:
    _work, _origin, clone = repos
    git(clone, "checkout", "-q", "-b", "feature/mine")
    result = upgrade(clone, home, "--check")
    assert result.returncode == 2
    assert "feature/mine" in result.stderr and "Traceback" not in result.stderr
    assert git(clone, "symbolic-ref", "--short", "HEAD") == "feature/mine"


def test_non_git_source_is_refused(tmp_path: Path, home: Path) -> None:
    plain = tmp_path / "plain"
    shutil.copytree(FIXTURE, plain)
    result = upgrade(plain, home, "--check")
    assert result.returncode == 2 and "Traceback" not in result.stderr


def test_no_tags_stable_warns_and_stays(tmp_path: Path, home: Path) -> None:
    work = tmp_path / "w"
    shutil.copytree(FIXTURE, work)
    git(work, "init", "-q", "-b", "main")
    release_commit(work, "0.9.0")
    origin = tmp_path / "o.git"
    git(tmp_path, "clone", "-q", "--bare", str(work), str(origin))
    clone = tmp_path / "c"
    git(tmp_path, "clone", "-q", str(origin), str(clone))
    before = git(clone, "rev-parse", "HEAD")
    result = upgrade(clone, home, "--no-install")
    assert result.returncode == 0, result.stderr
    assert "tag" in result.stderr
    assert git(clone, "rev-parse", "HEAD") == before
    assert git(clone, "symbolic-ref", "--short", "HEAD") == "main"


def test_upgrade_without_manifest_requires_install(repos, home: Path) -> None:
    _work, _origin, clone = repos
    result = upgrade(clone, home)
    assert result.returncode == 2 and "install" in result.stderr
    assert head_version(clone) == "0.11.0"


def test_team_config_channel_is_used_by_upgrade_check(repos, home: Path, tmp_path: Path) -> None:
    _work, _origin, clone = repos
    team = tmp_path / "team.json"
    team.write_text(json.dumps({"team_id": "t1", "collect_repo": "x", "salt": "s", "channel": "beta"}), encoding="utf-8")
    result = upgrade(clone, home, "--check", "--team-config", str(team))
    assert result.returncode == 0, result.stderr
    assert "(beta)" in result.stdout
    assert "(stable)" in upgrade(clone, home, "--check", "--team-config", str(team), "--channel", "stable").stdout


def test_team_json_rejects_bad_channel() -> None:
    from lib import telemetry
    base = {"team_id": "t1", "collect_repo": "x", "salt": "s"}
    for bad in ("nightly", 3, ""):
        with pytest.raises(UserError):
            telemetry.validate_team_bytes(json.dumps({**base, "channel": bad}).encode())
    telemetry.validate_team_bytes(json.dumps({**base, "channel": "beta"}).encode())


def _manifest(home: Path) -> dict:
    return json.loads((home / ".claude/aicj/manifest.json").read_text(encoding="utf-8"))


def test_install_records_framework_and_uninstall_removes_framework_json(repos, home: Path) -> None:
    _work, _origin, clone = repos
    result = run_install(clone, home)
    assert result.returncode == 0, result.stderr
    record = json.loads((home / ".claude/aicj/framework.json").read_text(encoding="utf-8"))
    assert record == {"version": "0.11.0", "channel": "stable", "commit": git(clone, "rev-parse", "--short=7", "HEAD")}
    data = _manifest(home)
    assert data["framework"] == record
    assert data["options"]["channel"] == "stable" and data["options"]["channel_override"] == ""
    assert data["version"] == 1
    assert ".claude/aicj/framework.json" in {e["path"] for e in data["entries"]}
    status = run_aicj("status", home=home, source=clone)
    assert "framework 0.11.0 channel=stable" in status.stdout
    assert run_aicj("uninstall", home=home).returncode == 0
    assert not (home / ".claude/aicj/framework.json").exists()
    assert not (home / ".claude/aicj").exists() or not any((home / ".claude/aicj").iterdir())


def test_install_channel_flag_override_and_auto(repos, home: Path) -> None:
    _work, _origin, clone = repos
    assert run_install(clone, home, "--channel", "beta").returncode == 0
    opts = _manifest(home)["options"]
    assert (opts["channel"], opts["channel_override"]) == ("beta", "beta")
    framework = json.loads((home / ".claude/aicj/framework.json").read_text(encoding="utf-8"))
    assert framework["channel"] == "beta"
    assert run_install(clone, home).returncode == 0  # no flag: override survives
    assert _manifest(home)["options"]["channel_override"] == "beta"
    assert run_install(clone, home, "--channel", "auto").returncode == 0
    opts = _manifest(home)["options"]
    assert (opts["channel"], opts["channel_override"]) == ("stable", "")


def test_old_manifest_without_new_fields_loads(repos, home: Path) -> None:
    _work, _origin, clone = repos
    assert run_install(clone, home).returncode == 0
    path = home / ".claude/aicj/manifest.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data.pop("framework")
    data["options"].pop("channel")
    data["options"].pop("channel_override")
    path.write_text(json.dumps(data), encoding="utf-8")
    loaded = manifest.load(home)
    assert loaded is not None and loaded.framework == {}
    assert loaded.options.channel == "" and loaded.options.channel_override == ""
    assert run_aicj("status", home=home, source=clone).returncode == 0
    assert run_install(clone, home).returncode == 0  # reinstall upgrades the record in place
    assert "framework" in _manifest(home)


def test_doctor_and_status_report_source_drift(repos, home: Path) -> None:
    work, origin, clone = repos
    assert run_install(clone, home).returncode == 0
    doctor = run_aicj("doctor", home=home, source=clone)
    assert "framework 0.11.0 channel=stable" in doctor.stdout and "与已装记录不同" not in doctor.stdout
    assert upgrade(clone, home, "--no-install").returncode == 0  # checkout moves to v0.10.0, install record stays
    drift = run_aicj("doctor", home=home, source=clone)
    assert "与已装记录不同" in drift.stdout
    status = run_aicj("status", home=home, source=clone)
    assert "与已装记录不同" in status.stdout


def test_upgrade_reinstalls_and_updates_recorded_framework(repos, home: Path) -> None:
    _work, _origin, clone = repos
    assert run_install(clone, home, "--adapters", "").returncode == 0
    assert _manifest(home)["framework"]["version"] == "0.11.0"
    result = upgrade(clone, home)
    assert result.returncode == 0, result.stdout + result.stderr
    data = _manifest(home)
    assert data["framework"]["version"] == "0.10.0"
    assert data["framework"]["channel"] == "stable"
    assert data["source"]["commit"] == git(clone, "rev-parse", "HEAD")
    assert json.loads((home / ".claude/aicj/framework.json").read_text(encoding="utf-8"))["version"] == "0.10.0"
    # switching channel through upgrade records the override and returns to main
    result = upgrade(clone, home, "--channel", "beta")
    assert result.returncode == 0, result.stdout + result.stderr
    data = _manifest(home)
    assert data["framework"]["version"] == "0.11.0" and data["framework"]["channel"] == "beta"
    assert data["options"]["channel_override"] == "beta"
    assert upgrade(clone, home, "--channel", "auto").returncode == 0
    assert _manifest(home)["options"]["channel_override"] == ""
    assert _manifest(home)["framework"]["channel"] == "stable"


def test_install_event_carries_channel_commit_and_envelope_version(repos, home: Path, tmp_path: Path) -> None:
    _work, _origin, clone = repos
    team = tmp_path / "team.json"
    team.write_text(json.dumps({"team_id": "t1", "collect_repo": str(tmp_path / "nowhere.git"), "salt": "s",
                                "channel": "beta"}), encoding="utf-8")
    result = run_install(clone, home, "--team-config", str(team))
    assert result.returncode == 0, result.stderr
    spool = home / ".claude/aicj/telemetry/spool"
    events = [json.loads(line) for f in spool.glob("*.jsonl") for line in f.read_text(encoding="utf-8").splitlines()]
    installs = [e for e in events if e["type"] == "install" and e.get("command") == "install"]
    assert installs, events
    event = installs[-1]
    framework = json.loads((home / ".claude/aicj/framework.json").read_text(encoding="utf-8"))
    assert framework["channel"] == "beta"  # from team.json
    assert event["channel"] == "beta" and event["commit"] == framework["commit"]
    assert event["version"] == framework["version"] == "0.11.0"
    upgraded = upgrade(clone, home, "--channel", "stable")
    assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr
    events = [json.loads(line) for f in spool.glob("*.jsonl") for line in f.read_text(encoding="utf-8").splitlines()]
    assert any(e["type"] == "install" and e.get("command") == "upgrade" and e.get("ok") is True and e["channel"] == "stable"
               for e in events)


def test_version_file_is_valid_semver() -> None:
    assert (REPO / "VERSION").read_text(encoding="utf-8") == "0.9.0\n"
