from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS = REPO_ROOT / "core" / "hooks"
if str(HOOKS) not in sys.path:
    sys.path.insert(0, str(HOOKS))

import aicj_telemetry as tel  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_git(tmp_path, monkeypatch):
    cfg = tmp_path / "gitconfig-global"
    cfg.write_text("[user]\n\temail = dev" + "@example.test\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("AICJ_CLAUDE_DIR", raising=False)
    return cfg


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True).stdout


def make_remote(tmp_path: Path, name: str = "remote.git") -> Path:
    remote = tmp_path / name
    git(tmp_path, "init", "--bare", "-b", "main", str(remote))
    seed = tmp_path / (name + "-seed")
    git(tmp_path, "init", "-b", "main", str(seed))
    (seed / "README").write_text("x\n", encoding="utf-8")
    git(seed, "add", "-A")
    git(seed, "-c", "user.name=t", "-c", "user.email=t" + "@x.test", "commit", "-qm", "init")
    git(seed, "push", str(remote), "main")
    return remote


def write_team(claude: Path, remote: Path | str, **extra) -> dict:
    team = {"team_id": "team1", "collect_repo": str(remote), "salt": "salt-abc", "branch": "main"}
    team.update(extra)
    (claude / "aicj").mkdir(parents=True, exist_ok=True)
    (claude / "aicj" / "team.json").write_text(json.dumps(team), encoding="utf-8")
    return team


@pytest.fixture
def claude(tmp_path):
    d = tmp_path / "claude"
    d.mkdir()
    return d


@pytest.fixture
def remote(tmp_path):
    return make_remote(tmp_path)


@pytest.fixture
def team(claude, remote):
    write_team(claude, remote)
    return tel.load_team(claude)


def write_log(path: Path, rows, crlf: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    nl = "\r\n" if crlf else "\n"
    path.write_bytes(("".join(json.dumps(r) + nl for r in rows)).encode("utf-8"))


def spool_events(claude: Path) -> list[dict]:
    out = []
    d = claude / "aicj" / "telemetry" / "spool"
    if d.is_dir():
        for f in sorted(d.glob("*.jsonl")):
            out += [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]
    return out


def remote_files(remote: Path) -> dict[str, str]:
    files = git(remote, "ls-tree", "-r", "--name-only", "main").split()
    return {f: git(remote, "show", "main:" + f) for f in files if f != "README"}
