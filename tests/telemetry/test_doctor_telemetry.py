from __future__ import annotations

import datetime as dt
import json
import sys

from .conftest import REPO_ROOT, write_team

sys.path.insert(0, str(REPO_ROOT / "installer"))
from lib import doctor  # noqa: E402


def _iso(delta_hours: float) -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=delta_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _setup(tmp_path, *, last_success=None, spool_age_hours=None, interval=24):
    home = tmp_path / "home"
    claude = home / ".claude"
    write_team(claude, "https://example.test/x.git", upload_interval_hours=interval)
    tdir = claude / "aicj" / "telemetry"
    tdir.mkdir(parents=True, exist_ok=True)
    if last_success:
        (tdir / "state.json").write_text(json.dumps({"last_success": last_success}), encoding="utf-8")
    if spool_age_hours is not None:
        day = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=spool_age_hours)).strftime("%Y-%m-%d")
        (tdir / "spool").mkdir()
        (tdir / "spool" / f"{day}.jsonl").write_text('{"x":1}\n', encoding="utf-8")
    return home


def _row(home):
    rows = [r for r in doctor._telemetry(home, REPO_ROOT) if r[1] == "telemetry-report"]
    assert len(rows) == 1
    return rows[0]


def test_no_team_returns_no_rows(tmp_path):
    assert doctor._telemetry(tmp_path / "home", REPO_ROOT) == []


def test_report_warns_when_last_success_older_than_three_intervals(tmp_path):
    status, _, detail = _row(_setup(tmp_path, last_success=_iso(80)))
    assert status == doctor.WARN and "3 个上报周期" in detail


def test_report_passes_when_last_success_recent(tmp_path):
    status, _, detail = _row(_setup(tmp_path, last_success=_iso(30)))
    assert status == doctor.PASS and "3 个上报周期" not in detail


def test_report_warns_when_never_succeeded_and_pending_backlog_is_old(tmp_path):
    status, _, detail = _row(_setup(tmp_path, spool_age_hours=120))
    assert status == doctor.WARN and "从未成功上报" in detail


def test_report_passes_when_never_succeeded_but_backlog_is_fresh(tmp_path):
    status, _, _ = _row(_setup(tmp_path, spool_age_hours=2))
    assert status == doctor.PASS
