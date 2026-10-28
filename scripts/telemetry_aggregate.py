#!/usr/bin/env python3
"""Admin-side aggregation of the team telemetry collect repo (not installed).

Usage: telemetry_aggregate.py --repo <collect repo clone> [--since YYYY-MM-DD] [--format md|json]

Reads <date>/<user>-<install>.jsonl, re-validates every row with the library's
sanitize, drops duplicate event_ids across all files and prints summary counts only
(no raw events).
Users appear only as their hash.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core" / "hooks"))
import aicj_telemetry as tel  # noqa: E402

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

FAMILIES = {
    "install": ("install", "project_init"),
    "hook": ("hook_gate", "bypass"),
    "task_days": ("task_snapshot",),  # one snapshot per task per day: counts task-days, not tasks
    "rework": ("test_run", "review"),
    "lesson": ("lesson_card", "lesson_recall", "correction"),
}
FAMILY_OF = {t: f for f, types in FAMILIES.items() for t in types}


def load_events(repo: Path, since: str) -> list[dict]:
    seen: set[str] = set()
    events: list[dict] = []
    for day in sorted(p for p in repo.iterdir() if p.is_dir() and DATE_RE.match(p.name)):
        if since and day.name < since:
            continue
        for f in sorted(day.glob("*.jsonl")):
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    row = json.loads(line.strip("\r"))
                except ValueError:
                    continue
                clean = tel.sanitize(row) if isinstance(row, dict) else None
                if not clean or clean["event_id"] in seen:
                    continue
                seen.add(clean["event_id"])
                events.append(clean)
    return events


def family_counts(events: list[dict]) -> dict[str, int]:
    out = {f: 0 for f in FAMILIES}
    for ev in events:
        fam = FAMILY_OF.get(ev["type"])
        if fam:
            out[fam] += 1
    return out


def grouped(events: list[dict], key) -> dict[str, dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for ev in events:
        groups[key(ev)].append(ev)
    rows = {}
    for name in sorted(groups):
        evs = groups[name]
        rows[name] = {"families": family_counts(evs), "events": len(evs), "detail": detail(evs)}
    return rows


def _int(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def by_task(events: list[dict]) -> dict[str, dict]:
    """Per task hash: test run count and 1-based index of the first green run (ts order)."""
    runs: dict[str, list[dict]] = defaultdict(list)
    for ev in events:
        if ev["type"] == "test_run" and ev.get("task"):
            runs[ev["task"]].append(ev)
    out = {}
    for task in sorted(runs):
        ordered = sorted(runs[task], key=lambda e: (e["ts"], e["event_id"]))
        first = next((i for i, e in enumerate(ordered, 1) if e.get("green")), None)
        out[task] = {"test_runs": len(ordered), "first_green_index": first}
    return out


def by_lesson_card(events: list[dict]) -> dict[str, dict]:
    """Per card: recall count and corrections in the same task after the card's first recall (best-effort)."""
    recalls: dict[str, list[dict]] = defaultdict(list)
    for ev in events:
        if ev["type"] == "lesson_recall" and ev.get("card"):
            recalls[ev["card"]].append(ev)
    corrections = [ev for ev in events if ev["type"] == "correction" and ev.get("task")]
    out = {}
    for card in sorted(recalls):
        first: dict[str, str] = {}
        for ev in recalls[card]:
            if ev.get("task") and (ev["task"] not in first or ev["ts"] < first[ev["task"]]):
                first[ev["task"]] = ev["ts"]
        after = sum(1 for c in corrections if c["task"] in first and c["ts"] > first[c["task"]])
        out[card] = {"recalls": len(recalls[card]), "corrections_after_recall": after}
    return out


def detail(events: list[dict]) -> dict[str, int]:
    c = Counter()
    for ev in events:
        t = ev["type"]
        if t == "install" and ev.get("command") == "install":
            c["install_ok" if ev.get("ok") else "install_failed"] += 1
        elif t == "hook_gate":
            c["gate_" + str(ev.get("event"))] += 1
            c["gate_mode_" + str(ev.get("mode") or "unknown")] += 1
        elif t == "bypass":
            c["bypass"] += 1
        elif t == "test_run":
            c["test_runs"] += 1
            c["test_green"] += 1 if ev.get("green") else 0
            for k in ("passed", "failed", "skipped"):
                c["tests_" + k] += _int(ev.get(k))
        elif t == "review" and ev.get("kind") == "outcome":
            c["review_findings"] += int(ev.get("findings") or 0)
            c["review_confirmed"] += int(ev.get("confirmed") or 0)
        elif t == "correction":
            c["corrections"] += 1
        elif t == "lesson_recall":
            c["lesson_recalls"] += 1
    return dict(sorted(c.items()))


def summarize(events: list[dict]) -> dict:
    by_type = Counter(ev["type"] for ev in events)
    installs = {(ev["user"], ev.get("install")) for ev in events if ev.get("install")}
    ran = [ev for ev in events if ev["type"] == "install" and ev.get("command") == "install"]
    failed = [ev for ev in ran if not ev.get("ok")]
    tests = [ev for ev in events if ev["type"] == "test_run"]
    latest: dict[tuple, dict] = {}
    for ev in events:
        if ev["type"] == "task_snapshot":
            k = (ev["user"], ev.get("task"))
            if k not in latest or ev["ts"] >= latest[k]["ts"]:
                latest[k] = ev
    return {
        "events": len(events),
        "users": len({ev["user"] for ev in events}),
        "installs": len(installs),
        "events_by_type": dict(sorted(by_type.items())),
        "install_failure_rate": (len(failed) / len(ran)) if ran else None,
        "hook_gate_by_rule_tag": dict(sorted(Counter(
            ev.get("rule_tag") or "-" for ev in events if ev["type"] == "hook_gate").items())),
        "bypass_by_kind": dict(sorted(Counter(ev.get("kind") for ev in events if ev["type"] == "bypass").items())),
        "test_green_rate": (sum(1 for t in tests if t.get("green")) / len(tests)) if tests else None,
        "review_findings": sum(int(ev.get("findings") or 0) for ev in events
                               if ev["type"] == "review" and ev.get("kind") == "outcome"),
        "corrections": by_type.get("correction", 0),
        "task_rework": {
            "tasks": len(latest),
            "files_touched_repeatedly": sum(int(s.get("files_touched_repeatedly") or 0) for s in latest.values()),
            "commits_after_review": sum(int(s.get("commits_after_review") or 0) for s in latest.values()),
        },
    }


def build(repo: Path, since: str) -> dict:
    events = load_events(repo, since)
    return {
        "summary": summarize(events),
        "families": {f: list(t) for f, t in FAMILIES.items()},
        "by_day": grouped(events, lambda ev: ev["ts"][:10]),
        "by_user": grouped(events, lambda ev: ev["user"]),
        "by_task": by_task(events),
        "by_lesson_card": by_lesson_card(events),
    }


def _table(title: str, first: str, rows: dict[str, dict]) -> list[str]:
    fams = list(FAMILIES)
    out = [f"## {title}", "", "| " + " | ".join([first, *fams, "events", "detail"]) + " |",
           "|" + "---|" * (len(fams) + 3)]
    for name, row in rows.items():
        det = ", ".join(f"{k}={v}" for k, v in row["detail"].items()) or "-"
        out.append("| " + " | ".join([name, *(str(row["families"][f]) for f in fams), str(row["events"]), det]) + " |")
    return out + [""]


def render_md(data: dict) -> str:
    s = data["summary"]
    rate = lambda v: "n/a" if v is None else f"{v:.0%}"  # noqa: E731
    lines = ["# Team telemetry summary", "",
             f"- events: {s['events']}  users: {s['users']}  installs: {s['installs']}",
             f"- events by type: {json.dumps(s['events_by_type'])}",
             f"- install failure rate: {rate(s['install_failure_rate'])}",
             f"- hook gate by rule_tag: {json.dumps(s['hook_gate_by_rule_tag'])}",
             f"- bypass by kind: {json.dumps(s['bypass_by_kind'])}",
             f"- test green rate: {rate(s['test_green_rate'])}",
             f"- review findings: {s['review_findings']}  corrections: {s['corrections']}",
             f"- task rework: {json.dumps(s['task_rework'])}", "",
             "Families: " + "; ".join(f"{f}={'/'.join(t)}" for f, t in data["families"].items()), ""]
    lines += _table("By day", "day", data["by_day"])
    lines += _table("By user hash", "user", data["by_user"])
    lines += ["## By task", "", "| task | test_runs | first_green_index |", "|---|---|---|"]
    lines += [f"| {t} | {r['test_runs']} | {r['first_green_index'] or '-'} |" for t, r in data["by_task"].items()]
    lines += ["", "## By lesson card (best-effort: matched by task hash and ts)", "",
              "| card | recalls | corrections_after_recall |", "|---|---|---|"]
    lines += [f"| {c} | {r['recalls']} | {r['corrections_after_recall']} |" for c, r in data["by_lesson_card"].items()]
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", required=True)
    ap.add_argument("--since", default="")
    ap.add_argument("--format", choices=("md", "json"), default="md")
    args = ap.parse_args(argv)
    repo = Path(args.repo).expanduser()
    since = args.since.strip()
    if not repo.is_dir() or (since and not DATE_RE.match(since)):
        print("invalid --repo or --since (YYYY-MM-DD)", file=sys.stderr)
        return 2
    data = build(repo, since)
    print(json.dumps(data, ensure_ascii=False, indent=2) if args.format == "json" else render_md(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
