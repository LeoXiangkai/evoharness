#!/usr/bin/env python3
"""Admin-side weekly review numbers for the team telemetry collect repo (not installed).

Usage: team_weekly.py --repo <collect repo clone> [--week YYYY-Www] [--format md|json] [--min-n 20]

Deterministic only: ISO-week window, metrics with week-over-week comparison, verdicts for
due trials from <repo>/admin/trials.jsonl, subtraction signals and a four-stage table.
Reads events through telemetry_aggregate.load_events; writes nothing. Design: docs/team-weekly-design.md.
Exit codes: 0 ok, 2 invalid arguments or --repo is not a git clone.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import telemetry_aggregate as agg  # noqa: E402

DEFAULT_MIN_N = 20
MISBLOCK_MIN_DENIES = 5
MISBLOCK_RATIO = 0.5
MISBLOCK_WINDOW = dt.timedelta(minutes=30)
CARD_MIN_RECALLS = 5
CARD_RATIO = 0.5
IDLE_WEEKS = 4

WEEK_RE = re.compile(r"^(\d{4})-W(\d{2})$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TS_FMT = "%Y-%m-%dT%H:%M:%SZ"
NO_DATA = "无数据"
LOW_N = "样本不足"

FIXED_KEYS = (
    "install_failure_rate", "test_green_rate", "first_green_index_median",
    "corrections_per_task_day", "rework_files_per_task", "commits_after_review_per_task",
    "bypass_total", "bypass:no_verify", "bypass:hook_mode_override", "bypass:skip_tests",
)
BYPASS_KINDS = ("no_verify", "hook_mode_override", "skip_tests")
PREFIX_KEYS = ("gate_deny:", "card_corrections_ratio:")
LOWER_IS_BETTER = {k for k in FIXED_KEYS if k != "test_green_rate"} | {"card_corrections_ratio:"}

TRIAL_FIELDS = ("id", "title", "registered", "metric", "baseline", "direction", "review_date",
                "revoke_if", "status", "deferred", "undo")


# ---------------------------------------------------------------------------
# Week window
# ---------------------------------------------------------------------------
def parse_week(text: str) -> tuple[int, int]:
    m = WEEK_RE.match((text or "").strip())
    if not m:
        raise ValueError(f"invalid week: {text!r}")
    year, week = int(m.group(1)), int(m.group(2))
    dt.date.fromisocalendar(year, week, 1)  # ValueError for week 53 of a 52-week year
    return year, week


def week_id(day: dt.date) -> str:
    iso = day.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def week_bounds(week: str) -> tuple[dt.datetime, dt.datetime]:
    """[Monday 00:00Z, next Monday 00:00Z)."""
    year, num = parse_week(week)
    monday = dt.date.fromisocalendar(year, num, 1)
    start = dt.datetime(monday.year, monday.month, monday.day, tzinfo=dt.timezone.utc)
    return start, start + dt.timedelta(days=7)


def shift_week(week: str, delta: int) -> str:
    start, _ = week_bounds(week)
    return week_id((start + dt.timedelta(days=7 * delta)).date())


def last_complete_week(now: dt.datetime | None = None) -> str:
    now = now or dt.datetime.now(dt.timezone.utc)
    return week_id((now - dt.timedelta(days=7)).date())


def _fmt_ts(value: dt.datetime) -> str:
    return value.strftime(TS_FMT)


def in_window(events: list[dict], start: dt.datetime, end: dt.datetime) -> list[dict]:
    lo, hi = _fmt_ts(start), _fmt_ts(end)
    return [ev for ev in events if lo <= ev["ts"] < hi]


def _parse_ts(value: str) -> dt.datetime:
    return dt.datetime.strptime(value, TS_FMT).replace(tzinfo=dt.timezone.utc)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def _ratio(num: int, den: int, min_n: int) -> dict:
    value = (num / den) if den >= min_n and den > 0 else None
    return {"value": value, "n": den}


def latest_snapshots(events: list[dict]) -> list[dict]:
    latest: dict[tuple, dict] = {}
    for ev in events:
        if ev["type"] == "task_snapshot":
            key = (ev["user"], ev.get("task") or ev["event_id"])
            if key not in latest or ev["ts"] >= latest[key]["ts"]:
                latest[key] = ev
    return list(latest.values())


def compute_metrics(events: list[dict], min_n: int) -> dict[str, dict]:
    """key -> {"value": number|None, "n": denominator}. Ratios with n < min_n are None."""
    out: dict[str, dict] = {}
    installs = [e for e in events if e["type"] == "install" and e.get("command") == "install"]
    out["install_failure_rate"] = _ratio(sum(1 for e in installs if not e.get("ok")), len(installs), min_n)
    tests = [e for e in events if e["type"] == "test_run"]
    out["test_green_rate"] = _ratio(sum(1 for e in tests if e.get("green")), len(tests), min_n)
    greens = [t["first_green_index"] for t in agg.by_task(events).values() if t["first_green_index"]]
    out["first_green_index_median"] = {
        "value": float(statistics.median(greens)) if len(greens) >= min_n else None, "n": len(greens)}
    snaps = [e for e in events if e["type"] == "task_snapshot"]
    out["corrections_per_task_day"] = _ratio(sum(1 for e in events if e["type"] == "correction"),
                                             len(snaps), min_n)
    latest = latest_snapshots(events)
    out["rework_files_per_task"] = _ratio(sum(agg._int(s.get("files_touched_repeatedly")) for s in latest),
                                          len(latest), min_n)
    out["commits_after_review_per_task"] = _ratio(sum(agg._int(s.get("commits_after_review")) for s in latest),
                                                  len(latest), min_n)
    have = bool(events)
    bypass = Counter(e.get("kind") for e in events if e["type"] == "bypass")
    out["bypass_total"] = {"value": sum(bypass.values()) if have else None, "n": None}
    for kind in BYPASS_KINDS:
        out["bypass:" + kind] = {"value": bypass.get(kind, 0) if have else None, "n": None}
    denies = Counter((e.get("rule_tag") or "-") for e in events
                     if e["type"] == "hook_gate" and e.get("event") == "deny")
    for tag in sorted(denies):
        out["gate_deny:" + tag] = {"value": denies[tag], "n": None}
    for card, row in agg.by_lesson_card(events).items():
        out["card_corrections_ratio:" + card] = _ratio(row["corrections_after_recall"], row["recalls"], min_n)
    return out


def valid_metric_key(key: object) -> bool:
    if not isinstance(key, str):
        return False
    if key in FIXED_KEYS:
        return True
    return any(key.startswith(p) and len(key) > len(p) and key == key.strip() for p in PREFIX_KEYS)


def metric_direction_note(key: str) -> str:
    if key.startswith("gate_deny:"):
        return "看提案语义"
    base = "card_corrections_ratio:" if key.startswith("card_corrections_ratio:") else key
    return "越低越好" if base in LOWER_IS_BETTER else "越高越好"


def load_prev_metrics(repo: Path, prev_week: str, events: list[dict], min_n: int) -> dict[str, dict]:
    """Prefer the archived snapshot admin/weekly/<week>.json; recompute from events when absent or stale."""
    snap = repo / "admin" / "weekly" / f"{prev_week}.json"
    try:
        data = json.loads(snap.read_text(encoding="utf-8"))
        if (isinstance(data, dict) and data.get("week") == prev_week and data.get("min_n") == min_n
                and isinstance(data.get("metrics"), dict)):
            return data["metrics"]
    except (OSError, ValueError):
        pass
    start, end = week_bounds(prev_week)
    return compute_metrics(in_window(events, start, end), min_n)


# ---------------------------------------------------------------------------
# Trials ledger
# ---------------------------------------------------------------------------
def _is_num(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _is_date(value: object) -> bool:
    if not isinstance(value, str) or not DATE_RE.match(value):
        return False
    try:
        dt.datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def _nonblank(value: object, limit: int) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value.strip()) <= limit


def validate_trial(row: object) -> tuple[dict | None, list[str]]:
    if not isinstance(row, dict):
        return None, ["不是 JSON 对象"]
    errs = [f"缺字段 {f}" for f in TRIAL_FIELDS if f not in row]
    if errs:
        return None, errs
    t = dict(row)
    if not _nonblank(t["id"], 64):
        errs.append("id 为空或过长")
    if not _nonblank(t["title"], 120):
        errs.append("title 为空或超过 120 字")
    if not _is_date(t["registered"]):
        errs.append("registered 不是 YYYY-MM-DD")
    if not valid_metric_key(t["metric"]):
        errs.append(f"未知指标键 {t['metric']!r}")
    if t["baseline"] is not None and not _is_num(t["baseline"]):
        errs.append("baseline 不是数字或 null")
    direction = str(t["direction"]).strip().casefold() if isinstance(t["direction"], str) else ""
    if direction not in ("down", "up"):
        errs.append("direction 须为 down/up")
    if not _is_date(t["review_date"]):
        errs.append("review_date 不是 YYYY-MM-DD")
    if not _nonblank(t["revoke_if"], 200):
        errs.append("revoke_if 为空或超过 200 字")
    status = str(t["status"]).strip().casefold() if isinstance(t["status"], str) else ""
    if status not in ("active", "kept", "revoked"):
        errs.append("status 须为 active/kept/revoked")
    deferred = t["deferred"]
    if isinstance(deferred, bool) or not isinstance(deferred, int) or deferred < 0:
        errs.append("deferred 不是 0/1")
    elif deferred > 1:
        errs.append(f"deferred={deferred} > 1")
    if not _nonblank(t["undo"], 200):
        errs.append("undo 为空或超过 200 字")
    shared = t.get("shared_metric_with", [])
    if not isinstance(shared, list) or not all(isinstance(s, str) and s.strip() for s in shared):
        errs.append("shared_metric_with 不是 id 列表")
    if errs:
        return None, errs
    t.update(id=t["id"].strip(), metric=t["metric"], direction=direction, status=status,
             shared_metric_with=[s.strip() for s in shared])
    return t, []


def load_trials(repo: Path) -> tuple[dict[str, dict], list[str]]:
    """Valid trials by id (later row wins) plus one problem line per invalid row. Missing file = empty."""
    path = repo / "admin" / "trials.jsonl"
    trials: dict[str, dict] = {}
    problems: list[str] = []
    if not path.is_file():
        return trials, problems
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return trials, [f"trials.jsonl 无法读取：{exc.__class__.__name__}"]
    for no, line in enumerate(text.splitlines(), 1):
        line = line.strip("\r").strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            problems.append(f"trials.jsonl:L{no} 不是合法 JSON")
            continue
        trial, errs = validate_trial(row)
        if trial is None:
            ident = row.get("id") if isinstance(row, dict) else None
            problems.append(f"trials.jsonl:L{no}" + (f" (id={ident})" if ident else "") + " " + "；".join(errs))
            continue
        trials[trial["id"]] = trial
    return trials, problems


def sharing_problems(trials: dict[str, dict]) -> list[str]:
    out = []
    by_metric: dict[str, list[str]] = defaultdict(list)
    for t in trials.values():
        if t["status"] == "active":
            by_metric[t["metric"]].append(t["id"])
    for metric, ids in sorted(by_metric.items()):
        if len(ids) < 2:
            continue
        for i in ids:
            others = set(ids) - {i}
            if not others <= set(trials[i]["shared_metric_with"]):
                out.append(f"在试项 {i} 与 {', '.join(sorted(others))} 共用指标 {metric}，shared_metric_with 未互相登记")
    return out


def judge_trial(trial: dict, current: dict | None, baseline: float | None, shared: bool) -> dict:
    cur = current.get("value") if current else None
    if shared:
        verdict = "不可单独归因"
    elif cur is None or baseline is None:
        verdict = LOW_N
    elif trial["direction"] == "down":
        verdict = "已改善" if cur < baseline else "未改善"
    else:
        verdict = "已改善" if cur > baseline else "未改善"
    if trial["deferred"] == 1:
        advice = "保留" if verdict == "已改善" else "撤掉" if verdict == "未改善" else "保留或撤掉（二选一，不可再延期）"
    else:
        advice = {"已改善": "保留", "未改善": "撤掉"}.get(verdict, "可延期一次，或保留 / 撤掉")
    return {"id": trial["id"], "title": trial["title"], "metric": trial["metric"], "direction": trial["direction"],
            "baseline": baseline, "current": cur, "deferred": trial["deferred"],
            "verdict": verdict, "advice": advice, "undo": trial["undo"]}


def due_trials(trials: dict[str, dict], metrics: dict[str, dict], events: list[dict],
               week_end_date: str, min_n: int) -> list[dict]:
    active = [t for t in trials.values() if t["status"] == "active"]
    metric_users = Counter(t["metric"] for t in active)
    out = []
    for t in sorted(active, key=lambda x: x["id"]):
        if t["review_date"] > week_end_date:
            continue
        baseline = t["baseline"]
        if baseline is None:
            reg_start, reg_end = week_bounds(week_id(dt.date.fromisoformat(t["registered"])))
            baseline = compute_metrics(in_window(events, reg_start, reg_end), min_n).get(t["metric"], {}).get("value")
        shared = metric_users[t["metric"]] > 1 or bool(t["shared_metric_with"])
        current = metrics.get(t["metric"])
        if current is None and t["metric"].startswith("gate_deny:") and metrics["bypass_total"]["value"] is not None:
            current = {"value": 0, "n": None}  # no deny of that rule in a week that has events
        out.append(judge_trial(t, current, baseline, shared))
    return out


# ---------------------------------------------------------------------------
# Subtraction candidates
# ---------------------------------------------------------------------------
def suspected_misblocks(window: list[dict], all_events: list[dict]) -> list[dict]:
    """Rule tags with >= 5 denies where >= half were followed by a bypass of the same user+task within 30 min."""
    bypass_ts: dict[tuple, list[dt.datetime]] = defaultdict(list)
    for e in all_events:
        if e["type"] == "bypass" and e.get("task"):
            bypass_ts[(e["user"], e["task"])].append(_parse_ts(e["ts"]))
    denies: dict[str, list[dict]] = defaultdict(list)
    for e in window:
        if e["type"] == "hook_gate" and e.get("event") == "deny":
            denies[e.get("rule_tag") or "-"].append(e)
    out = []
    for tag in sorted(denies):
        rows = denies[tag]
        if len(rows) < MISBLOCK_MIN_DENIES:
            continue
        hit = 0
        for d in rows:
            if not d.get("task"):
                continue
            t0 = _parse_ts(d["ts"])
            if any(t0 <= b <= t0 + MISBLOCK_WINDOW for b in bypass_ts.get((d["user"], d["task"]), ())):
                hit += 1
        ratio = hit / len(rows)
        if ratio >= MISBLOCK_RATIO:
            out.append({"rule_tag": tag, "denies": len(rows), "followed_by_bypass": hit, "ratio": ratio})
    return out


def low_efficiency_cards(window: list[dict]) -> list[dict]:
    out = []
    for card, row in agg.by_lesson_card(window).items():
        if row["recalls"] >= CARD_MIN_RECALLS:
            ratio = row["corrections_after_recall"] / row["recalls"]
            if ratio >= CARD_RATIO:
                out.append({"card": card, "recalls": row["recalls"],
                            "corrections_after_recall": row["corrections_after_recall"], "ratio": ratio})
    return out


def idle_cards(all_events: list[dict], end: dt.datetime) -> list[dict]:
    """Active cards (latest lesson_card event, whole history) with 0 recalls in the last 4 weeks.

    Cards first seen inside the 4-week span are skipped: they have not had the full period to be recalled.
    """
    lo = _fmt_ts(end - dt.timedelta(weeks=IDLE_WEEKS))
    hi = _fmt_ts(end)
    latest: dict[str, dict] = {}
    first_seen: dict[str, str] = {}
    recalled: set[str] = set()
    for e in all_events:
        if e["ts"] >= hi:
            continue
        card = e.get("card")
        if not card:
            continue
        if e["type"] == "lesson_card":
            if card not in latest or e["ts"] >= latest[card]["ts"]:
                latest[card] = e
            if card not in first_seen or e["ts"] < first_seen[card]:
                first_seen[card] = e["ts"]
        elif e["type"] == "lesson_recall" and e["ts"] >= lo:
            recalled.add(card)
    return [{"card": c, "last_card_event": latest[c]["ts"]} for c in sorted(latest)
            if latest[c].get("status") == "active" and c not in recalled and first_seen[c] < lo]


# ---------------------------------------------------------------------------
# Stage table
# ---------------------------------------------------------------------------
def _cell(metrics: dict, key: str) -> str:
    m = metrics.get(key)
    if not m or (m.get("value") is None and not m.get("n")):
        return f"{key}={NO_DATA}"
    if m["value"] is None:
        return f"{key}={LOW_N}(n={m['n']})"
    return f"{key}={_fmt(m['value'])}"


def stage_table(metrics: dict, window: list[dict]) -> list[dict]:
    reviews = [e for e in window if e["type"] == "review" and e.get("kind") == "outcome"]
    if reviews:
        review_cell = (f"review_findings={sum(agg._int(e.get('findings')) for e in reviews)}，"
                       f"review_confirmed={sum(agg._int(e.get('confirmed')) for e in reviews)}")
    else:
        review_cell = f"review findings/confirmed={NO_DATA}"
    return [
        {"stage": "需求", "cells": [_cell(metrics, "corrections_per_task_day")]},
        {"stage": "开发", "cells": [_cell(metrics, "rework_files_per_task"), _cell(metrics, "first_green_index_median")]},
        {"stage": "测试", "cells": [_cell(metrics, "test_green_rate"), _cell(metrics, "bypass:skip_tests")]},
        {"stage": "验收", "cells": [review_cell, _cell(metrics, "commits_after_review_per_task")]},
    ]


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def overview(window: list[dict]) -> dict:
    s = agg.summarize(window)
    return {"events": s["events"], "users": s["users"], "installs": s["installs"]}


def build(repo: Path, week: str, min_n: int) -> dict:
    events = agg.load_events(repo, "")
    start, end = week_bounds(week)
    prev_week = shift_week(week, -1)
    window = in_window(events, start, end)
    prev_start, prev_end = week_bounds(prev_week)
    metrics = compute_metrics(window, min_n)
    prev_metrics = load_prev_metrics(repo, prev_week, events, min_n)
    trials, problems = load_trials(repo)
    problems += sharing_problems(trials)
    week_end_date = (end - dt.timedelta(days=1)).date().isoformat()
    due = due_trials(trials, metrics, events, week_end_date, min_n)
    return {
        "week": week, "prev_week": prev_week, "min_n": min_n,
        "window": {"start": _fmt_ts(start), "end": _fmt_ts(end)},
        "overview": {"this": overview(window), "prev": overview(in_window(events, prev_start, prev_end))},
        "metrics": metrics, "metrics_prev": prev_metrics,
        "trials_due": due,
        "subtraction": {
            "suspected_misblock": suspected_misblocks(window, events),
            "low_efficiency_cards": low_efficiency_cards(window),
            "idle_cards": idle_cards(events, end),
            "trials_not_improved": [d["id"] for d in due if d["verdict"] == "未改善"],
        },
        "stages": stage_table(metrics, window),
        "trial_problems": problems,
    }


def _fmt(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, float) and not value.is_integer():
        return f"{value:.2f}"
    return str(int(value))


def _val(m: dict | None) -> str:
    if not m:
        return NO_DATA
    if m.get("value") is None:
        return NO_DATA if not m.get("n") else f"{LOW_N}(n={m['n']})"
    return _fmt(m["value"])


def _change(cur: dict | None, prev: dict | None) -> str:
    if not cur or not prev or cur.get("value") is None or prev.get("value") is None:
        return "-"
    return f"{cur['value'] - prev['value']:+.2f}"


def render_md(data: dict) -> str:
    L: list[str] = [f"# 团队周复盘 {data['week']}", "",
                    f"窗口 {data['window']['start']} ~ {data['window']['end']}（UTC，右开）；对比 {data['prev_week']}；min-n={data['min_n']}", ""]
    ov = data["overview"]
    L += ["## 1. 概况", "", "| 项 | 本周 | 上周 |", "|---|---|---|"]
    for key, label in (("events", "事件数"), ("users", "活跃用户数"), ("installs", "安装数")):
        L.append(f"| {label} | {ov['this'][key]} | {ov['prev'][key]} |")
    L += ["", "## 2. 指标周对比", "", "| 指标 | 本周 | 上周 | 变化 | 方向 |", "|---|---|---|---|---|"]
    keys = list(FIXED_KEYS) + sorted((set(data["metrics"]) | set(data["metrics_prev"])) - set(FIXED_KEYS))
    for key in keys:
        cur, prev = data["metrics"].get(key), data["metrics_prev"].get(key)
        L.append(f"| {key} | {_val(cur)} | {_val(prev)} | {_change(cur, prev)} | {metric_direction_note(key)} |")
    L += ["", "## 3. 到期在试项", ""]
    if data["trials_due"]:
        L += ["| id | 机制 | 指标 | 方向 | 基线 | 当前 | 判定 | 建议 | undo |", "|---|---|---|---|---|---|---|---|---|"]
        for d in data["trials_due"]:
            L.append(f"| {d['id']} | {d['title']} | {d['metric']} | {d['direction']} | {_fmt(d['baseline'])} | "
                     f"{_fmt(d['current'])} | {d['verdict']} | {d['advice']} | {d['undo']} |")
    else:
        L.append("本周无")
    sub = data["subtraction"]
    L += ["", "## 4. 减法候选（只列信号，不下结论）", ""]
    lines = []
    for r in sub["suspected_misblock"]:
        lines.append(f"- 疑似误拦（相关性提示，非因果）：{r['rule_tag']} deny {r['denies']} 次，其中 {r['followed_by_bypass']} 次"
                     f"在 30 分钟内同 user+task 出现 bypass（占比 {r['ratio']:.2f}）")
    for r in sub["low_efficiency_cards"]:
        lines.append(f"- 低效经验卡（建议改写）：{r['card']} recalls={r['recalls']}，"
                     f"recall 后纠正 {r['corrections_after_recall']}（比率 {r['ratio']:.2f}）")
    for r in sub["idle_cards"]:
        lines.append(f"- 闲置经验卡（建议归档）：{r['card']} 最近 {IDLE_WEEKS} 周 0 次 recall，最新卡片事件 {r['last_card_event']}")
    for i in sub["trials_not_improved"]:
        lines.append(f"- 到期未改善的在试项：{i}")
    L += lines or ["本周无"]
    L += ["", "## 5. 流程四段表", "", "| 阶段 | 取数 |", "|---|---|"]
    for row in data["stages"]:
        L.append(f"| {row['stage']} | {'；'.join(row['cells'])} |")
    L += ["", "## 6. 在试台账问题", ""]
    L += [f"- {p}" for p in data["trial_problems"]] or ["本周无"]
    L.append("")
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", required=True)
    ap.add_argument("--week", default="")
    ap.add_argument("--format", choices=("md", "json"), default="md")
    ap.add_argument("--min-n", type=int, default=DEFAULT_MIN_N)
    args = ap.parse_args(argv)
    repo = Path(args.repo).expanduser()
    if not repo.is_dir() or not (repo / ".git").exists():
        print("invalid --repo: not an existing git clone", file=sys.stderr)
        return 2
    if args.min_n < 1:
        print("invalid --min-n: must be >= 1", file=sys.stderr)
        return 2
    week = args.week.strip() or last_complete_week()
    try:
        parse_week(week)
    except ValueError:
        print("invalid --week (YYYY-Www)", file=sys.stderr)
        return 2
    data = build(repo, week, args.min_n)
    print(json.dumps(data, ensure_ascii=False, indent=2) if args.format == "json" else render_md(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
