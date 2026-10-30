#!/usr/bin/env python3
"""Cross-platform installer smoke test using an isolated temporary home."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


def run(repo: Path, home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(repo / "installer/aicj.py"), *args, "--home", str(home), "--source", str(repo)],
        cwd=repo, text=True, encoding="utf-8", capture_output=True,
    )


def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="aicj-roundtrip-") as raw:
        home = Path(raw)
        install = run(repo, home, "install", "--codex", "--strict")
        if install.returncode != 0:
            print(install.stdout + install.stderr, end="")
            return 1
        doctor = run(repo, home, "doctor")
        print(doctor.stdout, end="")
        if doctor.returncode != 0 or any(line.startswith(("MISSING", "FAIL")) for line in doctor.stdout.splitlines()):
            return 1
        config = home / ".claude/aicj/config.json"
        if json.loads(config.read_text(encoding="utf-8")) != {"hook_mode": "block", "executor_mode": "cc"}:
            return 1
        uninstall = run(repo, home, "uninstall")
        if uninstall.returncode != 0:
            print(uninstall.stdout + uninstall.stderr, end="")
            return 1
        leftovers = [p for p in home.rglob("*") if p.is_file() or p.is_symlink()]
        if leftovers:
            print("leftovers:", ", ".join(str(p.relative_to(home)) for p in leftovers))
            return 1
    print("install roundtrip: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
