from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

from .conftest import REPO_ROOT, spool_events, write_team

RECALL = REPO_ROOT / "adapters" / "lesson" / "hooks" / "lesson-recall.py"
PROBE = REPO_ROOT / "adapters" / "optional-plugins" / "verify-probe" / "bin" / "verify-probe"


def _env(claude):
    env = dict(os.environ, AICJ_CLAUDE_DIR=str(claude))
    env.pop("AICJ_REVIEW_LOG_DIR", None)
    return env


def _install_lib(claude):
    dest = claude / "hooks" / "aicj"
    dest.mkdir(parents=True)
    shutil.copy(REPO_ROOT / "core" / "hooks" / "aicj_telemetry.py", dest / "aicj_telemetry.py")


def _recall(claude, tmp_path):
    cards = claude / "aicj" / "lessons" / "cards.jsonl"
    cards.parent.mkdir(parents=True, exist_ok=True)
    cards.write_text(json.dumps({
        "id": "c1", "scope": "global", "trigger": {"tools": ["Bash"], "cmd_regex": "rm"},
        "lesson": "no", "level": "hint", "status": "active", "created": "2026-01-01"}) + "\n", encoding="utf-8")
    payload = {"tool_name": "Bash", "tool_input": {"command": "rm x"}, "session_id": "s1", "cwd": str(tmp_path)}
    return subprocess.run([sys.executable, str(RECALL)], input=json.dumps(payload), env=_env(claude),
                          capture_output=True, text=True)


def test_lesson_recall_writes_row_only_with_team_and_collector_reads_it(claude, remote, tmp_path):
    r = _recall(claude, tmp_path)
    assert r.returncode == 0 and "lesson:c1" in r.stdout
    assert not (claude / "aicj" / "lessons" / "recalls.jsonl").exists()

    claude2 = tmp_path / "claude2"
    claude2.mkdir()
    write_team(claude2, remote)
    _install_lib(claude2)
    r = _recall(claude2, tmp_path)
    assert r.returncode == 0 and "lesson:c1" in json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
    rows = [json.loads(x) for x in (claude2 / "aicj" / "lessons" / "recalls.jsonl").read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["card"] == "c1"

    import aicj_telemetry as tel
    tel.collect(claude2)
    kinds = [e["type"] for e in spool_events(claude2)]
    assert kinds.count("lesson_recall") == 1


def test_verify_probe_emits_event_with_team_and_keeps_exit_code(claude, remote, tmp_path):
    write_team(claude, remote)
    _install_lib(claude)
    prof = claude / "aicj" / "verify-profiles"
    prof.mkdir(parents=True)
    (prof / "p.json").write_text(json.dumps({
        "backend": {"root": str(tmp_path), "compile": {"probe_cmd": ["true"]}, "unit": {"probe_cmd": ["false"]}},
        "frontend": {"root": str(tmp_path), "unit": {"probe_cmd": ["true"]}},
        "e2e": {"root": str(tmp_path), "probe_cmd": ["true"]}}), encoding="utf-8")
    r = subprocess.run([sys.executable, str(PROBE), "p", "--json"], env=_env(claude), capture_output=True, text=True)
    assert r.returncode == 1
    ev = [e for e in spool_events(claude) if e["type"] == "verify_probe"]
    assert len(ev) == 1 and ev[0]["overall"] == "BLOCKED"
    assert "backend-unit" in ev[0]["failed_layers"] and "backend-compile" in ev[0]["layers"]
