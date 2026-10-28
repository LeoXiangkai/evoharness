"""Team weekly review script, adapter packaging and skill contract (docs/team-weekly-design.md §6)."""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys

import pytest

from .conftest import REPO_ROOT, git, write_log

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import team_weekly as tw  # noqa: E402
import telemetry_aggregate as agg  # noqa: E402

SCRIPT = REPO_ROOT / "scripts" / "team_weekly.py"
AICJ = REPO_ROOT / "installer" / "aicj.py"
SKILL = REPO_ROOT / "adapters" / "team-admin" / "skills" / "team-weekly" / "SKILL.md"
USER = "a" * 16
USER2 = "b" * 16
WEEK = "2026-W43"  # 2026-10-19 .. 2026-10-25
PREV = "2026-W42"
CARD = "c" * 12
CARD2 = "d" * 12


def tk(n: int) -> str:
    return f"{n:012x}"


def ev(n, etype, ts, user=USER, **data):
    row = {"schema": 1, "type": etype, "ts": ts, "event_id": f"{n:016x}", "team": "team1", "user": user,
           "install": "b" * 8, "platform": "darwin"}
    row.update(data)
    return row


def sane(rows):
    """Events as the aggregate sees them: only rows that survive telemetry sanitize."""
    out = [tw.agg.tel.sanitize(r) for r in rows]
    assert all(out), "fixture event rejected by sanitize"
    return out


def make_repo(tmp_path, rows_by_day: dict[str, list[dict]], trials=None, name="collect"):
    repo = tmp_path / name
    repo.mkdir()
    git(tmp_path, "init", "-q", str(repo))
    for day, rows in rows_by_day.items():
        write_log(repo / day / "u.jsonl", rows)
    if trials is not None:
        (repo / "admin").mkdir()
        text = "".join((t if isinstance(t, str) else json.dumps(t)) + "\n" for t in trials)
        (repo / "admin" / "trials.jsonl").write_text(text, encoding="utf-8")
    return repo


def trial(**over):
    row = {"id": "t1", "title": "gate tweak", "registered": "2026-10-05", "metric": "test_green_rate",
           "baseline": 0.5, "direction": "up", "review_date": "2026-10-25", "revoke_if": "no gain",
           "shared_metric_with": [], "deferred": 0, "status": "active", "undo": "git revert abc"}
    row.update(over)
    return row


def green_runs(n_total, n_green, start=100, day="2026-10-20"):
    return [ev(start + i, "test_run", f"{day}T10:{i % 60:02d}:00Z", task=tk(i % 7 + 1), green=i < n_green)
            for i in range(n_total)]


def metrics_for(rows, min_n=20):
    return tw.compute_metrics(sane(rows), min_n)


# [T1]
def test_t1_week_window_boundaries_and_year_crossing():
    start, end = tw.week_bounds(WEEK)
    assert (start.isoformat(), end.isoformat()) == ("2026-10-19T00:00:00+00:00", "2026-10-26T00:00:00+00:00")
    rows = sane([ev(1, "correction", "2026-10-25T23:59:59Z"), ev(2, "correction", "2026-10-26T00:00:00Z"),
                 ev(3, "correction", "2026-10-19T00:00:00Z"), ev(4, "correction", "2026-10-18T23:59:59Z")])
    got = {e["event_id"] for e in tw.in_window(rows, start, end)}
    assert got == {f"{1:016x}", f"{3:016x}"}
    assert tw.week_bounds("2020-W53")[0].date() == dt.date(2020, 12, 28)
    assert tw.week_id(dt.date(2021, 1, 3)) == "2020-W53"
    assert tw.week_id(dt.date(2021, 1, 4)) == "2021-W01"
    assert tw.shift_week("2021-W01", -1) == "2020-W53"
    with pytest.raises(ValueError):
        tw.parse_week("2021-W53")


# [T2]
def test_t2_ratio_below_min_n_is_null_and_rendered_as_insufficient(tmp_path):
    assert metrics_for(green_runs(19, 10))["test_green_rate"] == {"value": None, "n": 19}
    assert metrics_for(green_runs(20, 10))["test_green_rate"] == {"value": 0.5, "n": 20}
    repo = make_repo(tmp_path, {"2026-10-20": green_runs(19, 10)})
    md = tw.render_md(tw.build(repo, WEEK, 20))
    row = next(ln for ln in md.splitlines() if ln.startswith("| test_green_rate |"))
    assert "样本不足(n=19)" in row


# [T3]
@pytest.mark.parametrize("direction,baseline,current,verdict", [
    ("down", 0.5, 0.3, "已改善"), ("down", 0.5, 0.5, "未改善"), ("down", 0.5, 0.7, "未改善"),
    ("up", 0.5, 0.7, "已改善"), ("up", 0.5, 0.5, "未改善"), ("up", 0.5, 0.3, "未改善"),
])
def test_t3_trial_verdict_both_directions(direction, baseline, current, verdict):
    t, errs = tw.validate_trial(trial(direction=direction))
    assert not errs
    res = tw.judge_trial(t, {"value": current, "n": 30}, baseline, False)
    assert res["verdict"] == verdict
    assert tw.judge_trial(t, {"value": None, "n": 3}, baseline, False)["verdict"] == "样本不足"


# [T4]
def test_t4_shared_metric_is_not_attributable():
    a, _ = tw.validate_trial(trial(id="a", shared_metric_with=["b"]))
    b, _ = tw.validate_trial(trial(id="b", shared_metric_with=["a"]))
    trials = {"a": a, "b": b}
    assert tw.sharing_problems(trials) == []
    metrics = {"test_green_rate": {"value": 0.9, "n": 40}}
    due = tw.due_trials(trials, metrics, [], "2026-10-25", 20)
    assert [d["verdict"] for d in due] == ["不可单独归因", "不可单独归因"]
    # same metric without mutual registration: still not attributable, and flagged in section 6
    c, _ = tw.validate_trial(trial(id="c"))
    d, _ = tw.validate_trial(trial(id="d"))
    assert len(tw.sharing_problems({"c": c, "d": d})) == 2
    assert {x["verdict"] for x in tw.due_trials({"c": c, "d": d}, metrics, [], "2026-10-25", 20)} == {"不可单独归因"}


# [T5]
def test_t5_deferred_trial_only_keep_or_revoke():
    t, _ = tw.validate_trial(trial(deferred=1))
    improved = tw.judge_trial(t, {"value": 0.9, "n": 40}, 0.5, False)
    worse = tw.judge_trial(t, {"value": 0.1, "n": 40}, 0.5, False)
    unknown = tw.judge_trial(t, {"value": None, "n": 4}, 0.5, False)
    assert (improved["advice"], worse["advice"]) == ("保留", "撤掉")
    assert "延期" not in unknown["advice"].replace("不可再延期", "") and "保留" in unknown["advice"] and "撤掉" in unknown["advice"]
    fresh, _ = tw.validate_trial(trial(deferred=0))
    assert "延期" in tw.judge_trial(fresh, {"value": None, "n": 4}, 0.5, False)["advice"]


# [T6]
def test_t6_bad_ledger_rows_are_reported_and_do_not_abort(tmp_path):
    good = trial(id="good")
    rows = [
        good,
        "{not json",
        trial(id="badkey", metric="nope_metric"),
        {k: v for k, v in trial(id="missing").items() if k != "undo"},
        trial(id="defer2", deferred=2),
        [1, 2],
        trial(id="blankundo", undo="  "),
    ]
    repo = make_repo(tmp_path, {"2026-10-20": green_runs(25, 20)}, trials=rows)
    data = tw.build(repo, WEEK, 20)
    probs = "\n".join(data["trial_problems"])
    assert len(data["trial_problems"]) == 6
    for needle in ("L2 不是合法 JSON", "id=badkey", "未知指标键", "缺字段 undo", "deferred=2 > 1", "不是 JSON 对象", "id=blankundo"):
        assert needle in probs
    assert [d["id"] for d in data["trials_due"]] == ["good"]
    assert "id=badkey" in tw.render_md(data)


# [T7]
def _deny_rows(tag, n, start=1000):
    return [ev(start + i, "hook_gate", f"2026-10-20T10:{i:02d}:00Z", event="deny", rule_tag=tag, mode="block",
               task=tk(i + 1)) for i in range(n)]


def _misblock(denies, bypasses):
    rows = sane(denies + bypasses)
    return tw.suspected_misblocks(rows, rows)


def test_t7_misblock_requires_bypass_within_30min_same_user_and_task():
    denies = _deny_rows("r1", 5)

    def by(i, minutes, user=USER, task=None, n=2000):
        return ev(n + i, "bypass", f"2026-10-20T10:{i:02d}:00Z", user=user, task=task or tk(i + 1), kind="no_verify") \
            if minutes == 0 else ev(n + i, "bypass", (dt.datetime(2026, 10, 20, 10, i) + dt.timedelta(minutes=minutes))
                                    .strftime("%Y-%m-%dT%H:%M:%SZ"), user=user, task=task or tk(i + 1), kind="no_verify")

    hits = [by(i, 10) for i in range(3)]  # 3/5 = 0.6
    res = _misblock(denies, hits)
    assert res and res[0]["rule_tag"] == "r1" and res[0]["followed_by_bypass"] == 3 and res[0]["denies"] == 5
    assert _misblock(denies, [by(i, 30) for i in range(3)])  # exactly 30 min still counts
    assert _misblock(denies, [by(i, 31) for i in range(5)]) == []  # too late
    assert _misblock(denies, [by(i, 10, task=tk(99 + i)) for i in range(5)]) == []  # other task
    assert _misblock(denies, [by(i, 10, user=USER2) for i in range(5)]) == []  # other user
    assert _misblock(denies, [by(i, -5) for i in range(5)]) == []  # bypass before the deny
    assert _misblock(denies, [by(i, 10) for i in range(2)]) == []  # 2/5 = 0.4 < 0.5
    four = _deny_rows("r2", 4)
    assert _misblock(four, [by(i, 10, n=3000) for i in range(4)]) == []  # < 5 denies


# [T8]
def _recalls(card, n, corrected, base=4000):
    rows = []
    for i in range(n):
        t = tk(base + i)
        rows.append(ev(base + 2 * i, "lesson_recall", f"2026-10-20T09:{i:02d}:00Z", task=t, card=card, level="hint"))
        if i < corrected:
            rows.append(ev(base + 2 * i + 1, "correction", f"2026-10-20T11:{i:02d}:00Z", task=t))
    return rows


def test_t8_low_efficiency_card_threshold_boundaries():
    def low(n, corrected):
        return tw.low_efficiency_cards(sane(_recalls(CARD, n, corrected)))
    assert low(4, 4) == []  # recalls < 5
    assert low(5, 2) == []  # 0.4
    assert [r["card"] for r in low(5, 3)] == [CARD]  # 0.6
    assert low(6, 3)[0]["ratio"] == 0.5  # exactly the threshold


def test_t8_idle_card_candidates():
    _, end = tw.week_bounds(WEEK)
    card = lambda n, c, ts, status="active": ev(n, "lesson_card", ts, card=c, level="hint", scope_kind="global", status=status)
    rows = sane([
        card(1, CARD, "2026-08-01T00:00:00Z"),                                     # old, never recalled -> idle
        card(2, CARD2, "2026-08-01T00:00:00Z"),
        ev(3, "lesson_recall", "2026-10-10T00:00:00Z", card=CARD2, level="hint"),  # recalled recently -> not idle
        card(4, "e" * 12, "2026-08-01T00:00:00Z"),
        card(5, "e" * 12, "2026-09-01T00:00:00Z", status="retired"),              # latest event retired -> not idle
        card(6, "f" * 12, "2026-10-10T00:00:00Z"),                                 # born inside the 4 weeks -> skipped
        card(7, "9" * 12, "2026-08-01T00:00:00Z"),
        ev(8, "lesson_recall", "2026-09-20T00:00:00Z", card="9" * 12, level="hint"),  # recall just outside 4 weeks
    ])
    got = [r["card"] for r in tw.idle_cards(rows, end)]
    assert got == sorted([CARD, "9" * 12])
    edge = sane([card(1, CARD, "2026-08-01T00:00:00Z"),
                 ev(2, "lesson_recall", "2026-09-28T00:00:00Z", card=CARD, level="hint")])  # exactly end-4w
    assert tw.idle_cards(edge, end) == []


# [T9]
def test_t9_stage_table_marks_missing_data(tmp_path):
    repo = make_repo(tmp_path, {})
    data = tw.build(repo, WEEK, 20)
    assert [s["stage"] for s in data["stages"]] == ["需求", "开发", "测试", "验收"]
    for stage in data["stages"]:
        assert all(tw.NO_DATA in c for c in stage["cells"]), stage
    md = tw.render_md(data)
    assert "本周无" in md
    # with only a few test runs: insufficient sample is distinguished from no data
    repo2 = make_repo(tmp_path, {"2026-10-20": green_runs(3, 1)}, name="c2")
    cells = next(s for s in tw.build(repo2, WEEK, 20)["stages"] if s["stage"] == "测试")["cells"]
    assert "样本不足" in cells[0] and tw.NO_DATA not in cells[0]


# [T10]
def test_t10_admin_dir_is_not_read_as_events(tmp_path):
    good = ev(1, "bypass", "2026-10-20T10:00:00Z", kind="no_verify")
    repo = make_repo(tmp_path, {"2026-10-20": [good]}, trials=[trial()])
    # even a perfectly valid event line under admin/ (in any nesting) must be ignored
    (repo / "admin" / "weekly").mkdir()
    (repo / "admin" / "weekly" / "x.jsonl").write_text(json.dumps(ev(2, "bypass", "2026-10-20T11:00:00Z", kind="skip_tests")) + "\n", encoding="utf-8")
    (repo / "admin" / "decisions.jsonl").write_text(json.dumps(ev(3, "bypass", "2026-10-20T12:00:00Z", kind="skip_tests")) + "\n", encoding="utf-8")
    events = agg.load_events(repo, "")
    assert [e["event_id"] for e in events] == [f"{1:016x}"]
    assert agg.summarize(events)["events"] == 1


# [T11]
def run_cli(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, encoding="utf-8")


def two_week_repo(tmp_path):
    prev = green_runs(30, 15, start=500, day="2026-10-14") + [ev(450, "bypass", "2026-10-14T01:00:00Z", kind="skip_tests")]
    cur = green_runs(30, 24, start=100, day="2026-10-20") + [
        ev(460, "bypass", "2026-10-20T01:00:00Z", kind="no_verify"),
        ev(461, "install", "2026-10-21T01:00:00Z", command="install", ok=False, error_kind="x"),
        ev(462, "install", "2026-10-21T02:00:00Z", user=USER2, command="install", ok=True),
    ]
    trials = [trial(), trial(id="bad", metric="zzz")]
    return make_repo(tmp_path, {"2026-10-14": prev, "2026-10-20": cur}, trials=trials)


def test_t11_cli_md_json_and_exit_codes(tmp_path):
    repo = two_week_repo(tmp_path)
    md = run_cli("--repo", str(repo), "--week", WEEK, "--format", "md")
    assert md.returncode == 0, md.stderr
    for head in ("## 1. 概况", "## 2. 指标周对比", "## 3. 到期在试项", "## 4. 减法候选", "## 5. 流程四段表", "## 6. 在试台账问题"):
        assert head in md.stdout
    greens = next(ln for ln in md.stdout.splitlines() if ln.startswith("| test_green_rate |"))
    assert "| 0.80 | 0.50 | +0.30 |" in greens
    assert "| t1 | gate tweak | test_green_rate | up | 0.50 | 0.80 | 已改善 | 保留 |" in md.stdout
    assert "id=bad" in md.stdout and "未知指标键" in md.stdout
    js = run_cli("--repo", str(repo), "--week", WEEK, "--format", "json")
    assert js.returncode == 0, js.stderr
    data = json.loads(js.stdout)
    assert data["week"] == WEEK and data["prev_week"] == PREV
    assert data["metrics"]["test_green_rate"]["value"] == 0.8
    assert data["metrics_prev"]["test_green_rate"]["value"] == 0.5
    assert data["metrics"]["bypass:no_verify"]["value"] == 1
    assert data["overview"]["this"]["installs"] == 2 and data["overview"]["this"]["users"] == 2
    assert data["trials_due"][0]["verdict"] == "已改善"
    assert len(data["trial_problems"]) == 1
    # prefers the archived previous-week snapshot when present
    (repo / "admin" / "weekly").mkdir()
    (repo / "admin" / "weekly" / f"{PREV}.json").write_text(json.dumps({"week": PREV, "min_n": 20, "metrics": {"test_green_rate": {"value": 0.6, "n": 99}}}), encoding="utf-8")
    again = json.loads(run_cli("--repo", str(repo), "--week", WEEK, "--format", "json").stdout)
    assert again["metrics_prev"]["test_green_rate"]["value"] == 0.6
    # a stale snapshot (different min_n) is ignored and recomputed from events
    stale = json.loads(run_cli("--repo", str(repo), "--week", WEEK, "--format", "json", "--min-n", "10").stdout)
    assert stale["metrics_prev"]["test_green_rate"]["value"] == 0.5


def test_t11_invalid_repo_exits_2(tmp_path):
    assert run_cli("--repo", str(tmp_path / "missing")).returncode == 2
    plain = tmp_path / "plain"
    plain.mkdir()
    assert run_cli("--repo", str(plain)).returncode == 2
    assert run_cli("--repo", str(plain), "--week", "bad").returncode == 2


def test_t11_missing_admin_dir_is_empty_ledger(tmp_path):
    repo = make_repo(tmp_path, {"2026-10-20": green_runs(25, 20)})
    res = run_cli("--repo", str(repo), "--week", WEEK, "--format", "json")
    assert res.returncode == 0, res.stderr
    data = json.loads(res.stdout)
    assert data["trials_due"] == [] and data["trial_problems"] == []


# [T12]
def _aicj(*args, home):
    env = dict(os.environ, AICJ_SKIP_HUD="1")
    env.pop("AICJ_CLAUDE_DIR", None)
    env.pop("AICJ_REVIEW_LOG_DIR", None)
    return subprocess.run([sys.executable, str(AICJ), *args, "--home", str(home), "--source", str(REPO_ROOT)],
                          capture_output=True, text=True, encoding="utf-8", cwd=str(REPO_ROOT), env=env)


def test_t12_adapter_install_doctor_uninstall(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    skill = home / ".claude" / "skills" / "team-weekly" / "SKILL.md"
    res = _aicj("install", "--packs", "none", "--adapters", "team-admin", home=home)
    assert res.returncode == 0, res.stdout + res.stderr
    assert skill.is_file()
    assert skill.read_bytes() == SKILL.read_bytes()
    doc = _aicj("doctor", home=home)
    assert doc.returncode == 0, doc.stdout + doc.stderr
    assert "FAIL" not in doc.stdout and "MISSING" not in doc.stdout
    un = _aicj("uninstall", home=home)
    assert un.returncode == 0, un.stdout + un.stderr
    assert not skill.exists() and not skill.parent.exists()
    leftovers = [p for p in home.rglob("*") if p.is_file() and "team-weekly" in p.as_posix()]
    assert leftovers == []


def test_t12_default_install_does_not_include_team_admin(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    assert _aicj("install", "--packs", "none", home=home).returncode == 0
    assert not (home / ".claude" / "skills" / "team-weekly").exists()


# [T13]
def test_t13_skill_keeps_required_constraints():
    text = SKILL.read_text(encoding="utf-8")
    assert text.startswith("---\nname: team-weekly\n")
    for needle in ("减法先行", "新增提案数不得超过删除数", "一次批量确认", "未勾选 = `unknown`", "不写 `rejected`",
                   "永不自动执行", "结论必须引用报告里的数字", "scripts/team_weekly.py"):
        assert needle in text, needle
