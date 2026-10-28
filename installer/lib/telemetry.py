"""Team telemetry integration for the installer: team.json fetch/validate and best-effort events.

Everything that emits is best-effort: a missing library, missing team.json or any
exception is swallowed so telemetry can never change installer behavior.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import urllib.request
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from .util import UserError

TEAM_REL = ".claude/aicj/team.json"
LIB_REL = "hooks/aicj/aicj_telemetry.py"
FETCH_TIMEOUT = 15
MAX_BYTES = 64 * 1024
_TOKEN_RE = re.compile(r"[A-Za-z0-9._:+-]{1,64}")


def _safe_ref(name: str) -> bool:
    """Mirror of aicj_telemetry._safe_ref (the installer must not import hook code): keep in sync."""
    return (bool(name) and not name.startswith(("-", "/")) and not name.endswith(("/", ".", ".lock"))
            and ".." not in name and "//" not in name and "@{" not in name
            and not re.search(r"[\s~^:?*\[\\\x00-\x1f\x7f]", name))


def _read_spec(spec: str) -> bytes:
    text = spec.strip()
    if not text:
        raise UserError("--team-config is empty")
    lowered = text.casefold()
    try:
        if lowered.startswith(("http://", "https://")):
            with urllib.request.urlopen(text, timeout=FETCH_TIMEOUT) as resp:  # noqa: S310 - scheme checked above
                return resp.read(MAX_BYTES + 1)
        if lowered.startswith("file://"):
            parsed = urlparse(text)
            path = Path(url2pathname(unquote(parsed.path)))
            if parsed.netloc and parsed.netloc != "localhost":
                path = Path(f"//{parsed.netloc}{path}")
        else:
            path = Path(text).expanduser()
        return path.read_bytes()
    except UserError:
        raise
    except Exception as exc:
        raise UserError(f"--team-config: cannot read {_safe_name(text)}: {type(exc).__name__}") from exc


def _safe_name(spec: str) -> str:
    parsed = urlparse(spec)
    return f"{parsed.scheme}://{parsed.netloc}/..." if parsed.scheme in ("http", "https") else spec


def validate_team_bytes(data: bytes, label: str = "team config") -> bytes:
    """Return normalized bytes (utf-8 JSON + newline) or raise UserError."""
    if len(data) > MAX_BYTES:
        raise UserError(f"{label}: larger than {MAX_BYTES} bytes")
    try:
        raw = json.loads(data.decode("utf-8-sig"))
    except (UnicodeError, ValueError) as exc:
        raise UserError(f"{label}: not valid UTF-8 JSON") from exc
    if not isinstance(raw, dict):
        raise UserError(f"{label}: top level must be a JSON object")
    team_id = raw.get("team_id")
    if not isinstance(team_id, str) or not _TOKEN_RE.fullmatch(team_id):
        raise UserError(f"{label}: team_id must match [A-Za-z0-9._:+-]{{1,64}}")
    for key in ("collect_repo", "salt"):
        value = raw.get(key)
        if not isinstance(value, str) or not value.strip():
            raise UserError(f"{label}: {key} is required and must be a non-blank string")
    if raw["collect_repo"].strip().startswith("-"):
        raise UserError(f"{label}: collect_repo must not start with '-'")
    branch = raw.get("branch")
    if branch is not None:
        if not isinstance(branch, str) or (branch.strip() and not _safe_ref(branch.strip())):
            raise UserError(f"{label}: branch is not a safe git ref name")
    return (json.dumps(raw, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def fetch_team_config(spec: str) -> bytes:
    return validate_team_bytes(_read_spec(spec), "--team-config")


def existing_team_bytes(home: Path) -> bytes | None:
    """Current team.json content when it is a regular, valid file; None otherwise."""
    path = home / TEAM_REL
    try:
        if not path.is_file() or path.is_symlink():
            return None
        return validate_team_bytes(path.read_bytes(), "existing team.json")
    except (OSError, UserError):
        return None


def has_team(home: Path) -> bool:
    return (home / TEAM_REL).is_file()


def load_library(*candidates: Path):
    """Load aicj_telemetry from the first candidate file that exists; None when absent/broken."""
    for path in candidates:
        try:
            if not path.is_file():
                continue
            spec = importlib.util.spec_from_file_location("aicj_telemetry", path)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            sys.modules.setdefault("aicj_telemetry", module)
            spec.loader.exec_module(module)
            return module
        except Exception:
            continue
    return None


def _lib(home: Path, source: Path | None):
    candidates = [home / ".claude" / LIB_REL]
    if source is not None:
        candidates.append(source / "core" / "hooks" / "aicj_telemetry.py")
    return load_library(*candidates)


def emit_install(home: Path, source: Path | None, command: str, ok: bool, *,
                 error_kind: str = "", options=None, failed_checks: list[str] | None = None) -> None:
    try:
        if not has_team(home):
            return
        lib = _lib(home, source)
        if lib is None:
            return
        data: dict = {"command": command, "ok": bool(ok),
                      "python": f"{sys.version_info[0]}.{sys.version_info[1]}.{sys.version_info[2]}"}
        if error_kind and _TOKEN_RE.fullmatch(error_kind):
            data["error_kind"] = error_kind
        if options is not None:
            data["packs"] = [p for p in options.packs if _TOKEN_RE.fullmatch(str(p))]
            data["adapters"] = [a for a in options.adapters if _TOKEN_RE.fullmatch(str(a))]
            data["hook_mode"] = "block" if options.strict else "warn"
        if failed_checks:
            data["failed_checks"] = [c for c in failed_checks if _TOKEN_RE.fullmatch(str(c))]
        lib.emit(home / ".claude", "install", data)
    except Exception:
        return


def _run_killing_tree(cmd: list[str], env: dict, timeout: int) -> None:
    """Run cmd in its own process group/session; on timeout kill the whole tree (git grandchildren included)."""
    kwargs: dict = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, **kwargs)
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True,
                               timeout=10, stdin=subprocess.DEVNULL)
            else:
                import signal
                os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            pass
        try:
            proc.kill()
            proc.wait(timeout=10)
        except Exception:
            pass


def uninstall_report(home: Path, source: Path | None, timeout: int = 60) -> None:
    """Emit install(command=uninstall) and flush with a hard external timeout."""
    try:
        if not has_team(home):
            return
        emit_install(home, source, "uninstall", True)
        script = home / ".claude" / "bin" / "aicj-telemetry"
        lib = home / ".claude" / LIB_REL
        if not script.is_file() or not lib.is_file():
            return
        env_claude = str(home / ".claude")
        env = dict(os.environ, AICJ_CLAUDE_DIR=env_claude, AICJ_TELEMETRY_SAME_PGRP="1")
        _run_killing_tree([sys.executable, str(script), "report", "--force"], env, timeout)
    except Exception:
        return


def _force_rmtree(path: Path) -> None:
    def _onerror(func, p, _exc):  # Windows: git marks objects read-only
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass
    shutil.rmtree(path, onerror=_onerror)


def remove_collect_artifacts(home: Path) -> None:
    """Uninstall: drop the collect-repo clone (whole team's data) and the report lock; keep spool/state/identity."""
    try:
        tdir = home / ".claude" / "aicj" / "telemetry"
        repo = tdir / "collect-repo"
        if repo.is_dir() and not repo.is_symlink():
            _force_rmtree(repo)
        lock = tdir / "report.lock"
        if lock.is_dir() and not lock.is_symlink():
            _force_rmtree(lock)
        elif lock.exists() or lock.is_symlink():
            lock.unlink()
    except Exception:
        return
