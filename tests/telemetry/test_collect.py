from __future__ import annotations

import json
import subprocess

import aicj_telemetry as tel
from .conftest import spool_events, write_log

LOGS = "aicj/review-logs"
LES = "aicj/lessons"


def _gate_rows(cwd):
    return [{"ts": "2026-10-27T10:00:00.123456+00:00", "hook": "pre-bash", "event": "deny",
             "rule": "[git-safe] never skip hooks", "tool": "Bash", "cmd": "git commit --no-verify -m secret",
             "session_id": "s1", "cwd": cwd}]


def test_collect_real_formats_and_idempotent(claude, team, tmp_path):
    repo = tmp_path / "proj"
    subprocess.run(["git", "init", "-q", "-b", "feature/a", str(repo)], check=True)
    write_log(claude / LOGS / "gate-events.jsonl", _gate_rows(str(repo)))
    write_log(claude / LOGS / "code-review-gates.jsonl", [
        {"ts": "2026-10-27T10:01:00+00:00", "kind": "gate", "repo_root": str(repo), "branch": "feature/a",
         "files": ["a.py", "b.py"], "lines": 40, "review_gate": "required", "risk_signals": ["new_api"]},
        {"ts": "2026-10-27T10:05:00+00:00", "kind": "outcome", "repo_root": str(repo), "branch": "feature/a",
         "reviewers": 1, "findings": 3, "confirmed": 2, "review_gate": "required"},
    ])
    write_log(claude / LES / "corrections.jsonl", [
        {"ts": "2026-10-27T10:02:00Z", "session_id": "s1", "cwd": str(repo), "prompt": "you are wrong about X"}])
    write_log(claude / LES / "cards.jsonl", [
        {"id": "card-1", "scope": "global", "trigger": "t", "lesson": "secret lesson text", "level": "warn",
         "status": "active", "created": "2026-10-01"}])
    write_log(claude / LES / "recalls.jsonl", [
        {"ts": "2026-10-27T10:03:00Z", "card": "card-1", "session_id": "s1", "cwd": str(repo)}])

    n = tel.collect(claude)
    assert n == 6
    evs = spool_events(claude)
    types = sorted(e["type"] for e in evs)
    for t in ("hook_gate", "correction", "lesson_card", "lesson_recall"):
        assert t in types
    assert types.count("review") == 2
    blob = json.dumps(evs)
    for bad in ("no-verify", "secret", "you are wrong", str(repo), "never skip hooks"):
        assert bad not in blob
    gate = next(e for e in evs if e["type"] == "hook_gate")
    assert gate["ts"] == "2026-10-27T10:00:00Z" and gate["mode"] == "unknown" and gate["rule_tag"] == "git-safe"
    scope = next(e for e in evs if e["type"] == "review" and e["kind"] == "scope")
    assert scope["file_count"] == 2 and scope["changed_lines"] == 40 and scope["risk_signals"] == ["new_api"]
    outcome = next(e for e in evs if e["type"] == "review" and e["kind"] == "outcome")
    assert (outcome["findings"], outcome["confirmed"]) == (3, 2)
    assert outcome["task"] == scope["task"]
    recall = next(e for e in evs if e["type"] == "lesson_recall")
    assert recall["level"] == "warn"

    assert tel.collect(claude) == 0
    assert len(spool_events(claude)) == len(evs)


def test_collect_incremental_append(claude, team, tmp_path):
    p = claude / LES / "corrections.jsonl"
    write_log(p, [{"ts": "2026-10-27T10:02:00Z", "cwd": str(tmp_path)}])
    assert tel.collect(claude) == 1
    with open(p, "ab") as fh:
        fh.write(json.dumps({"ts": "2026-10-27T11:00:00Z", "cwd": str(tmp_path)}).encode() + b"\n")
    assert tel.collect(claude) == 1
    assert tel.collect(claude) == 0


def test_collect_crlf_and_partial_last_line(claude, team, tmp_path):
    p = claude / LES / "corrections.jsonl"
    write_log(p, [{"ts": "2026-10-27T10:02:00Z"}, {"ts": "2026-10-27T10:03:00Z"}], crlf=True)
    with open(p, "ab") as fh:
        fh.write(b'{"ts": "2026-10-27T10:04')  # writer mid-flight
    assert tel.collect(claude) == 2
    with open(p, "ab") as fh:
        fh.write(b':00Z"}\r\n')
    assert tel.collect(claude) == 1


def test_collect_skips_garbage_rows(claude, team):
    p = claude / LES / "corrections.jsonl"
    p.parent.mkdir(parents=True)
    p.write_bytes(b'not json\n[1,2]\n{"ts": "bad"}\n{"ts": "2026-10-27T10:02:00Z"}\n')
    assert tel.collect(claude) == 1


def test_cards_rewrite_only_emits_new_cards(claude, team):
    p = claude / LES / "cards.jsonl"
    c1 = {"id": "c1", "scope": "global", "level": "hint", "status": "active", "created": "2026-10-01"}
    c2 = {"id": "c2", "scope": "src/", "level": "warn", "status": "active", "created": "2026-10-02"}
    write_log(p, [c1])
    assert tel.collect(claude) == 1
    write_log(p, [c2, c1])  # atomic rewrite, different layout
    assert tel.collect(claude) == 1
    cards = [e for e in spool_events(claude) if e["type"] == "lesson_card"]
    assert {c["scope_kind"] for c in cards} == {"global", "path"}


def test_truncated_log_restarts(claude, team):
    p = claude / LES / "corrections.jsonl"
    write_log(p, [{"ts": "2026-10-27T10:02:00Z"}, {"ts": "2026-10-27T10:03:00Z"}])
    assert tel.collect(claude) == 2
    write_log(p, [{"ts": "2026-10-28T10:02:00Z"}])
    assert tel.collect(claude) == 1


def test_emit_writes_sanitized_event(claude, team, tmp_path):
    assert tel.emit(claude, "install", {"command": "install", "ok": True, "packs": ["java"],
                                        "path": "/Users/" + "bob/x"}, version="1.2.3") is True
    ev = spool_events(claude)[0]
    assert ev["version"] == "1.2.3" and ev["packs"] == ["java"] and "path" not in ev
    assert ev["team"] == "team1" and len(ev["event_id"]) == 32
    assert tel.emit(claude, "bogus", {}) is False


def test_emit_data_cannot_override_envelope(claude, team):
    tel.emit(claude, "correction", {"user": "evil", "team": "evil", "event_id": "00" * 8})
    ev = spool_events(claude)[0]
    assert ev["team"] == "team1" and ev["user"] != "evil" and ev["event_id"] != "00" * 8


def test_task_snapshot_counts(claude, team, tmp_path):
    repo = tmp_path / "proj"
    run = lambda *a: subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@x.test", *a],
                                    check=True, capture_output=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / "a").write_text("1")
    run("add", "-A"); run("commit", "-qm", "base")
    run("checkout", "-q", "-b", "feature/z")
    for i in range(2):
        (repo / "a").write_text(str(i + 5))
        (repo / f"n{i}").write_text("x")
        run("add", "-A"); run("commit", "-qm", f"c{i}")
    assert tel.task_key(claude, str(repo), team) is not None
    tel.collect(claude)
    snap = [e for e in spool_events(claude) if e["type"] == "task_snapshot"]
    assert len(snap) == 1
    assert snap[0]["commits"] == 2 and snap[0]["files_changed"] == 3 and snap[0]["files_touched_repeatedly"] == 1
    tel.collect(claude)
    assert len([e for e in spool_events(claude) if e["type"] == "task_snapshot"]) == 1


def test_windows_backslash_cwd_and_repo_root_do_not_leak(claude, team):
    win = "C:\\Users\\" + "alice\\proj"
    write_log(claude / LOGS / "gate-events.jsonl", _gate_rows(win))
    write_log(claude / LOGS / "code-review-gates.jsonl", [
        {"ts": "2026-10-27T10:01:00+00:00", "kind": "gate", "repo_root": win, "branch": "feature/a",
         "files": [], "lines": 1, "review_gate": "skip"}])
    assert tel.collect(claude) == 2
    blob = json.dumps(spool_events(claude))
    assert "alice" not in blob and "Users" not in blob


def test_emit_event_id_optional_keyword(claude, team):
    assert tel.emit(claude, "correction", {}, event_id="ab" * 16) is True
    assert tel.emit(claude, "correction", {}, event_id="NOT HEX") is True
    assert tel.emit(claude, "correction", {}, event_id=None) is True
    ids = [e["event_id"] for e in spool_events(claude)]
    assert ids[0] == "ab" * 16
    assert len(ids[1]) == 32 and ids[1] != "NOT HEX" and len(ids[2]) == 32 and ids[1] != ids[2]


def test_review_rows_share_task_key_and_snapshot_sees_review(claude, team, tmp_path):
    repo = tmp_path / "proj"

    def g(*a):
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@x.test", *a],
                       check=True, capture_output=True)

    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    g("remote", "add", "origin", "git@host.test:org/app.git")
    (repo / "a").write_text("1")
    g("add", "-A")
    g("commit", "-qm", "base")
    g("checkout", "-q", "-b", "feature/r")
    (repo / "a").write_text("2")
    g("add", "-A")
    g("commit", "-qm", "c1")
    key = tel.task_key(claude, str(repo), team)
    assert key is not None
    g("checkout", "-q", "main")  # HEAD differs from the branch recorded in the rows
    common = str(repo / ".git")
    base = {"files": ["a"], "lines": 1, "review_gate": "required", "branch": "feature/r"}
    write_log(claude / LOGS / "code-review-gates.jsonl", [
        dict(base, ts="2026-10-27T10:00:00+00:00", kind="gate", common_dir=common),
        dict(base, ts="2026-10-27T10:01:00+00:00", kind="gate", repo_root=str(repo)),
        dict(base, ts="2026-10-27T10:02:00+00:00", kind="outcome", common_dir=common,
             reviewers=1, findings=0, confirmed=0),
    ])
    tel.collect(claude)
    rev = [e for e in spool_events(claude) if e["type"] == "review"]
    assert len(rev) == 3
    assert {(e["repo"], e["task"]) for e in rev} == {key}
    g("checkout", "-q", "feature/r")
    (repo / "a").write_text("3")
    g("add", "-A")
    g("commit", "-qm", "c2")
    state = tel._load_state(claude)
    assert state["review_ts"][key[1]]
    state["snapshots"] = {}
    tel._save_state(claude, state)
    tel.collect(claude)
    snaps = [e for e in spool_events(claude) if e["type"] == "task_snapshot"]
    assert snaps and snaps[-1]["commits_after_review"] > 0
