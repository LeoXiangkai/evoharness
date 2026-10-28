from __future__ import annotations

import json
import os
import shutil
import time

import aicj_telemetry as tel
from .conftest import git, make_remote, remote_files, spool_events, write_log, write_team

CORR = "aicj/lessons/corrections.jsonl"


def _corrections(claude, day="2026-10-27", n=2, start=0):
    rows = [{"ts": f"{day}T10:{start + i:02d}:00Z"} for i in range(n)]
    p = claude / CORR
    old = p.read_bytes() if p.exists() else b""
    write_log(p, rows)
    p.write_bytes(old + p.read_bytes())


def _lines(text):
    return [json.loads(x) for x in text.splitlines() if x.strip()]


def test_report_uploads_to_user_install_path(claude, team, remote):
    _corrections(claude)
    res = tel.run_report(claude, force=True)
    assert res == {"status": "ok", "reason": "uploaded", "uploaded": 2}
    files = remote_files(remote)
    ident = tel.identity(claude, team)
    assert list(files) == [f"2026-10-27/{ident['user']}-{ident['install']}.jsonl"]
    evs = _lines(next(iter(files.values())))
    assert len(evs) == 2 and all(e["type"] == "correction" for e in evs)
    st = tel.status(claude)
    assert st["last_error"] is None and st["last_success"] and st["pending"] == 0


def test_reupload_does_not_duplicate(claude, team, remote):
    _corrections(claude)
    tel.run_report(claude, force=True)
    # Simulate lost upload offsets: the whole spool is re-read, event_id dedupe must hold.
    sp = claude / "aicj" / "telemetry" / "state.json"
    st = json.loads(sp.read_text(encoding="utf-8"))
    st["upload_offsets"] = {}
    sp.write_text(json.dumps(st), encoding="utf-8")
    res = tel.run_report(claude, force=True)
    assert res["uploaded"] == 0
    evs = _lines(next(iter(remote_files(remote).values())))
    assert len(evs) == 2 and len({e["event_id"] for e in evs}) == 2


def test_incremental_upload_appends(claude, team, remote):
    _corrections(claude, n=1)
    tel.run_report(claude, force=True)
    _corrections(claude, n=1, start=5)
    assert tel.run_report(claude, force=True)["uploaded"] == 1
    assert len(_lines(next(iter(remote_files(remote).values())))) == 2


def test_two_installs_two_files(tmp_path, remote):
    for n in ("a", "b"):
        d = tmp_path / n
        d.mkdir()
        write_team(d, remote)
        _corrections(d)
        assert tel.run_report(d, force=True)["status"] == "ok"
    files = remote_files(remote)
    assert len(files) == 2
    users = {k.split("/")[1].rsplit("-", 1)[0] for k in files}
    assert len(users) == 1  # same person, two installs


def test_concurrent_remote_change_is_rebased(claude, team, remote, tmp_path, monkeypatch):
    _corrections(claude)
    real = tel._prepare_repo

    def prepare_then_other_pushes(*a, **k):
        real(*a, **k)
        other = tmp_path / "other"
        git(tmp_path, "clone", "-q", str(remote), str(other))
        (other / "x.txt").write_text("y\n", encoding="utf-8")
        git(other, "add", "-A")
        git(other, "-c", "user.name=o", "-c", "user.email=o" + "@x.test", "commit", "-qm", "other")
        git(other, "push", "-q", "origin", "main")

    monkeypatch.setattr(tel, "_prepare_repo", prepare_then_other_pushes)
    assert tel.run_report(claude, force=True)["status"] == "ok"
    files = remote_files(remote)
    assert "x.txt" in files and any(k.startswith("2026-10-27/") for k in files)


def test_rewritten_remote_history_recovers(claude, team, remote, tmp_path):
    _corrections(claude, n=1)
    assert tel.run_report(claude, force=True)["status"] == "ok"
    # Remote history replaced by an unrelated root commit: pull --rebase fails, reset path must recover.
    fresh = tmp_path / "fresh"
    git(tmp_path, "init", "-q", "-b", "main", str(fresh))
    (fresh / "README").write_text("new\n", encoding="utf-8")
    git(fresh, "add", "-A")
    git(fresh, "-c", "user.name=o", "-c", "user.email=o" + "@x.test", "commit", "-qm", "new root")
    git(fresh, "push", "-q", "--force", str(remote), "main")
    _corrections(claude, n=1, start=9)
    res = tel.run_report(claude, force=True)
    assert res["status"] == "ok", res
    evs = [e for v in remote_files(remote).values() for e in _lines(v)]
    assert len(evs) == 1
    assert tel.status(claude)["last_error"] is None


def test_unreachable_repo_fails_keeps_spool_then_catches_up(claude, tmp_path):
    bad = tmp_path / "nope.git"
    write_team(claude, bad)
    _corrections(claude)
    res = tel.run_report(claude, force=True)
    assert res["status"] == "failed" and res["reason"] == "clone"
    st = tel.status(claude)
    assert st["last_error"] == "clone" and st["last_success"] is None
    assert len(spool_events(claude)) == 2 and st["pending"] == 2
    remote = make_remote(tmp_path, "late.git")
    write_team(claude, remote)
    res = tel.run_report(claude, force=True)
    assert res["status"] == "ok" and res["uploaded"] == 2
    assert len(remote_files(remote)) == 1
    assert tel.status(claude)["last_error"] is None


def test_empty_remote_is_initialised(claude, tmp_path):
    empty = tmp_path / "empty.git"
    git(tmp_path, "init", "--bare", "-b", "main", str(empty))
    write_team(claude, empty)
    _corrections(claude)
    assert tel.run_report(claude, force=True)["status"] == "ok"
    assert len(remote_files(empty)) == 1


def test_throttle_and_force(claude, team):
    _corrections(claude)
    assert tel.run_report(claude)["status"] == "ok"
    _corrections(claude, n=1, start=7)
    assert tel.run_report(claude) == {"status": "skipped", "reason": "throttled", "uploaded": 0}
    assert tel.run_report(claude, force=True)["uploaded"] == 1


def test_failed_attempt_throttled_one_hour(claude, tmp_path):
    write_team(claude, tmp_path / "nope.git")
    _corrections(claude)
    assert tel.run_report(claude)["status"] == "failed"
    assert tel.run_report(claude)["reason"] == "throttled"


def test_fresh_lock_blocks_stale_lock_is_taken(claude, team):
    _corrections(claude)
    tdir = claude / "aicj" / "telemetry"
    tdir.mkdir(parents=True, exist_ok=True)
    lock = tdir / "report.lock"
    lock.write_text("1")
    assert tel.run_report(claude, force=True) == {"status": "skipped", "reason": "locked", "uploaded": 0}
    old = time.time() - tel.LOCK_STALE_SECONDS - 60
    os.utime(lock, (old, old))
    assert tel.run_report(claude, force=True)["status"] == "ok"
    assert not lock.exists()


def test_spool_cap_drops_oldest(claude, team, tmp_path):
    write_team(claude, tmp_path / "nope.git", spool_max_mb=0.001)  # ~1 KiB
    spool = claude / "aicj" / "telemetry" / "spool"
    spool.mkdir(parents=True)
    for day in ("2026-10-01", "2026-10-02", "2026-10-03"):
        (spool / f"{day}.jsonl").write_text("x" * 700 + "\n", encoding="utf-8")
    tel.run_report(claude, force=True)
    left = sorted(p.name for p in spool.glob("*.jsonl"))
    assert left and "2026-10-03.jsonl" in left and "2026-10-01.jsonl" not in left


def test_uploaded_events_are_sanitized_again(claude, team, remote):
    spool = claude / "aicj" / "telemetry" / "spool"
    spool.mkdir(parents=True)
    ident = tel.identity(claude, team)
    good = {"schema": 1, "type": "correction", "ts": "2026-10-27T10:00:00Z", "event_id": "a" * 32,
            "team": "team1", "user": ident["user"], "install": ident["install"], "platform": "linux",
            "prompt": "tell me secrets"}
    unknown = dict(good, type="mystery", event_id="b" * 32)
    (spool / "2026-10-27.jsonl").write_text(json.dumps(good) + "\n" + json.dumps(unknown) + "\n", encoding="utf-8")
    tel.run_report(claude, force=True)
    blob = "".join(remote_files(remote).values())
    assert "secrets" not in blob and "mystery" not in blob and "a" * 32 in blob


def test_crlf_in_remote_file_still_dedupes(claude, team, remote, tmp_path):
    _corrections(claude, n=1)
    tel.run_report(claude, force=True)
    path = next(iter(remote_files(remote)))
    clone = tmp_path / "crlf"
    git(tmp_path, "clone", "-q", str(remote), str(clone))
    f = clone / path
    f.write_bytes(f.read_bytes().replace(b"\n", b"\r\n"))
    git(clone, "add", "-A")
    git(clone, "-c", "user.name=o", "-c", "user.email=o" + "@x.test", "commit", "-qm", "crlf")
    git(clone, "push", "-q", "origin", "main")
    st = json.loads((claude / "aicj/telemetry/state.json").read_text(encoding="utf-8"))
    st["upload_offsets"] = {}
    (claude / "aicj/telemetry/state.json").write_text(json.dumps(st), encoding="utf-8")
    assert tel.run_report(claude, force=True)["uploaded"] == 0


def test_state_json_fields(claude, team):
    _corrections(claude)
    tel.run_report(claude, force=True)
    st = json.loads((claude / "aicj/telemetry/state.json").read_text(encoding="utf-8"))
    for k in ("offsets", "upload_offsets", "last_attempt", "last_success", "last_error"):
        assert k in st
    shutil.rmtree(claude / "aicj/telemetry/collect-repo")
    _corrections(claude, n=1, start=20)
    assert tel.run_report(claude, force=True)["uploaded"] == 1  # re-clone works


def _force_push_conflicting_root(remote, tmp_path, rel, content):
    fresh = tmp_path / "conflict"
    shutil.rmtree(fresh, ignore_errors=True)
    git(tmp_path, "init", "-q", "-b", "main", str(fresh))
    target = fresh / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    git(fresh, "add", "-A")
    git(fresh, "-c", "user.name=o", "-c", "user.email=o" + "@x.test", "commit", "-qm", "root")
    git(fresh, "push", "-q", "--force", str(remote), "main")


def test_push_recovery_resets_when_rebase_conflicts(claude, team, remote, tmp_path, monkeypatch):
    _corrections(claude)
    ident = tel.identity(claude, team)
    rel = f"2026-10-27/{ident['user']}-{ident['install']}.jsonl"
    real_prepare = tel._prepare_repo
    resets = []
    real_reset = tel._reset_to_remote

    def prepare_then_conflict(*a, **k):
        real_prepare(*a, **k)
        _force_push_conflicting_root(remote, tmp_path, rel, '{"stale": true}\n')

    def spy(*a, **k):
        resets.append(1)
        return real_reset(*a, **k)

    monkeypatch.setattr(tel, "_prepare_repo", prepare_then_conflict)
    monkeypatch.setattr(tel, "_reset_to_remote", spy)
    res = tel.run_report(claude, force=True)
    assert res["status"] == "ok", res
    assert resets
    evs = _lines(remote_files(remote)[rel])
    ids = [e.get("event_id") for e in evs if "event_id" in e]
    assert len(ids) == 2 and len(set(ids)) == 2
    assert tel.status(claude)["last_error"] is None


def test_empty_remote_first_push_failure_then_catches_up(claude, tmp_path, monkeypatch):
    empty = tmp_path / "empty2.git"
    git(tmp_path, "init", "--bare", "-b", "main", str(empty))
    write_team(claude, empty)
    _corrections(claude)
    real = tel._run

    def fail_push(repo, prefix, args, **k):
        if args and args[0] == "push":
            raise tel._ReportError("push")
        return real(repo, prefix, args, **k)

    monkeypatch.setattr(tel, "_run", fail_push)
    assert tel.run_report(claude, force=True) == {"status": "failed", "reason": "push", "uploaded": 0}
    monkeypatch.setattr(tel, "_run", real)
    res = tel.run_report(claude, force=True)
    assert res["status"] == "ok", res
    assert len(remote_files(empty)) == 1


def test_converted_event_ids_survive_telemetry_dir_wipe(claude, team, remote, tmp_path):
    _corrections(claude)
    write_log(claude / "aicj/review-logs/code-review-gates.jsonl", [
        {"ts": "2026-10-27T10:00:00+00:00", "kind": "outcome", "common_dir": str(tmp_path / "x" / ".git"),
         "branch": "feature/q", "reviewers": 1, "findings": 0, "confirmed": 0, "review_gate": "required"}])
    tel.collect(claude)
    assert tel.run_report(claude, force=True)["status"] == "ok"

    def ids():
        out = set()
        for text in remote_files(remote).values():
            out |= {e["event_id"] for e in _lines(text)}
        return out

    before = ids()
    assert len(before) == 3
    shutil.rmtree(claude / "aicj" / "telemetry")
    tel.collect(claude)
    assert tel.run_report(claude, force=True)["status"] == "ok"
    assert ids() == before


def test_stale_lock_threshold_and_atomic_steal(claude, team):
    tdir = claude / "aicj" / "telemetry"
    tdir.mkdir(parents=True, exist_ok=True)
    assert tel.LOCK_STALE_SECONDS == 1800
    lock = tdir / "report.lock"
    lock.write_text("1")
    ten_min = time.time() - 600
    os.utime(lock, (ten_min, ten_min))
    assert tel._acquire_lock(tdir) is False
    old = time.time() - tel.LOCK_STALE_SECONDS - 60
    os.utime(lock, (old, old))
    assert tel._acquire_lock(tdir) is True
    assert lock.exists() and time.time() - lock.stat().st_mtime < 60
    assert not list(tdir.glob("report.lock.stale.*"))
    assert tel._acquire_lock(tdir) is False


def test_due_reads_only_team_and_state(claude, team):
    assert tel.due(claude) is True
    _corrections(claude)
    tel.run_report(claude, force=True)
    assert tel.due(claude) is False
    state = tel._load_state(claude)
    state["last_success"] = "2020-01-01T00:00:00Z"
    state["last_attempt"] = "2020-01-01T00:00:00Z"
    tel._save_state(claude, state)
    assert tel.due(claude) is True
    state["last_attempt"] = tel._iso(tel._now())
    tel._save_state(claude, state)
    assert tel.due(claude) is False
    (claude / "aicj" / "team.json").unlink()
    assert tel.due(claude) is False


def test_git_clone_separates_repo_positional_with_double_dash(claude, team, monkeypatch):
    seen = []
    real = tel._run

    def spy(repo, prefix, args, **kw):
        seen.append(list(args))
        return real(repo, prefix, args, **kw)

    monkeypatch.setattr(tel, "_run", spy)
    _corrections(claude)
    assert tel.run_report(claude, force=True)["status"] == "ok"
    clones = [a for a in seen if a[0] == "clone"]
    assert clones and all("--" in a and a.index("--") < a.index(team["collect_repo"]) for a in clones)
    for a in seen:
        if a[0] in ("fetch", "pull", "ls-remote"):
            assert "--" in a
