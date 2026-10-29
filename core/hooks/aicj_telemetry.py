#!/usr/bin/env python3
"""Team telemetry library (stdlib only, cross-platform).

Contract: docs/team-telemetry-design.md. Every public function returns early and creates no
file when <claude>/aicj/team.json is absent or invalid, and never raises. The claude directory
is only ever taken from function arguments.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

__all__ = [
    "SCHEMA", "ENVELOPE", "ENVELOPE_REQUIRED",
    "load_team", "identity", "sanitize", "emit", "collect", "run_report", "status", "task_key",
    "record_skill", "classify_rework", "skill_token",
]

SCHEMA_VERSION = 1
LOCK_STALE_SECONDS = 1800
EMAIL_RETRY_SECONDS = 3600
ATTEMPT_MIN_INTERVAL_SECONDS = 3600
GIT_TIMEOUT = 60
LOCAL_GIT_TIMEOUT = 10
PUSH_ATTEMPTS = 3
SNAPSHOT_WINDOW_DAYS = 14

# ---------------------------------------------------------------------------
# Schema (authoritative field table; docs/telemetry-fields.md is reconciled against it)
# ---------------------------------------------------------------------------
# kinds: int bool token hash user tokens date ts, or ("enum", (values...))
PLATFORMS = ("darwin", "linux", "windows", "wsl")
EVENT_TYPES = (
    "install", "project_init", "hook_gate", "bypass", "test_run", "review",
    "task_snapshot", "verify_probe", "correction", "lesson_card", "lesson_recall",
    "skill_use", "ask_user",
)
REWORK_CAUSES = ("A", "B", "C", "D", "E", "F", "G")

ENVELOPE = {
    "schema": "int",
    "type": ("enum", EVENT_TYPES),
    "ts": "ts",
    "event_id": "hash",
    "team": "token",
    "user": "user",
    "install": "hash",
    "repo": "hash",
    "task": "hash",
    "version": "token",
    "platform": ("enum", PLATFORMS),
}
ENVELOPE_REQUIRED = ("schema", "type", "ts", "event_id", "team", "user")

SCHEMA = {
    "install": {
        "command": ("enum", ("install", "uninstall", "doctor")),
        "ok": "bool", "error_kind": "token", "packs": "tokens", "adapters": "tokens",
        "hook_mode": "token", "failed_checks": "tokens", "python": "token",
    },
    "project_init": {"ok": "bool", "error_kind": "token"},
    "hook_gate": {
        "hook": "token", "event": ("enum", ("deny", "ask_dropped")),
        "mode": ("enum", ("warn", "block", "unknown")),
        "rule_tag": "token", "rule_hash": "hash", "tool": "token",
    },
    "bypass": {"kind": ("enum", ("no_verify", "hook_mode_override", "skip_tests"))},
    "test_run": {
        "framework": "token", "passed": "int", "failed": "int", "skipped": "int",
        "errors": "int", "green": "bool",
    },
    "review": {
        "kind": ("enum", ("scope", "outcome")), "review_gate": "token", "risk_signals": "tokens",
        "file_count": "int", "changed_lines": "int", "reviewers": "int", "findings": "int",
        "confirmed": "int",
    },
    "task_snapshot": {
        "commits": "int", "files_changed": "int", "files_touched_repeatedly": "int",
        "commits_after_review": "int",
    },
    "verify_probe": {"overall": "token", "layers": "tokens", "failed_layers": "tokens"},
    "correction": {"cause": ("enum", REWORK_CAUSES), "cause_rules": "token", "after_skill": "token"},
    "lesson_card": {
        "card": "hash", "level": ("enum", ("hint", "warn", "block")),
        "scope_kind": ("enum", ("global", "path")),
        "status": ("enum", ("active", "retired")), "created": "date",
    },
    "lesson_recall": {"card": "hash", "level": ("enum", ("hint", "warn", "block"))},
    "skill_use": {"skill": "token", "via": ("enum", ("tool", "slash"))},
    "ask_user": {"questions": "int"},
}

_TOKEN_RE = re.compile(r"[A-Za-z0-9._:+-]{1,64}")
_HASH_RE = re.compile(r"[0-9a-f]{8,64}")
_USER_RE = re.compile(r"(?:anon-)?[0-9a-f]{16}")
_TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

_SENSITIVE = [
    re.compile(r"(?<!\d)(?:(?:\+|00)?86[- ]?)?1[3-9]\d{9}(?!\d)"),         # mainland mobile number, optional +86/86 prefix
    re.compile(r"(?<!\d)\d{17}[\dXx](?![\dXx])"),                   # national id number
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+"),                # email
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{8,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{8,}"),
    re.compile(r"AKIA[0-9A-Z]{8,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{3,}\.[A-Za-z0-9_-]{3,}\.?[A-Za-z0-9_-]*"),  # JWT
    re.compile(r"xox[abprs]-[A-Za-z0-9-]{8,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),                          # Google API key
    re.compile(r"glpat-[A-Za-z0-9_-]{8,}"),                         # GitLab PAT
    re.compile(r"(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{8,}"),         # Stripe keys
    re.compile(r"(?<!\d)\d{1,3}(?:\.\d{1,3}){3}(?!\d)"),            # IPv4 (local service addresses)
]


# Closed vocabulary for user-defined token fields (anything else is replaced by "x-" + salted hash).
# Layer names: LAYER_SPECS in adapters/optional-plugins/verify-probe/bin/verify-probe.
KNOWN_LAYERS = frozenset({"backend-compile", "backend-unit", "service-start", "app-api", "database",
                          "frontend-unit", "e2e"})
# Rule tags: the "[tag]" prefix of gate/hook messages; the only ones emitted are in
# core/hooks/push-review-gate.py and core/bin/git-safe.
KNOWN_RULE_TAGS = frozenset({"push-review-gate", "git-safe"})
# Framework skills: directory names under core/skills/* and adapters/*/skills/* (tests keep this in sync).
KNOWN_SKILLS = frozenset({
    "add-module", "auto-sdd", "code-review", "flow", "git-commit", "local-verify", "prd", "qa-baseline",
    "qa-testcase-pro", "req-analysis", "req-intake", "sdd-pipeline", "executor-handoff-ops", "team-weekly",
})
# Claude Code built-in slash commands: never a skill_use event.
BUILTIN_SLASH = frozenset({
    "add-dir", "agents", "bug", "clear", "compact", "config", "context", "cost", "doctor", "exit", "export",
    "help", "hooks", "ide", "init", "login", "logout", "mcp", "memory", "model", "permissions", "plugin",
    "pr-comments", "privacy-settings", "release-notes", "resume", "review", "rewind", "status", "statusline",
    "terminal-setup", "theme", "todos", "usage", "vim", "quit", "reload-plugins", "output-style", "fast",
    "login-status", "upgrade", "feedback", "bashes", "install-github-app", "security-review", "skills",
})
SKILL_LOG_DAYS = 14
AFTER_SKILL_WINDOW_SECONDS = 600
REWORK_RULES_VERSION = "r2"
# The cause whose keyword appears earliest in the text wins (people state the problem before the remedy:
# "函数名不对，测试也一起改" is naming, not testing); ties go to the order A, B, F, E, C, D; no match -> G.
# Matched case-insensitively against the local correction text only; the text itself never enters an event.
REWORK_RULES = (
    ("A", ("需求", "口径", "不是这个意思", "我要的是", "理解错", "不是我要的", "requirement")),
    ("B", ("漏了", "也要", "其他地方", "也得改", "同步改", "忘了", "影响到", "missed")),
    ("F", ("测试", "用例", "断言", "mock", "test")),
    ("E", ("审查", "评审", "review", "规范", "命名", "注释", "不符合约定", "函数名", "方法名", "变量名", "类名", "改名", "重命名")),
    ("C", ("环境", "配置", "端口", "代理", "依赖", "版本", "数据库", "数据不对", "权限", "登录", "proxy", "env")),
    ("D", ("报错", "bug", "异常", "空指针", "崩", "跑不起来", "不生效", "逻辑错", "error")),
)
_SKILL_HASH_RE = re.compile(r"x-[0-9a-f]{10}")


def classify_rework(text: object) -> str:
    """Rework cause letter A..G for a correction text (rules r2)."""
    if not isinstance(text, str) or not text.strip():
        return "G"
    low = text.lower()
    best, best_pos = "G", len(low)
    for cause, words in REWORK_RULES:
        pos = min((i for i in (low.find(w) for w in words) if i >= 0), default=-1)
        if 0 <= pos < best_pos:
            best, best_pos = cause, pos
    return best


def skill_token(salt: str, name: object) -> str | None:
    """Framework skill name as-is, any other name as 'x-' + salted hash (idempotent on that form)."""
    if not isinstance(name, str):
        return None
    name = name.strip()
    if not name:
        return None
    if name in KNOWN_SKILLS or _SKILL_HASH_RE.fullmatch(name):
        return name
    return "x-" + _sha(salt, name)[:10]


def _vocab_or_hash(salt: str, value: object, vocab: frozenset) -> object:
    if not isinstance(value, str):
        return value
    if value in vocab:
        return value
    return "x-" + _sha(salt, value)[:10]


def _valid_ts(value: object) -> bool:
    if not isinstance(value, str) or not _TS_RE.fullmatch(value):
        return False
    try:
        _dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    return True


def _valid_date(value: object) -> bool:
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
        return False
    try:
        _dt.datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def _sensitive(value: str) -> bool:
    return any(p.search(value) for p in _SENSITIVE)


def _token_ok(value: object) -> bool:
    return isinstance(value, str) and bool(_TOKEN_RE.fullmatch(value)) and not _sensitive(value)


def _check(kind: object, value: object) -> bool:
    if isinstance(kind, tuple):  # ("enum", values)
        return isinstance(value, str) and value in kind[1]
    if kind == "int":
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0
    if kind == "bool":
        return isinstance(value, bool)
    if kind == "token":
        return _token_ok(value)
    if kind == "hash":
        # hex only: digit-run patterns (phone/id) would false-positive on random hex
        return isinstance(value, str) and bool(_HASH_RE.fullmatch(value))
    if kind == "user":
        # salted hash: hex-only, so the digit-run patterns would false-positive on random hex
        return isinstance(value, str) and bool(_USER_RE.fullmatch(value))
    if kind == "tokens":
        return (isinstance(value, (list, tuple)) and len(value) <= 32
                and all(_token_ok(v) for v in value))
    if kind == "ts":
        return _valid_ts(value)
    if kind == "date":
        return _valid_date(value)
    return False


def sanitize(event: dict) -> dict | None:
    """Whitelist + type-check + sensitive-pattern filter. Returns a new dict or None (drop)."""
    try:
        if not isinstance(event, dict):
            return None
        etype = event.get("type")
        if not isinstance(etype, str) or etype not in SCHEMA:
            return None
        if event.get("schema") != SCHEMA_VERSION or isinstance(event.get("schema"), bool):
            return None
        out: dict = {}
        for key, kind in ENVELOPE.items():
            if key in event and _check(kind, event[key]):
                out[key] = list(event[key]) if isinstance(event[key], tuple) else event[key]
        if any(key not in out for key in ENVELOPE_REQUIRED):
            return None
        for key, kind in SCHEMA[etype].items():
            if key in event and _check(kind, event[key]):
                out[key] = list(event[key]) if isinstance(event[key], tuple) else event[key]
        return out
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _iso(dt: _dt.datetime) -> str:
    return dt.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _norm_ts(value: object) -> str | None:
    """Normalise a local-log timestamp (Z, +00:00, fractional, other offset) to UTC ...Z."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = _dt.datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_dt.timezone.utc)
        return _iso(parsed)
    except Exception:  # e.g. OverflowError for 9999-12-31 with a negative offset
        return None


def _parse_iso(value: object) -> _dt.datetime | None:
    norm = _norm_ts(value)
    if not norm:
        return None
    return _dt.datetime.strptime(norm, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.timezone.utc)


def _sha(*parts: str) -> str:
    return hashlib.sha256("".join(parts).encode("utf-8", "replace")).hexdigest()


def _platform() -> str:
    plat = sys.platform
    if plat.startswith("darwin"):
        return "darwin"
    if plat.startswith(("win", "cygwin", "msys")):
        return "windows"
    try:
        if "microsoft" in os.uname().release.lower():
            return "wsl"
    except AttributeError:
        pass
    return "linux"


def _tdir(claude_dir: Path) -> Path:
    return Path(claude_dir) / "aicj" / "telemetry"


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_json_atomic(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex[:8] + ".tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def _append_lines(path: Path, lines: list[str]) -> None:
    if not lines:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = ("\n".join(lines) + "\n").encode("utf-8")
    # one os.write on an O_APPEND fd: concurrent hook processes cannot interleave a line (binary: no CRLF)
    fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o644)
    try:
        locked = False
        if os.name == "nt":
            try:
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                locked = True
            except Exception:
                pass
        try:
            view = memoryview(blob)
            while view:
                view = view[os.write(fd, view):]
        finally:
            if locked:
                try:
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                except Exception:
                    pass
    finally:
        os.close(fd)


def _dumps(event: dict) -> str:
    return json.dumps(event, ensure_ascii=False, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Team config / identity
# ---------------------------------------------------------------------------
def _safe_ref(name: str) -> bool:
    return (bool(name) and not name.startswith(("-", "/")) and not name.endswith(("/", ".", ".lock"))
            and ".." not in name and "//" not in name and "@{" not in name
            and not re.search(r"[\s~^:?*\[\\\x00-\x1f\x7f]", name))


def load_team(claude_dir: Path) -> dict | None:
    try:
        raw = _read_json(Path(claude_dir) / "aicj" / "team.json")
        if not isinstance(raw, dict):
            return None
        team_id, repo, salt = raw.get("team_id"), raw.get("collect_repo"), raw.get("salt")
        if not _token_ok(team_id):
            return None
        if not isinstance(repo, str) or not repo.strip():
            return None
        if not isinstance(salt, str) or not salt.strip():
            return None
        repo = repo.strip()
        if repo.startswith("-"):
            return None
        branch = raw.get("branch")
        branch = branch.strip() if isinstance(branch, str) and branch.strip() else "main"
        if not _safe_ref(branch):
            return None

        def _num(key: str, default: float) -> float:
            value = raw.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                return default
            return float(value)

        team = dict(raw)
        team.update({
            "team_id": team_id, "collect_repo": repo, "salt": salt, "branch": branch,
            "upload_interval_hours": _num("upload_interval_hours", 24.0),
            "spool_max_mb": _num("spool_max_mb", 20.0),
        })
        return team
    except Exception:
        return None


def _git_email() -> str:
    try:
        proc = subprocess.run(
            ["git", "config", "--get", "user.email"], capture_output=True, timeout=LOCAL_GIT_TIMEOUT,
            env=_git_env(), stdin=subprocess.DEVNULL,
        )
        if proc.returncode == 0:
            return proc.stdout.decode("utf-8", "replace").strip().lower()
    except Exception:
        pass
    return ""


def _user_hash(salt: str, email: str, install_id: str) -> str:
    if email:
        return _sha(salt, "\n", "".join(email.split()).lower())[:16]
    return "anon-" + _sha(salt, "\n", install_id)[:16]


def identity(claude_dir: Path, team: dict) -> dict:
    """Return {"user", "install"}; first call persists identity.json."""
    try:
        salt = str(team["salt"])
        path = _tdir(claude_dir) / "identity.json"
        cached: dict = {}
        try:
            data = _read_json(path)
            if isinstance(data, dict):
                cached = data
        except Exception:
            cached = {}
        install_id = cached.get("install_id")
        if not isinstance(install_id, str) or not install_id:
            install_id = uuid.uuid4().hex
            cached = {}
        fp = _sha(salt)[:8]
        user = cached.get("user")
        checked = cached.get("email_checked")
        checked = checked if isinstance(checked, (int, float)) and not isinstance(checked, bool) else 0
        now = time.time()
        invalid = not isinstance(user, str) or not user or cached.get("salt_fp") != fp
        # anonymous users retry the email lookup, at most once per EMAIL_RETRY_SECONDS
        retry = (not invalid and user.startswith("anon-")
                 and (now - checked >= EMAIL_RETRY_SECONDS or checked > now))
        changed = False
        if invalid or retry:
            user = _user_hash(salt, _git_email(), install_id)
            checked = now
            changed = True
        result = {"user": user, "install": _sha(install_id)[:8]}
        if changed or cached.get("install_id") != install_id:
            try:
                _write_json_atomic(path, {"install_id": install_id, "user": user, "salt_fp": fp,
                                          "email_checked": checked})
            except Exception:
                pass
        return result
    except Exception:
        return {"user": "", "install": ""}


# ---------------------------------------------------------------------------
# git helpers
# ---------------------------------------------------------------------------
class _ReportError(Exception):
    def __init__(self, category: str):
        super().__init__(category)
        self.category = category


_SSH_CFG_CACHE: dict = {}
KILL_DRAIN_TIMEOUT = 5


def _user_ssh_configured(env: dict, cwd: Path | str | None) -> bool:
    if env.get("GIT_SSH_COMMAND") or env.get("GIT_SSH"):
        return True
    where = str(cwd) if cwd and os.path.isdir(str(cwd)) else ""  # repo-local config if in a repo, else global
    if where not in _SSH_CFG_CACHE:  # one lookup per process / run
        cfg = False
        try:
            proc = subprocess.run(["git", "config", "--get", "core.sshCommand"], cwd=where or None, env=env,
                                  capture_output=True, timeout=LOCAL_GIT_TIMEOUT, stdin=subprocess.DEVNULL)
            cfg = bool(proc.stdout.strip())
        except Exception:
            cfg = False
        _SSH_CFG_CACHE[where] = cfg
    return _SSH_CFG_CACHE[where]


# Only these inherited GIT_* vars survive: everything else (GIT_DIR, GIT_WORK_TREE, GIT_AUTHOR_*,
# GIT_CONFIG_COUNT/KEY_n/VALUE_n/PARAMETERS ...) could redirect the upload at the user's own repo
# or inject identity into team commits.
_GIT_ENV_ALLOW = ("GIT_SSH", "GIT_SSH_COMMAND", "GIT_SSH_VARIANT", "GIT_CONFIG_GLOBAL",
                  "GIT_CONFIG_SYSTEM", "GIT_CONFIG_NOSYSTEM", "GIT_EXEC_PATH",
                  "GIT_SSL_CAINFO", "GIT_SSL_CAPATH", "GIT_SSL_CERT", "GIT_SSL_KEY",
                  "GIT_SSL_CERT_PASSWORD_PROTECTED", "GIT_SSL_NO_VERIFY",
                  "GIT_PROXY_COMMAND", "GIT_HTTP_PROXY_AUTHMETHOD", "GIT_HTTP_LOW_SPEED_LIMIT",
                  "GIT_HTTP_LOW_SPEED_TIME")


SAME_PGRP_ENV = "AICJ_TELEMETRY_SAME_PGRP"


def _git_env(cwd: Path | str | None = None, user: str = "") -> dict:
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    env.update({k: os.environ[k] for k in _GIT_ENV_ALLOW if k in os.environ})
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    if not _user_ssh_configured(env, cwd):
        env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes"
    if user:
        email = f"{user}@telemetry.invalid"
        env.update({"GIT_AUTHOR_NAME": "aicj-telemetry", "GIT_AUTHOR_EMAIL": email,
                    "GIT_COMMITTER_NAME": "aicj-telemetry", "GIT_COMMITTER_EMAIL": email})
    return env


def _user_git_env() -> dict:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    return env


def _kill_tree(proc, own_group: bool = True) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True,
                           timeout=10, stdin=subprocess.DEVNULL,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        elif own_group:
            import signal
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass


def _git(args: list[str], cwd: Path | str | None, timeout: int = GIT_TIMEOUT, user_env: bool = False):
    """Single choke point for every git subprocess. Raises subprocess/OS errors to the caller.

    user_env=True (read-only lookups on the user's own repo) keeps the inherited GIT_DIR/GIT_WORK_TREE;
    the default scrubbed env is the only one the collect-repo upload path may use."""
    kwargs: dict = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0) | \
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    own_group = os.name != "nt" and os.environ.get(SAME_PGRP_ENV) != "1"
    if own_group:
        # the uninstall runner sets SAME_PGRP_ENV so git stays in the runner's group and its killpg reaches us
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(
        ["git", *args], cwd=str(cwd) if cwd else None, env=_user_git_env() if user_env else _git_env(cwd, _prefix_user(args)), stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, **kwargs,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        # kill ssh/credential helpers too: they hold the pipes and would block a plain kill()
        _kill_tree(proc, own_group)
        try:
            stdout, stderr = proc.communicate(timeout=KILL_DRAIN_TIMEOUT)
        except subprocess.TimeoutExpired:
            stdout, stderr = b"", b""
        raise subprocess.TimeoutExpired(exc.cmd, timeout, output=stdout, stderr=stderr)
    except BaseException:
        _kill_tree(proc, own_group)
        raise
    return subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr)


def _out(proc) -> str:
    return proc.stdout.decode("utf-8", "replace").strip()


def _local_git(cwd: str, *args: str, user_env: bool = False) -> str | None:
    try:
        proc = _git(list(args), cwd, LOCAL_GIT_TIMEOUT, user_env=user_env)
    except Exception:
        return None
    return _out(proc) if proc.returncode == 0 else None


def _norm_origin(url: str) -> str:
    text = url.strip()
    m = re.match(r"^(?:[^@/\s]+@)?([^:/\s]+):(?!//)(.+)$", text)
    if m and "://" not in text:
        host, path = m.group(1), m.group(2)
    else:
        try:
            parts = urlsplit(text)
            host, path = parts.hostname or "", parts.path
        except ValueError:
            host, path = "", text
        if not host:
            return text.rstrip("/\\").removesuffix(".git").lower()
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return f"{host.lower()}/{path}"


# ---------------------------------------------------------------------------
# task_key / tasks.json
# ---------------------------------------------------------------------------
def _repo_task(cwd: str, salt: str) -> dict | None:
    if not isinstance(cwd, str) or not cwd.strip() or not os.path.isdir(cwd):
        return None
    out = _local_git(cwd, "rev-parse", "--show-toplevel", "--git-common-dir", user_env=True)
    if not out:
        return None
    lines = out.splitlines()
    if len(lines) < 2:
        return None
    top, common = lines[0].strip(), lines[1].strip()
    common_abs = _norm_path(os.path.join(cwd, common))
    branch = _local_git(cwd, "symbolic-ref", "--short", "-q", "HEAD", user_env=True) or "HEAD"
    origin = _local_git(cwd, "config", "--get", "remote.origin.url", user_env=True)
    repo, task = _keys(salt, common_abs, branch, origin)
    return {"repo": repo, "task": task, "repo_path": top, "branch": branch}


def _norm_path(path: str) -> str:
    try:
        return os.path.realpath(path.replace("\\", "/") if os.name != "nt" else path).replace("\\", "/")
    except (ValueError, OSError):  # NUL or otherwise invalid path: keep a stable literal
        return path.replace("\\", "/")


def _keys(salt: str, common_abs: str, branch: str, origin: str | None) -> tuple[str, str]:
    """The one place (repo, task) hashes are derived; collect-time converters must use it too."""
    if origin:
        repo_name = _norm_origin(origin)
    else:
        repo_name = os.path.basename(os.path.dirname(common_abs)) if os.path.basename(common_abs) == ".git" \
            else os.path.basename(common_abs)
    return _sha(salt, repo_name)[:12], _sha(salt, common_abs, "\n", branch)[:12]


def task_key(claude_dir: Path, cwd: str, team: dict) -> tuple[str, str] | None:
    try:
        info = _repo_task(cwd, str(team["salt"]))
        if not info:
            return None
        _register_task(claude_dir, info)
        return info["repo"], info["task"]
    except Exception:
        return None


def _register_task(claude_dir: Path, info: dict) -> None:
    try:
        path = _tdir(claude_dir) / "tasks.json"
        try:
            tasks = _read_json(path)
            if not isinstance(tasks, dict):
                tasks = {}
        except Exception:
            tasks = {}
        now = _now()
        cur = tasks.get(info["task"])
        if isinstance(cur, dict):
            seen = _parse_iso(cur.get("last_seen"))
            same = cur.get("repo_path") == info["repo_path"] and cur.get("branch") == info["branch"]
            if same and seen and (now - seen).total_seconds() < 3600:
                return
        tasks[info["task"]] = {"repo": info["repo"], "repo_path": info["repo_path"],
                               "branch": info["branch"], "last_seen": _iso(now)}
        # tmp + os.replace: a concurrent register can lose an entry but never corrupts the file
        _write_json_atomic(path, tasks)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# emit
# ---------------------------------------------------------------------------
def _build(team: dict, ident: dict, etype: str, data: dict, ts: str,
           event_id: str, repo_task: tuple[str, str] | None, version: str | None) -> dict | None:
    event: dict = {
        "schema": SCHEMA_VERSION, "type": etype, "ts": ts, "event_id": event_id,
        "team": team["team_id"], "user": ident["user"], "install": ident["install"],
        "platform": _platform(),
    }
    if repo_task:
        event["repo"], event["task"] = repo_task
    if version:
        event["version"] = version
    for key, value in (data or {}).items():
        if key not in ENVELOPE:  # envelope is set by the library, never by the caller's data
            event[key] = value
    salt = str(team.get("salt", ""))
    if etype == "verify_probe":
        for key in ("layers", "failed_layers"):
            if isinstance(event.get(key), (list, tuple)):
                event[key] = [_vocab_or_hash(salt, v, KNOWN_LAYERS) for v in event[key]]
    elif etype == "hook_gate" and "rule_tag" in event:
        event["rule_tag"] = _vocab_or_hash(salt, event["rule_tag"], KNOWN_RULE_TAGS)
    elif etype == "skill_use" and "skill" in event:
        event["skill"] = skill_token(salt, event["skill"])
    elif etype == "correction" and "after_skill" in event:
        event["after_skill"] = skill_token(salt, event["after_skill"])
    return sanitize(event)


def _spool_write(claude_dir: Path, event: dict) -> None:
    _append_lines(_tdir(claude_dir) / "spool" / (event["ts"][:10] + ".jsonl"), [_dumps(event)])


def emit(claude_dir: Path, type: str, data: dict, *, cwd: str | None = None,
         version: str | None = None, event_id: str | None = None) -> bool:
    """Spool one event. A caller-supplied event_id (hex hash) is kept; otherwise a uuid4 is used."""
    try:
        team = load_team(claude_dir)
        if team is None or type not in SCHEMA:
            return False
        ident = identity(claude_dir, team)
        if not ident["user"]:
            return False
        rt = task_key(claude_dir, cwd, team) if cwd else None
        if not (isinstance(event_id, str) and _HASH_RE.fullmatch(event_id)):
            event_id = uuid.uuid4().hex
        event = _build(team, ident, type, data if isinstance(data, dict) else {},
                       _iso(_now()), event_id, rt, version)
        if event is None:
            return False
        _spool_write(claude_dir, event)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# state / lock
# ---------------------------------------------------------------------------
def _load_state(claude_dir: Path) -> dict:
    try:
        data = _read_json(_tdir(claude_dir) / "state.json")
        state = data if isinstance(data, dict) else {}
    except Exception:
        state = {}
    for key in ("offsets", "upload_offsets", "snapshots", "review_ts"):
        if not isinstance(state.get(key), dict):
            state[key] = {}
    if not isinstance(state.get("cards_seen"), list):
        state["cards_seen"] = []
    return state


def _save_state(claude_dir: Path, state: dict) -> None:
    _write_json_atomic(_tdir(claude_dir) / "state.json", state)


def _acquire_lock(tdir: Path) -> bool:
    tdir.mkdir(parents=True, exist_ok=True)
    path = tdir / "report.lock"
    for _ in range(3):
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                age = time.time() - path.stat().st_mtime
            except FileNotFoundError:
                continue
            if age <= LOCK_STALE_SECONDS:
                return False
            # steal atomically: whoever renames the stale file away owns the right to re-create it
            grave = tdir / ("report.lock.stale." + uuid.uuid4().hex[:8])
            try:
                os.replace(str(path), str(grave))
            except OSError:
                continue  # another stealer won (or the lock was released); retry O_EXCL
            try:
                moved_age = time.time() - grave.stat().st_mtime
            except OSError:
                moved_age = LOCK_STALE_SECONDS + 1
            if moved_age <= LOCK_STALE_SECONDS:  # we grabbed a lock a faster stealer had just re-created
                try:
                    os.link(str(grave), str(path))
                except OSError:
                    pass
                try:
                    grave.unlink()
                except OSError:
                    pass
                return False
            try:
                grave.unlink()
            except OSError:
                pass
            continue
        try:
            os.write(fd, str(os.getpid()).encode())
        finally:
            os.close(fd)
        return True
    return False


def _release_lock(tdir: Path) -> None:
    try:
        (tdir / "report.lock").unlink()
    except OSError:
        pass


# ---------------------------------------------------------------------------
# collect: local source converters
# ---------------------------------------------------------------------------
class _Ctx:
    def __init__(self, claude_dir: Path, team: dict, ident: dict):
        self.claude_dir = Path(claude_dir)
        self.team = team
        self.salt = team["salt"]
        self.ident = ident
        self._rt: dict = {}

    def event_id(self, source: str, raw: str, locator: object = "") -> str:
        # install-independent: reinstalling or wiping telemetry state must not re-key old rows
        return _sha(self.ident["user"], "\n", source, "\n", str(locator), "\n", raw)[:32]

    def repo_task(self, cwd: object) -> tuple[str, str] | None:
        if not isinstance(cwd, str) or not cwd:
            return None
        if cwd not in self._rt:
            self._rt[cwd] = task_key(self.claude_dir, cwd, self.team)
        return self._rt[cwd]

    def build(self, etype: str, data: dict, ts: str, source: str, raw: str,
              rt: tuple[str, str] | None, locator: object = "") -> dict | None:
        return _build(self.team, self.ident, etype, data, ts,
                      self.event_id(source, raw, locator), rt, None)


def _hash_name(ctx: _Ctx, value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return _sha(ctx.salt, "\n", value.strip())[:12]


def _int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) and value >= 0 else None


def _conv_gate_event(ctx: _Ctx, row: dict, raw: str, state: dict, aux: dict) -> dict | None:
    ts = _norm_ts(row.get("ts"))
    if not ts:
        return None
    rule = row.get("rule")
    rule_line = rule.splitlines()[0].strip() if isinstance(rule, str) and rule.strip() else ""
    data: dict = {"hook": row.get("hook"), "event": row.get("event"),
                  "mode": row.get("mode") if row.get("mode") in ("warn", "block") else "unknown",
                  "tool": row.get("tool")}
    m = re.match(r"\s*\[([^\]]+)\]", rule_line)
    if m:
        data["rule_tag"] = m.group(1).strip()
    if rule_line:
        data["rule_hash"] = _hash_name(ctx, rule_line)
    return ctx.build("hook_gate", data, ts, "gate-events", raw, ctx.repo_task(row.get("cwd")), aux.get("locator", ""))


def _gate_task(ctx: _Ctx, row: dict) -> tuple[str, str] | None:
    branch = row.get("branch")
    if not isinstance(branch, str) or not branch.strip():
        return None
    branch = branch.strip()
    common = row.get("common_dir")
    if isinstance(common, str) and common.strip():
        common_abs = _norm_path(common.strip())
    else:
        root = row.get("repo_root")
        if not isinstance(root, str) or not root.strip():
            return None
        root = root.strip()
        out = _local_git(root, "rev-parse", "--git-common-dir") if os.path.isdir(root) else None
        if out:
            common_abs = _norm_path(os.path.join(root, out.splitlines()[0].strip()))
        else:  # repo no longer on disk: stable per-repo/branch identity from the recorded path
            root_norm = root.replace("\\", "/").rstrip("/")
            return (_sha(ctx.salt, os.path.basename(root_norm))[:12],
                    _sha(ctx.salt, root_norm, "\n", branch)[:12])
    origin = _local_git(common_abs, "config", "--get", "remote.origin.url") \
        if os.path.isdir(common_abs) else None
    return _keys(ctx.salt, common_abs, branch, origin)


def _conv_review(ctx: _Ctx, row: dict, raw: str, state: dict, aux: dict) -> dict | None:
    ts = _norm_ts(row.get("ts"))
    kind = row.get("kind")
    if not ts or kind not in ("gate", "outcome"):
        return None
    rt = _gate_task(ctx, row)
    data: dict = {"review_gate": row.get("review_gate")}
    if kind == "gate":
        data["kind"] = "scope"
        files = row.get("files")
        data["file_count"] = len(files) if isinstance(files, list) else None
        data["changed_lines"] = _int(row.get("lines"))
        signals = row.get("risk_signals")
        if isinstance(signals, list):
            data["risk_signals"] = signals
    else:
        data["kind"] = "outcome"
        for key in ("reviewers", "findings", "confirmed"):
            data[key] = _int(row.get(key))
        if rt:
            prev = state["review_ts"].get(rt[1])
            if not prev or ts > prev:
                state["review_ts"][rt[1]] = ts
    return ctx.build("review", data, ts, "code-review-gates", raw, rt, aux.get("locator", ""))


# ---------------------------------------------------------------------------
# skill-log: local-only association of skill calls with later corrections (never uploaded)
# ---------------------------------------------------------------------------
def _skill_log_path(claude_dir: Path) -> Path:
    return _tdir(claude_dir) / "skill-log.jsonl"


def _session_key(salt: str, session_id: object) -> str:
    if not isinstance(session_id, str) or not session_id.strip():
        return ""
    return _sha(salt, "\n", session_id.strip())[:16]


def record_skill(claude_dir: Path, session_id: object, skill: object, ts: str | None = None) -> bool:
    """Append (session hash, ts, skill token) to the local skill-log. Never raises."""
    try:
        team = load_team(claude_dir)
        if team is None:
            return False
        salt = str(team.get("salt", ""))
        key = _session_key(salt, session_id)
        token = skill_token(salt, skill)
        if not key or not token:
            return False
        row = {"s": key, "ts": ts or _iso(_now()), "skill": token}
        _append_lines(_skill_log_path(claude_dir), [_dumps(row)])
        return True
    except Exception:
        return False


def _load_skill_rows(claude_dir: Path) -> list[dict]:
    path = _skill_log_path(claude_dir)
    if not path.is_file():
        return []
    rows: list[dict] = []
    for ln in path.read_bytes().decode("utf-8", "replace").split("\n"):
        row = _parse_row(ln.strip("\r"))
        if row and isinstance(row.get("s"), str) and isinstance(row.get("skill"), str):
            when = _parse_iso(row.get("ts"))
            if when is not None:
                rows.append({"s": row["s"], "when": when, "skill": row["skill"]})
    return rows


def _prune_skill_log(claude_dir: Path, now: _dt.datetime | None = None) -> None:
    path = _skill_log_path(claude_dir)
    if not path.is_file():
        return
    cutoff = (now or _now()) - _dt.timedelta(days=SKILL_LOG_DAYS)
    keep: list[str] = []
    dropped = False
    for ln in path.read_bytes().decode("utf-8", "replace").split("\n"):
        ln = ln.strip("\r")
        if not ln.strip():
            continue
        row = _parse_row(ln)
        when = _parse_iso(row.get("ts")) if row else None
        if when is None or when < cutoff:
            dropped = True
            continue
        keep.append(ln)
    if dropped:
        tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
        tmp.write_text("".join(ln + "\n" for ln in keep), encoding="utf-8", newline="\n")
        os.replace(tmp, path)


def _skill_before(rows: object, salt: str, session_id: object, ts: str) -> str | None:
    """Latest skill token logged for the session within AFTER_SKILL_WINDOW_SECONDS before ts."""
    key = _session_key(salt, session_id)
    when = _parse_iso(ts)
    if not key or when is None or not rows:
        return None
    best = None
    for r in list(rows):
        if r["s"] != key:
            continue
        delta = (when - r["when"]).total_seconds()
        if 0 <= delta <= AFTER_SKILL_WINDOW_SECONDS and (best is None or r["when"] >= best["when"]):
            best = r
    return best["skill"] if best else None


def _conv_correction(ctx: _Ctx, row: dict, raw: str, state: dict, aux: dict) -> dict | None:
    ts = _norm_ts(row.get("ts"))
    if not ts:
        return None
    try:
        cause = classify_rework(row.get("prompt"))
    except Exception:
        cause = "G"
    data: dict = {"cause": cause, "cause_rules": REWORK_RULES_VERSION}
    try:  # association is best effort: a failure here must not lose the correction itself
        skill = _skill_before(aux.get("skill_rows"), ctx.salt, row.get("session_id"), ts)
        if skill:
            data["after_skill"] = skill
    except Exception:
        pass
    return ctx.build("correction", data, ts, "corrections", raw, ctx.repo_task(row.get("cwd")), aux.get("locator", ""))


def _conv_recall(ctx: _Ctx, row: dict, raw: str, state: dict, aux: dict) -> dict | None:
    ts = _norm_ts(row.get("ts"))
    card = row.get("card")
    if not ts or not isinstance(card, str):
        return None
    data = {"card": _hash_name(ctx, card), "level": aux.get("levels", {}).get(card)}
    return ctx.build("lesson_recall", data, ts, "recalls", raw, ctx.repo_task(row.get("cwd")), aux.get("locator", ""))


def _conv_card(ctx: _Ctx, row: dict, raw: str, state: dict, aux: dict) -> dict | None:
    cid = row.get("id")
    if not isinstance(cid, str) or not cid:
        return None
    scope = row.get("scope", "global")
    data = {
        "card": _hash_name(ctx, cid), "level": row.get("level"),
        "scope_kind": "global" if scope in (None, "", "global") else "path",
        "status": row.get("status"), "created": row.get("created"),
    }
    return ctx.build("lesson_card", data, _iso(_now()), "cards", raw, None, cid)


_OFFSET_SOURCES = (
    # (state key, relative dir, file, converter)
    ("gate-events", "review-logs", "gate-events.jsonl", _conv_gate_event),
    ("code-review-gates", "review-logs", "code-review-gates.jsonl", _conv_review),
    ("corrections", "lessons", "corrections.jsonl", _conv_correction),
    ("recalls", "lessons", "recalls.jsonl", _conv_recall),
)


def _read_new_lines(path: Path, offset: int) -> tuple[list[tuple[int, str]], int]:
    """Complete (byte_offset, line) pairs after `offset` (CRLF tolerant) and the new offset.
    Truncated file restarts."""
    size = path.stat().st_size
    if offset > size or offset < 0:
        offset = 0
    with open(path, "rb") as fh:
        fh.seek(offset)
        chunk = fh.read()
    end = chunk.rfind(b"\n")
    if end < 0:
        return [], offset
    body = chunk[: end + 1]
    lines: list[tuple[int, str]] = []
    pos = offset
    for piece in body.split(b"\n")[:-1]:
        line = piece.decode("utf-8", "replace").strip("\r")
        if line.strip():
            lines.append((pos, line))
        pos += len(piece) + 1
    return lines, offset + len(body)


def _parse_row(raw: str) -> dict | None:
    try:
        row = json.loads(raw)
    except ValueError:
        return None
    return row if isinstance(row, dict) else None


def _collect_locked(claude_dir: Path, team: dict) -> int:
    ident = identity(claude_dir, team)
    if not ident["user"]:
        return 0
    ctx = _Ctx(claude_dir, team, ident)
    state = _load_state(claude_dir)
    base = Path(claude_dir) / "aicj"
    added = 0
    aux: dict = {"levels": {}, "skill_rows": []}
    try:
        _prune_skill_log(claude_dir)
        aux["skill_rows"] = _load_skill_rows(claude_dir)
    except Exception:
        pass

    cards_path = base / "lessons" / "cards.jsonl"
    card_lines: list[str] = []
    if cards_path.is_file():
        try:
            card_lines = [ln.strip("\r") for ln in cards_path.read_bytes().decode("utf-8", "replace").split("\n")
                          if ln.strip()]
        except OSError:
            card_lines = []
        for ln in card_lines:
            row = _parse_row(ln)
            if row and isinstance(row.get("id"), str) and isinstance(row.get("level"), str):
                aux["levels"][row["id"]] = row["level"]

    for key, sub, fname, conv in _OFFSET_SOURCES:
        path = base / sub / fname
        if not path.is_file():
            continue
        try:
            lines, new_off = _read_new_lines(path, int(state["offsets"].get(key, 0) or 0))
        except OSError:
            continue
        for line_off, raw in lines:
            try:  # one poison row must neither abort collect nor block its offset
                row = _parse_row(raw)
                if row is None:
                    continue
                aux["locator"] = line_off
                event = conv(ctx, row, raw, state, aux)
                if event is not None:
                    _spool_write(claude_dir, event)
                    added += 1
            except Exception:
                continue
        state["offsets"][key] = new_off

    # cards.jsonl is rewritten atomically by the lesson tool, so byte offsets do not apply:
    # a row is new when its text has not been seen before.
    # Keys are user-independent (card id + row text) so a user-hash change does not re-spool cards.
    # Older state held user-bound event ids: those never match, so cards are re-keyed (re-emitted
    # once with their original event ids, which the merge step de-duplicates) on the first run.
    seen = set(state["cards_seen"])
    current: list[str] = []
    for raw in card_lines:
        try:
            row = _parse_row(raw)
            cid = row.get("id") if row else ""
            ckey = _sha("card", str(cid), "\n", raw)[:32]
            current.append(ckey)
            if ckey in seen:
                continue
            event = _conv_card(ctx, row, raw, state, aux) if row else None
            if event is not None:
                _spool_write(claude_dir, event)
                added += 1
        except Exception:
            continue
    state["cards_seen"] = current

    try:
        added += _collect_snapshots(claude_dir, ctx, state)
    except Exception:
        pass
    _save_state(claude_dir, state)
    return added


def _fork_point(repo: str, branch: str) -> str | None:
    fork = _local_git(repo, "config", "--get", f"branch.{branch}.fork-oid")
    if fork:
        return fork
    baseline = _local_git(repo, "config", "--get", f"branch.{branch}.baseline")
    candidates = [baseline] if baseline else ["main", "master"]
    for cand in candidates:
        if cand and cand != branch and _local_git(repo, "rev-parse", "--verify", "-q", cand):
            base = _local_git(repo, "merge-base", cand, branch)
            if base:
                return base
    return None


def _collect_snapshots(claude_dir: Path, ctx: _Ctx, state: dict) -> int:
    try:
        tasks = _read_json(_tdir(claude_dir) / "tasks.json")
    except Exception:
        return 0
    if not isinstance(tasks, dict):
        return 0
    now = _now()
    today = now.strftime("%Y-%m-%d")
    added = 0
    for task, info in list(tasks.items()):
        try:
            if not isinstance(info, dict) or state["snapshots"].get(task) == today:
                continue
            seen = _parse_iso(info.get("last_seen"))
            if not seen or (now - seen).days > SNAPSHOT_WINDOW_DAYS:
                continue
            repo, branch = info.get("repo_path"), info.get("branch")
            if not isinstance(repo, str) or not isinstance(branch, str) or not os.path.isdir(repo):
                continue
            if not _local_git(repo, "rev-parse", "--verify", "-q", branch):
                continue
            fork = _fork_point(repo, branch)
            if not fork:
                continue
            rng = f"{fork}..{branch}"
            commits = int(_local_git(repo, "rev-list", "--count", rng) or 0)
            if commits == 0:
                continue
            names = _local_git(repo, "log", "--name-only", "--format=%x00", rng) or ""
            per_file: dict[str, int] = {}
            for block in names.split("\x00"):
                for fname in {ln.strip() for ln in block.splitlines() if ln.strip()}:
                    per_file[fname] = per_file.get(fname, 0) + 1
            after = commits
            review_ts = state["review_ts"].get(task)
            if review_ts:
                after = int(_local_git(repo, "rev-list", "--count", f"--since={review_ts}", rng) or 0)
            data = {
                "commits": commits, "files_changed": len(per_file),
                "files_touched_repeatedly": sum(1 for n in per_file.values() if n >= 2),
                "commits_after_review": after if review_ts else 0,
            }
            raw = f"{task}\n{today}"
            rt = (info["repo"], task) if isinstance(info.get("repo"), str) else None
            event = ctx.build("task_snapshot", data, _iso(now), "task-snapshot", raw, rt)
            if event is not None:
                _spool_write(claude_dir, event)
                state["snapshots"][task] = today
                added += 1
        except Exception:
            continue
    return added


def collect(claude_dir: Path) -> int:
    try:
        team = load_team(claude_dir)
        if team is None:
            return 0
        tdir = _tdir(claude_dir)
        if not _acquire_lock(tdir):
            return 0
        try:
            return _collect_locked(claude_dir, team)
        finally:
            _release_lock(tdir)
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# run_report
# ---------------------------------------------------------------------------
def _result(status_: str, reason: str, uploaded: int = 0) -> dict:
    return {"status": status_, "reason": reason, "uploaded": uploaded}


def _prefix_user(prefix: list[str]) -> str:
    for item in prefix:
        if item.startswith("user.email=") and item.endswith("@telemetry.invalid"):
            return item[len("user.email="):-len("@telemetry.invalid")]
    return ""


def _run(repo: Path | str | None, prefix: list[str], args: list[str], *, check: bool, category: str):
    try:
        proc = _git([*prefix, *args], repo)
    except subprocess.TimeoutExpired:
        raise _ReportError("timeout")
    except FileNotFoundError:
        raise _ReportError("git_missing")
    except OSError:
        raise _ReportError("io")
    if check and proc.returncode != 0:
        raise _ReportError(category)
    return proc


def _git_prefix(tdir: Path, user: str) -> list[str]:
    return [
        "-c", f"core.hooksPath={tdir / 'no-hooks'}",
        "-c", "user.name=aicj-telemetry", "-c", f"user.email={user}@telemetry.invalid",
        "-c", "commit.gpgsign=false",
    ]


def _has_head(repo: Path, prefix: list[str]) -> bool:
    return _run(repo, prefix, ["rev-parse", "--verify", "-q", "HEAD"], check=False, category="git").returncode == 0


def _clone(tdir: Path, prefix: list[str], team: dict, repo: Path) -> None:
    branch = team["branch"]
    proc = _run(tdir, prefix, ["clone", "--depth", "1", "-b", branch, "--", team["collect_repo"], str(repo)],
                check=False, category="clone")
    if proc.returncode == 0:
        return
    shutil.rmtree(repo, ignore_errors=True)
    # An empty remote has no branch to check out: clone plainly and start the branch locally.
    _run(tdir, prefix, ["clone", "--", team["collect_repo"], str(repo)], check=True, category="clone")
    if not _has_head(repo, prefix):
        _run(repo, prefix, ["checkout", "-B", branch], check=True, category="clone")
    else:
        shutil.rmtree(repo, ignore_errors=True)
        raise _ReportError("clone")  # remote has commits but not the configured branch


def _reset_to_remote(repo: Path, prefix: list[str], branch: str) -> None:
    _run(repo, prefix, ["rebase", "--abort"], check=False, category="git")
    heads = _run(repo, prefix, ["ls-remote", "--heads", "--", "origin", branch], check=True, category="network")
    if not (heads.stdout or "").strip():
        return  # empty remote: nothing to reset to, local commits are pushed as the first history
    _run(repo, prefix, ["fetch", "--", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}"],
         check=True, category="network")
    _run(repo, prefix, ["reset", "--hard", f"origin/{branch}"], check=True, category="reset")


def _prepare_repo(tdir: Path, prefix: list[str], team: dict, repo: Path) -> None:
    branch = team["branch"]
    if not (repo / ".git").exists():
        shutil.rmtree(repo, ignore_errors=True)
        _clone(tdir, prefix, team, repo)
        return
    _run(repo, prefix, ["rebase", "--abort"], check=False, category="git")
    if _has_head(repo, prefix):
        _run(repo, prefix, ["reset", "--hard", "HEAD"], check=False, category="git")
    _run(repo, prefix, ["clean", "-fdq"], check=False, category="git")
    _run(repo, prefix, ["remote", "set-url", "origin", team["collect_repo"]], check=False, category="git")
    if not _has_head(repo, prefix):
        return
    proc = _run(repo, prefix, ["pull", "--rebase", "--", "origin", branch], check=False, category="pull")
    if proc.returncode != 0:
        _reset_to_remote(repo, prefix, branch)


def _merge_spool(claude_dir: Path, repo: Path, ident: dict, state: dict) -> tuple[int, dict]:
    """Append un-uploaded spool events to <date>/<user>-<install>.jsonl, skipping known event_ids."""
    spool = _tdir(claude_dir) / "spool"
    new_offsets: dict = {}
    by_date: dict[str, list[dict]] = {}
    if spool.is_dir():
        for f in sorted(spool.glob("*.jsonl")):
            offset = state["upload_offsets"].get(f.name, 0)
            lines, new_off = _read_new_lines(f, offset if isinstance(offset, int) else 0)
            new_offsets[f.name] = new_off
            for _off, raw in lines:
                row = _parse_row(raw)
                clean = sanitize(row) if row else None
                if clean:
                    by_date.setdefault(clean["ts"][:10], []).append(clean)
    merged = 0
    for date, events in sorted(by_date.items()):
        target = repo / date / f"{ident['user']}-{ident['install']}.jsonl"
        known: set[str] = set()
        needs_nl = False
        if target.is_file():
            data = target.read_bytes()
            needs_nl = bool(data) and not data.endswith(b"\n")
            for ln in data.decode("utf-8", "replace").split("\n"):
                row = _parse_row(ln.strip("\r"))
                if row and isinstance(row.get("event_id"), str):
                    known.add(row["event_id"])
        fresh: list[str] = []
        for ev in events:
            if ev["event_id"] in known:
                continue
            known.add(ev["event_id"])
            fresh.append(_dumps(ev))
        if fresh:
            if needs_nl:
                _append_lines(target, [""])
            _append_lines(target, fresh)
            merged += len(fresh)
    return merged, new_offsets


def _commit(repo: Path, prefix: list[str], count: int) -> None:
    _run(repo, prefix, ["add", "-A"], check=True, category="git")
    staged = _run(repo, prefix, ["diff", "--cached", "--quiet"], check=False, category="git")
    if staged.returncode != 0:
        _run(repo, prefix, ["commit", "-q", "-m", f"telemetry: {count} events"], check=True, category="commit")


def _push(claude_dir: Path, repo: Path, prefix: list[str], team: dict, ident: dict,
          state: dict) -> tuple[int, dict]:
    """Commit + push with up to 3 attempts. Returns (events merged, new spool offsets)."""
    branch = team["branch"]
    merged, offsets = _merge_spool(claude_dir, repo, ident, state)
    _commit(repo, prefix, merged)
    if not _has_head(repo, prefix):
        return merged, offsets
    for attempt in range(PUSH_ATTEMPTS):
        proc = _run(repo, prefix, ["push", "origin", f"HEAD:refs/heads/{branch}"], check=False, category="push")
        if proc.returncode == 0:
            return merged, offsets
        if attempt == PUSH_ATTEMPTS - 1:
            break
        pull = _run(repo, prefix, ["pull", "--rebase", "--", "origin", branch], check=False, category="pull")
        if pull.returncode != 0:
            _reset_to_remote(repo, prefix, branch)
            merged, offsets = _merge_spool(claude_dir, repo, ident, state)
            _commit(repo, prefix, merged)
    raise _ReportError("push")


def _enforce_cap(claude_dir: Path, team: dict, state: dict) -> None:
    spool = _tdir(claude_dir) / "spool"
    if not spool.is_dir():
        return
    cap = int(float(team["spool_max_mb"]) * 1024 * 1024)
    files = sorted(spool.glob("*.jsonl"))
    sizes = {}
    for f in files:
        try:
            sizes[f] = f.stat().st_size
        except OSError:
            sizes[f] = 0
    total = sum(sizes.values())
    for f in files:
        if total <= cap:
            break
        try:
            f.unlink()
        except OSError:
            continue
        total -= sizes[f]
        state["upload_offsets"].pop(f.name, None)


def run_report(claude_dir: Path, *, force: bool = False) -> dict:
    try:
        team = load_team(claude_dir)
        if team is None:
            return _result("skipped", "no_team")
        tdir = _tdir(claude_dir)
        if not _acquire_lock(tdir):
            return _result("skipped", "locked")
        try:
            return _report_locked(claude_dir, team, tdir, force)
        finally:
            _release_lock(tdir)
    except Exception:
        return _result("failed", "internal")


def _is_due(team: dict, state: dict, now: _dt.datetime) -> bool:
    success = _parse_iso(state.get("last_success"))
    if success and (now - success).total_seconds() < team["upload_interval_hours"] * 3600:
        return False
    attempt = _parse_iso(state.get("last_attempt"))
    if attempt and (now - attempt).total_seconds() < ATTEMPT_MIN_INTERVAL_SECONDS:
        return False
    return True


def due(claude_dir: Path) -> bool:
    """True when a report is due. Reads team.json and state.json only, never the spool."""
    try:
        team = load_team(claude_dir)
        if team is None:
            return False
        return _is_due(team, _load_state(claude_dir), _now())
    except Exception:
        return False


def _report_locked(claude_dir: Path, team: dict, tdir: Path, force: bool) -> dict:
    state = _load_state(claude_dir)
    now = _now()
    if not force and not _is_due(team, state, now):
        return _result("skipped", "throttled")
    state["last_attempt"] = _iso(now)
    try:
        _save_state(claude_dir, state)
    except Exception:
        pass
    ident = identity(claude_dir, team)
    if not ident["user"]:
        return _result("failed", "identity")
    error = None
    uploaded = 0
    try:
        state = _load_state(claude_dir)
        state["last_attempt"] = _iso(now)
        try:
            _collect_locked(claude_dir, team)
            state = _load_state(claude_dir)  # collect persisted offsets; keep them
            state["last_attempt"] = _iso(now)
        except Exception:
            pass  # a broken local source must not block uploading what is already in the spool
        prefix = _git_prefix(tdir, ident["user"])
        repo = tdir / "collect-repo"
        _prepare_repo(tdir, prefix, team, repo)
        uploaded, offsets = _push(claude_dir, repo, prefix, team, ident, state)
        state["upload_offsets"].update(offsets)
        state["last_success"] = _iso(_now())
        state["last_error"] = None
    except _ReportError as exc:
        error = exc.category
    except Exception:
        error = "internal"
    if error:
        state["last_error"] = error
    try:
        _enforce_cap(claude_dir, team, state)
        _save_state(claude_dir, state)
    except Exception:
        pass
    if error:
        return _result("failed", error)
    return _result("ok", "uploaded" if uploaded else "nothing_to_upload", uploaded)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------
def status(claude_dir: Path) -> dict:
    try:
        team = load_team(claude_dir)
        if team is None:
            return {"enabled": False}
        tdir = _tdir(claude_dir)
        state = _load_state(claude_dir)
        info: dict = {
            "enabled": True, "team_id": team["team_id"], "branch": team["branch"],
            "upload_interval_hours": team["upload_interval_hours"],
            "last_attempt": state.get("last_attempt"), "last_success": state.get("last_success"),
            "last_error": state.get("last_error"),
        }
        try:
            ident = _read_json(tdir / "identity.json")
            if isinstance(ident, dict):
                info["user"] = ident.get("user")
                info["anonymous"] = str(ident.get("user", "")).startswith("anon-")
                info["install"] = _sha(str(ident.get("install_id", "")))[:8]
        except Exception:
            pass
        spool_files = 0
        spool_bytes = 0
        pending = 0
        spool = tdir / "spool"
        if spool.is_dir():
            for f in spool.glob("*.jsonl"):
                try:
                    size = f.stat().st_size
                    spool_files += 1
                    spool_bytes += size
                    done = state["upload_offsets"].get(f.name, 0)
                    if isinstance(done, int) and done < size:
                        pending += f.read_bytes()[done:].count(b"\n")
                except OSError:
                    continue
        info.update({"spool_files": spool_files, "spool_bytes": spool_bytes, "pending": pending})
        return info
    except Exception:
        return {"enabled": False, "error": "status_failed"}
