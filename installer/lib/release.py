"""Framework version, release channels and the git side of `aicj upgrade` (contract: docs/global-install-design.md §8)."""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .util import UserError, read_json

CHANNELS = ("stable", "beta")
FRAMEWORK_REL = ".claude/aicj/framework.json"
GIT_TIMEOUT = 60
FETCH_TIMEOUT = 120
_TAG_RE = re.compile(r"v(\d+)\.(\d+)\.(\d+)")
_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def parse_tag(name: str) -> tuple[int, int, int] | None:
    match = _TAG_RE.fullmatch(name.strip())
    return tuple(int(part) for part in match.groups()) if match else None  # type: ignore[return-value]


def highest_tag(tags: list[str]) -> str | None:
    best: tuple[tuple[int, int, int], str] | None = None
    for raw in tags:
        name = raw.strip()
        key = parse_tag(name)
        if key is not None and (best is None or key > best[0]):
            best = (key, name)
    return best[1] if best else None


def normalize_channel(value: object) -> str:
    text = str(value or "").strip().casefold()
    return text if text in CHANNELS else ""


def resolve_channel(request: str, override: str, team: str) -> tuple[str, str]:
    """Return (effective channel, new member override). Priority: flag > override > team.json > stable."""
    request = (request or "").strip().casefold()
    if request == "auto":
        new_override = ""
    elif request in CHANNELS:
        new_override = request
    else:
        new_override = normalize_channel(override)
    return new_override or normalize_channel(team) or "stable", new_override


def team_channel(team_bytes: bytes | None) -> str:
    if not team_bytes:
        return ""
    try:
        raw = json.loads(team_bytes.decode("utf-8-sig"))
    except (UnicodeError, ValueError):
        return ""
    return normalize_channel(raw.get("channel")) if isinstance(raw, dict) else ""


def read_version(root: Path | None) -> str:
    """Contents of <root>/VERSION when it is a valid X.Y.Z; empty string otherwise."""
    if root is None:
        return ""
    try:
        text = (root / "VERSION").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return ""
    return text if _VERSION_RE.fullmatch(text) else ""


def display_version(version: str, channel: str, commit: str) -> str:
    if not version:
        return "unknown"
    return f"{version}+{commit}" if channel == "beta" and commit else version


def short_commit(full: str) -> str:
    return full.strip()[:7]


def framework_record(source: Path | None, channel: str, full_commit: str) -> dict:
    return {"version": read_version(source), "channel": channel, "commit": short_commit(full_commit)}


def load_framework(home: Path) -> dict:
    """~/.claude/aicj/framework.json as a dict of strings; {} when absent or unreadable."""
    path = home / FRAMEWORK_REL
    try:
        if not path.is_file():
            return {}
        raw = read_json(path)
    except (OSError, UserError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {key: str(raw.get(key) or "") for key in ("version", "channel", "commit")}


def local_commit(root: Path) -> str:
    try:
        result = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], text=True, encoding="utf-8",
                                capture_output=True, timeout=10, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def framework_lines(home: Path, source: Path | None) -> list[str]:
    """Read-only, offline: installed version/channel/commit plus a hint when the source repo moved on."""
    record = load_framework(home)
    if not record:
        return []
    shown = display_version(record["version"], record["channel"], record["commit"])
    lines = [f"framework {shown} channel={record['channel'] or '-'} commit={record['commit'] or '-'}"]
    if source is not None and source.is_dir():
        version = read_version(source)
        commit = short_commit(local_commit(source))
        if (version and version != record["version"]) or (commit and record["commit"] and commit != record["commit"]):
            lines.append(f"framework 源仓库为 {version or '?'} ({commit or '?'})，与已装记录不同；重新 install 或运行 upgrade")
    return lines


# ---------------------------------------------------------------------------
# git side of upgrade
# ---------------------------------------------------------------------------
def _git(repo: Path, *args: str, timeout: int = GIT_TIMEOUT, check: bool = True) -> subprocess.CompletedProcess:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}
    cmd = ["git", "-C", str(repo), *args]
    try:
        result = subprocess.run(cmd, text=True, encoding="utf-8", errors="replace", capture_output=True,
                                timeout=timeout, env=env, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as exc:
        raise UserError(f"git {args[0]} timed out after {timeout}s in {repo}") from exc
    except OSError as exc:
        raise UserError(f"cannot run git: {exc}") from exc
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise UserError(f"git {' '.join(args[:2])} failed in {repo}: {detail[-1] if detail else 'exit ' + str(result.returncode)}")
    return result


@dataclass
class Target:
    channel: str
    ref: str           # tag name, "main", or "" when staying put
    commit: str        # full hash
    version: str
    stay: bool = False


def _same_path(left: str, right: str) -> bool:
    # Windows git prints C:/... while the caller may pass c:\...; compare canonical, case-folded forms.
    return os.path.normcase(os.path.realpath(left)) == os.path.normcase(os.path.realpath(right))


def require_checkout(repo: Path) -> str:
    """Validate the upgrade preconditions; returns the current branch name or '' when detached."""
    top = _git(repo, "rev-parse", "--show-toplevel", check=False)
    if top.returncode != 0 or not _same_path(top.stdout.strip(), str(repo)):
        raise UserError(f"{repo} is not the root of a git checkout; upgrade needs a git clone of the framework")
    if _git(repo, "status", "--porcelain").stdout.strip():
        raise UserError(f"{repo} has uncommitted changes; commit or stash them before upgrade")
    head = _git(repo, "symbolic-ref", "-q", "--short", "HEAD", check=False)
    branch = head.stdout.strip() if head.returncode == 0 else ""
    if branch and branch != "main":
        raise UserError(f"{repo} is on branch {branch!r}; upgrade only runs from main or a detached HEAD "
                        "(it will not switch away from your branch)")
    return branch


def fetch(repo: Path) -> None:
    _git(repo, "fetch", "--tags", "origin", timeout=FETCH_TIMEOUT)


def _version_at(repo: Path, commit: str) -> str:
    result = _git(repo, "show", f"{commit}:VERSION", check=False)
    text = result.stdout.strip() if result.returncode == 0 else ""
    return text if _VERSION_RE.fullmatch(text) else ""


def resolve_target(repo: Path, channel: str, warnings: list[str]) -> Target:
    if channel == "beta":
        commit = _git(repo, "rev-parse", "--verify", "-q", "refs/remotes/origin/main^{commit}").stdout.strip()
        return Target("beta", "main", commit, _version_at(repo, commit))
    # Ask origin, not local tags: an unpushed tag or one deleted on origin must not become the stable target.
    listing = _git(repo, "ls-remote", "--tags", "--refs", "origin", timeout=FETCH_TIMEOUT).stdout.splitlines()
    tags = [line.split("refs/tags/", 1)[1] for line in listing if "refs/tags/" in line]
    tag = highest_tag(tags)
    if tag is None:
        warnings.append("origin 上还没有 vX.Y.Z 发布 tag，stable 通道停在当前提交")
        commit = local_commit(repo)
        return Target("stable", "", commit, _version_at(repo, commit) or read_version(repo), stay=True)
    commit = _git(repo, "rev-parse", "--verify", "-q", f"refs/tags/{tag}^{{commit}}").stdout.strip()
    version = _version_at(repo, commit)
    if version != tag[1:]:
        warnings.append(f"tag {tag} 指向的提交里 VERSION 为 {version or '缺失'}，与 tag 不一致")
    return Target("stable", tag, commit, tag[1:])


def is_downgrade(repo: Path, current: str, target: str) -> bool:
    if not current or not target or current == target:
        return False
    return _git(repo, "merge-base", "--is-ancestor", target, current, check=False).returncode == 0


def checkout(repo: Path, target: Target) -> None:
    if target.stay:
        return
    if target.channel == "beta":
        _git(repo, "checkout", "main")
        # Move to exactly the commit that was reported; a second fetch via pull could move past it.
        if _git(repo, "merge-base", "--is-ancestor", "HEAD", target.commit, check=False).returncode != 0:
            raise UserError(f"local main in {repo} has commits not on origin/main; push or reset them before upgrade")
        _git(repo, "merge", "--ff-only", "-q", target.commit)
    else:
        _git(repo, "checkout", "--detach", f"refs/tags/{target.ref}")
