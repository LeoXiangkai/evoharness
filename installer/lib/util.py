from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


class UserError(Exception):
    """Raised for bad arguments or unreadable/invalid input files (exit 2)."""


def is_windows() -> bool:
    return os.name == "nt"


def abspath(path: str | Path) -> Path:
    return Path(os.path.abspath(os.path.expanduser(str(path))))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def atomic_write(path: Path, data: bytes) -> None:
    # a symlinked target (dotfiles setup) must stay a symlink: write to what it points at
    real = Path(os.path.realpath(path))
    real.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode = stat.S_IMODE(real.stat().st_mode)
    except OSError:
        mode = 0o644
    fd, tmp = tempfile.mkstemp(dir=str(real.parent), prefix=f".{real.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        if not is_windows():
            os.chmod(tmp, mode)
        for attempt in range(5):
            try:
                os.replace(tmp, real)
                break
            except PermissionError:
                if not is_windows() or attempt == 4:
                    raise
                time.sleep(0.05 * (attempt + 1))
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write(path, text.encode("utf-8"))


def read_json(path: Path):
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise UserError(f"cannot read {path}: {exc}") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise UserError(f"invalid JSON in {path}: {exc}") from exc


def write_json(path: Path, payload) -> None:
    atomic_write_text(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def git_commit(repo: Path) -> str:
    import subprocess

    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def unique_backup_rel(home: Path, rel: str, ts: str) -> str:
    """`<rel>.aicj-bak-<ts>`, with a counter suffix when that name is taken."""
    candidate = f"{rel}.aicj-bak-{ts}"
    counter = 1
    while (home / candidate).exists() or (home / candidate).is_symlink():
        counter += 1
        candidate = f"{rel}.aicj-bak-{ts}-{counter}"
    return candidate
