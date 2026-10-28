from __future__ import annotations

import json
import subprocess
import sys

from .conftest import REPO_ROOT

AGG = REPO_ROOT / "scripts" / "telemetry_aggregate.py"
USER = "a" * 16
TASK1 = "1" * 12
TASK2 = "2" * 12
CARD_A = "c" * 12
CARD_B = "d" * 12


def ev(n, etype, ts, **data):
    row = {"schema": 1, "type": etype, "ts": ts, "event_id": f"{n:016x}", "team": "team1", "user": USER,
           "install": "b" * 8, "platform": "darwin"}
    row.update(data)
    return row


def write(repo, day, name, rows):
    d = repo / day
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def run(repo, fmt):
    r = subprocess.run([sys.executable, str(AGG), "--repo", str(repo), "--format", fmt],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout) if fmt == "json" else r.stdout


def fixture(repo):
    gate = ev(1, "hook_gate", "2026-10-20T01:00:00Z", event="deny", rule_tag="r1", mode="block")
    rows1 = [
        gate,
        ev(2, "hook_gate", "2026-10-20T01:01:00Z", event="ask_dropped", rule_tag="r1", mode="warn"),
        ev(3, "hook_gate", "2026-10-20T01:02:00Z", event="ask_dropped", rule_tag="r1", mode="unknown"),
        ev(4, "test_run", "2026-10-20T02:00:00Z", task=TASK1, green=False, passed=3, failed=2, skipped=1),
        ev(5, "test_run", "2026-10-20T02:10:00Z", task=TASK1, green=False, passed=4, failed=1, skipped=0),
        ev(6, "test_run", "2026-10-20T02:20:00Z", task=TASK1, green=True, passed=5, failed=0, skipped=1),
        ev(7, "test_run", "2026-10-20T03:00:00Z", task=TASK2, green=True, passed=7, failed=0, skipped=0),
        ev(8, "lesson_recall", "2026-10-20T01:30:00Z", task=TASK1, card=CARD_A, level="hint"),
        ev(9, "correction", "2026-10-20T01:20:00Z", task=TASK1),   # before first recall
        ev(10, "correction", "2026-10-20T01:40:00Z", task=TASK1),  # after
        ev(11, "correction", "2026-10-20T01:50:00Z", task=TASK2),  # other task, no recall there
        ev(12, "lesson_recall", "2026-10-20T04:00:00Z", task=TASK1, card=CARD_A, level="hint"),
        ev(13, "lesson_recall", "2026-10-20T01:45:00Z", task=TASK1, card=CARD_B, level="warn"),
    ]
    write(repo, "2026-10-20", f"{USER}-install1.jsonl", rows1)
    # same events re-uploaded under a second install file after a local reset, plus one new event
    write(repo, "2026-10-20", f"{USER}-install2.jsonl", [gate, rows1[3], ev(14, "correction", "2026-10-20T05:00:00Z", task=TASK1)])
    write(repo, "2026-10-21", f"{USER}-install2.jsonl", [rows1[5]])


def test_aggregate_json_exact(tmp_path):
    fixture(tmp_path)
    data = run(tmp_path, "json")
    assert data["summary"]["events"] == 14
    assert data["summary"]["corrections"] == 4
    day = data["by_user"][USER]["detail"]
    assert day["gate_deny"] == 1 and day["gate_ask_dropped"] == 2
    assert day["gate_mode_block"] == 1 and day["gate_mode_warn"] == 1 and day["gate_mode_unknown"] == 1
    assert day["test_runs"] == 4 and day["test_green"] == 2
    assert (day["tests_passed"], day["tests_failed"], day["tests_skipped"]) == (19, 3, 2)
    assert day["lesson_recalls"] == 3
    assert data["by_task"] == {TASK1: {"test_runs": 3, "first_green_index": 3},
                               TASK2: {"test_runs": 1, "first_green_index": 1}}
    assert data["by_lesson_card"] == {CARD_A: {"recalls": 2, "corrections_after_recall": 2},
                                      CARD_B: {"recalls": 1, "corrections_after_recall": 1}}
    assert "event_id" not in json.dumps(data)


def test_aggregate_md_has_sections_without_raw_events(tmp_path):
    fixture(tmp_path)
    out = run(tmp_path, "md")
    assert "## By task" in out and "## By lesson card (best-effort" in out
    assert f"| {TASK1} | 3 | 3 |" in out and f"| {CARD_A} | 2 | 2 |" in out
    assert "gate_mode_block=1" in out and "tests_passed=19" in out
    assert "0000000000000001" not in out


def test_first_green_index_none_when_never_green(tmp_path):
    write(tmp_path, "2026-10-20", f"{USER}-i.jsonl", [ev(1, "test_run", "2026-10-20T02:00:00Z", task=TASK1, green=False)])
    data = run(tmp_path, "json")
    assert data["by_task"] == {TASK1: {"test_runs": 1, "first_green_index": None}}
