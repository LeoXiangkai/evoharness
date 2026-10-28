#!/usr/bin/env python3
"""SessionStart hook: start a detached telemetry report when one is due.

`--run` is the detached child: collect() then run_report(). Always exits 0, prints nothing.
Does nothing unless <claude>/aicj/team.json exists.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

def _claude_dir() -> Path:
    env = os.environ.get("AICJ_CLAUDE_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def _spawn() -> None:
    kwargs: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.DEVNULL, "close_fds": True}
    if os.name == "nt":
        kwargs["creationflags"] = (getattr(subprocess, "DETACHED_PROCESS", 0x8)
                                   | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200))
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--run"], **kwargs)


def main() -> None:
    claude = _claude_dir()
    if not (claude / "aicj" / "team.json").is_file():
        return
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import aicj_telemetry as tel

    if "--run" in sys.argv[1:]:
        tel.collect(claude)
        tel.run_report(claude)
        return
    if tel.due(claude):
        _spawn()


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        pass
    sys.exit(0)
