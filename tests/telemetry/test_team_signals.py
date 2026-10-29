from __future__ import annotations

import datetime as dt
import io
import json
import sys

import pytest

from .conftest import REPO_ROOT, spool_events, write_log
from .test_capture import cap, run_hook
from .test_team_weekly import WEEK, ev, make_repo, metrics_for, sane, tk, tw

import aicj_telemetry as tel  # noqa: E402

LES = "aicj/lessons"
SKILL_LOG = "aicj/telemetry/skill-log.jsonl"


# S1 ---------------------------------------------------------------------------
@pytest.mark.parametrize("text,cause", [
    ("不是这个意思，我要的是按月汇总", "A"),
    ("需求口径理解错了", "A"),
    ("漏了列表页，其他地方也得改", "B"),
    ("You missed the second caller", "B"),
    ("测试报错了", "F"),  # conflict sentence: test wins over the symptom word
    ("这个断言写反了", "F"),
    ("命名不符合约定", "E"),
    ("Please follow the REVIEW comments", "E"),
    ("端口被占用，代理没配", "C"),
    ("数据库连不上", "C"),
    ("还是报错，空指针", "D"),
    ("there is a BUG here", "D"),
    ("你刚才写的函数名不对，应该叫 add_numbers，不是 add，测试也一起改。", "E"),  # problem before remedy
    ("测试也要补，另外命名不对", "F"),  # earliest keyword wins over rule order
    ("再来一次", "G"),
    ("", "G"),
    (None, "G"),
])
def test_rework_rules_classify_in_order_and_default_to_g(text, cause):
    assert tel.classify_rework(text) == cause


def _corrections(claude, rows):
    write_log(claude / LES / "corrections.jsonl", rows)
    tel.collect(claude)
    return [e for e in spool_events(claude) if e["type"] == "correction"]


# S2 ---------------------------------------------------------------------------
def test_correction_event_carries_cause_but_never_the_text(claude, team):
    text = "漏了导出接口 secret-phrase-xyz"
    evs = _corrections(claude, [{"ts": "2026-10-27T10:02:00Z", "session_id": "s1", "prompt": text}])
    assert [(e["cause"], e["cause_rules"]) for e in evs] == [("B", tel.REWORK_RULES_VERSION)]
    blob = json.dumps(evs, ensure_ascii=False)
    for piece in ("漏了", "导出接口", "secret-phrase-xyz", "prompt"):
        assert piece not in blob


# S3 ---------------------------------------------------------------------------
def test_known_skills_match_framework_skill_dirs():
    dirs = {p.name for p in (REPO_ROOT / "core" / "skills").iterdir() if p.is_dir()}
    dirs |= {p.name for p in REPO_ROOT.glob("adapters/*/skills/*") if p.is_dir()}
    assert tel.KNOWN_SKILLS == dirs


# S4 ---------------------------------------------------------------------------
def test_unknown_skill_is_hashed_and_known_kept():
    assert tel.skill_token("s", "code-review") == "code-review"
    hashed = tel.skill_token("s", "my-private:skill")
    assert hashed.startswith("x-") and len(hashed) == 12 and "private" not in hashed
    assert tel.skill_token("s", hashed) == hashed
    assert tel.skill_token("other-salt", "my-private:skill") != hashed
    assert tel.skill_token("s", "  ") is None and tel.skill_token("s", None) is None


@pytest.mark.parametrize("prompt,name", [
    ("/req-intake 整理一下", "req-intake"),
    ("  /prd", "prd"),
    ("/help", None),
    ("/Compact keep it short", None),
    ("/usr/local/bin is a path", None),
    ("看下 /prd 的输出", None),
    ("", None),
])
def test_slash_command_parsing(prompt, name):
    assert cap.slash_command(prompt) == name


def test_hook_emits_skill_use_for_tool_and_slash_but_not_builtin(claude, team):
    base = {"session_id": "s1", "cwd": str(claude)}
    for payload in (
        {**base, "hook_event_name": "PostToolUse", "tool_name": "Skill", "tool_use_id": "t1",
         "tool_input": {"skill": "code-review", "args": "secret-arg"}},
        {**base, "hook_event_name": "UserPromptSubmit", "prompt": "/my-private-skill do it"},
        {**base, "hook_event_name": "UserPromptSubmit", "prompt": "/help"},
        {**base, "hook_event_name": "UserPromptSubmit", "prompt": "plain text"},
    ):
        r = run_hook(claude, payload)
        assert r.returncode == 0 and r.stdout == ""
    uses = [e for e in spool_events(claude) if e["type"] == "skill_use"]
    assert sorted((e["skill"][:2] if e["skill"].startswith("x-") else e["skill"], e["via"]) for e in uses) == [
        ("code-review", "tool"), ("x-", "slash")]
    blob = json.dumps(spool_events(claude))
    assert "my-private-skill" not in blob and "secret-arg" not in blob


# S5 ---------------------------------------------------------------------------
@pytest.mark.parametrize("skill_ts,skill_session,expect", [
    ("2026-10-27T10:00:00Z", "s1", True),   # 2 minutes before, same session
    ("2026-10-27T09:51:00Z", "s1", False),  # 11 minutes before
    ("2026-10-27T10:00:00Z", "s2", False),  # other session
    ("2026-10-27T10:03:00Z", "s1", False),  # after the correction
])
def test_after_skill_links_only_same_session_within_window(claude, team, skill_ts, skill_session, expect):
    now = dt.datetime(2026, 10, 28, tzinfo=dt.timezone.utc)
    assert tel.record_skill(claude, skill_session, "code-review", skill_ts)
    orig = tel._now
    tel._now = lambda: now
    try:
        evs = _corrections(claude, [{"ts": "2026-10-27T10:02:00Z", "session_id": "s1", "prompt": "review 不对"}])
    finally:
        tel._now = orig
    assert [e.get("after_skill") for e in evs] == (["code-review"] if expect else [None])


# S6 ---------------------------------------------------------------------------
def test_ask_user_counts_questions_and_dedupes_repeated_post_events(claude, team, tmp_path):
    qs = [{"question": "private question text?"}, {"question": "q2?"}]
    for event in ("PostToolUse", "PostToolUseFailure"):
        r = run_hook(claude, {"hook_event_name": event, "tool_name": "AskUserQuestion", "session_id": "s1",
                              "tool_use_id": "t9", "tool_input": {"questions": qs}})
        assert r.returncode == 0
    asks = [e for e in spool_events(claude) if e["type"] == "ask_user"]
    assert [e["questions"] for e in asks] == [2, 2]
    assert len({e["event_id"] for e in asks}) == 1
    write_log(tmp_path / "collect" / "2026-10-27" / "u.jsonl", asks)
    loaded = tw.agg.load_events(tmp_path / "collect", "")
    assert tw.agg.ask_user_summary(loaded)["questions"] == 2
    assert "private question text" not in json.dumps(spool_events(claude))


def test_ask_user_without_questions_emits_nothing(claude, team):
    run_hook(claude, {"hook_event_name": "PostToolUse", "tool_name": "AskUserQuestion", "tool_input": {}})
    assert not [e for e in spool_events(claude) if e["type"] == "ask_user"]


# S7 ---------------------------------------------------------------------------
def test_skill_log_prunes_rows_older_than_retention_and_stays_out_of_spool(claude, team):
    now = dt.datetime(2026, 10, 28, tzinfo=dt.timezone.utc)
    old = (now - dt.timedelta(days=tel.SKILL_LOG_DAYS, seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    fresh = (now - dt.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    tel.record_skill(claude, "s-old", "prd", old)
    tel.record_skill(claude, "s-new", "prd", fresh)
    orig = tel._now
    tel._now = lambda: now
    try:
        tel.collect(claude)
    finally:
        tel._now = orig
    rows = [json.loads(x) for x in (claude / SKILL_LOG).read_text(encoding="utf-8").splitlines()]
    assert [r["ts"] for r in rows] == [fresh]
    assert "s-new" not in (claude / SKILL_LOG).read_text(encoding="utf-8")  # session id is hashed
    spool = json.dumps(spool_events(claude))
    assert rows[0]["s"] not in spool


# S8 ---------------------------------------------------------------------------
def _signal_rows(n_corr=20):
    rows = [ev(i, "task_snapshot", "2026-10-20T10:00:00Z", task=f"{i:012x}") for i in range(1, 5)]
    causes = ["A"] * 5 + ["D"] * 3 + ["G"] * (n_corr - 8)
    rows += [ev(100 + i, "correction", "2026-10-21T10:00:00Z", cause=c, cause_rules="r1",
                **({"after_skill": "prd"} if i < 2 else {})) for i, c in enumerate(causes)]
    rows += [ev(200 + i, "ask_user", "2026-10-21T11:00:00Z", questions=q) for i, q in enumerate([1, 3] * 10)]
    rows += [ev(300 + i, "skill_use", "2026-10-21T12:00:00Z", skill="prd", via="tool" if i % 2 else "slash")
             for i in range(20)]
    return rows


def test_weekly_signal_metrics_exact_values():
    m = metrics_for(_signal_rows(), min_n=20)
    assert m["rework_share:A"]["value"] == pytest.approx(0.25)
    assert m["rework_share:D"]["value"] == pytest.approx(0.15)
    assert m["rework_share:G"]["value"] == pytest.approx(0.6)
    assert m["ask_user_per_task_day"]["value"] is None  # 4 task-days < min_n
    assert m["ask_user_per_task_day"]["n"] == 4
    assert m["skill_corrected_ratio:prd"]["value"] == pytest.approx(0.1)


def test_weekly_signal_metrics_below_min_n_are_null():
    m = metrics_for(_signal_rows(n_corr=10), min_n=20)
    assert m["rework_share:A"]["value"] is None and m["rework_share:A"]["n"] == 10
    m = metrics_for(_signal_rows(), min_n=4)
    assert m["ask_user_per_task_day"]["value"] == pytest.approx(40 / 4)


def test_aggregate_signal_families():
    events = sane(_signal_rows())
    rc = tw.agg.by_rework_cause(events)
    assert rc["r1"]["total"] == 20 and rc["r1"]["counts"] == {"A": 5, "D": 3, "G": 12}
    assert rc["r1"]["g_share"] == pytest.approx(0.6)
    assert tw.agg.by_skill(events)["prd"] == {"calls": 20, "tool": 10, "slash": 10, "corrected": 2}
    asks = tw.agg.ask_user_summary(events)
    assert (asks["calls"], asks["questions"], asks["task_days"]) == (20, 40, 4)


def test_g_share_alert_needs_two_weeks_above_half():
    this = sane(_signal_rows())
    prev_hi = sane([ev(500 + i, "correction", "2026-10-14T10:00:00Z", cause="G" if i < 15 else "A", cause_rules="r1")
                    for i in range(20)])
    prev_lo = sane([ev(600 + i, "correction", "2026-10-14T10:00:00Z", cause="G" if i < 10 else "A", cause_rules="r1")
                    for i in range(20)])
    assert tw.rework_g_alert(this, prev_hi, 20) == {"this": pytest.approx(0.6), "prev": pytest.approx(0.75)}
    assert tw.rework_g_alert(this, prev_lo, 20) is None
    assert tw.rework_g_alert(this, prev_hi[:10], 20) is None


def test_g_share_alert_skips_weeks_under_different_rules():
    this = sane(_signal_rows())
    prev_r2 = sane([ev(700 + i, "correction", "2026-10-14T10:00:00Z", cause="G", cause_rules="r2") for i in range(20)])
    assert tw.rework_g_alert(this, prev_r2, 20) is None


def test_idle_skills_listed_only_once_skill_signal_exists():
    end = dt.datetime(2026, 10, 26, tzinfo=dt.timezone.utc)
    assert tw.idle_skills([], end) == []
    used = sane([ev(1, "skill_use", "2026-10-20T10:00:00Z", skill="prd", via="tool"),
                 ev(2, "skill_use", "2026-09-01T10:00:00Z", skill="flow", via="tool")])
    idle = tw.idle_skills(used, end)
    assert "prd" not in idle and "flow" in idle and set(idle) == tel.KNOWN_SKILLS - {"prd"}


# S9 ---------------------------------------------------------------------------
def test_stage_table_uses_signal_metrics(tmp_path):
    repo = make_repo(tmp_path, {"2026-10-21": _signal_rows()})
    data = tw.build(repo, WEEK, 20)
    cells = {r["stage"]: " ".join(r["cells"]) for r in data["stages"]}
    assert f"rework_share:A={tw._fmt(0.25)}" in cells["需求"]
    assert "ask_user_per_task_day=" in cells["需求"] and "prd 调用=20" in cells["需求"]
    assert "skill_corrected_ratio:prd=" in cells["需求"] and "req-intake 调用=0" in cells["需求"]
    assert "rework_share:C=" in cells["开发"] and "rework_share:D=" in cells["开发"]
    assert "rework_share:F=" in cells["测试"]
    assert "rework_share:E=" in cells["验收"] and "code-review 调用=0" in cells["验收"]


def test_stage_table_marks_skill_calls_no_data_before_signal(tmp_path):
    rows = [r for r in _signal_rows() if r["type"] != "skill_use"]
    data = tw.build(make_repo(tmp_path, {"2026-10-21": rows}), WEEK, 20)
    req = " ".join(next(r for r in data["stages"] if r["stage"] == "需求")["cells"])
    assert f"prd 调用={tw.NO_DATA}" in req


# S11 --------------------------------------------------------------------------
def test_classifier_failure_still_emits_correction_as_g(claude, team, monkeypatch):
    def boom(_):
        raise RuntimeError("x")
    monkeypatch.setattr(tel, "classify_rework", boom)
    evs = _corrections(claude, [{"ts": "2026-10-27T10:02:00Z", "session_id": "s1", "prompt": "漏了"}])
    assert [e["cause"] for e in evs] == ["G"]


def test_skill_log_failure_keeps_correction_and_hook_silent(claude, team, monkeypatch):
    (claude / "aicj" / "telemetry").mkdir(parents=True, exist_ok=True)
    (claude / SKILL_LOG).mkdir()  # a directory where the log file should be: every log read/write fails
    assert tel.record_skill(claude, "s1", "prd") is False
    evs = _corrections(claude, [{"ts": "2026-10-27T10:02:00Z", "session_id": "s1", "prompt": "漏了"}])
    assert [e["cause"] for e in evs] == ["B"]

    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(tel, "emit", boom)
    monkeypatch.setenv("AICJ_CLAUDE_DIR", str(claude))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
        {"hook_event_name": "PostToolUse", "tool_name": "Skill", "tool_input": {"skill": "prd"}})))
    cap.main()  # must return normally: no exception escapes the new signal path


def test_idle_skills_wait_until_collection_covers_the_window():
    end = dt.datetime(2026, 10, 26, tzinfo=dt.timezone.utc)
    recent_only = sane([ev(1, "skill_use", "2026-10-20T10:00:00Z", skill="prd", via="tool")])
    assert tw.idle_skills(recent_only, end) == []


def test_corrections_collected_before_rules_are_left_out_of_rework_metrics():
    legacy = [ev(700 + i, "correction", "2026-10-21T10:00:00Z") for i in range(30)]
    m = metrics_for(_signal_rows() + legacy, min_n=20)
    assert m["rework_share:G"]["n"] == 20 and m["rework_share:G"]["value"] == pytest.approx(0.6)
    old_weeks = sane(legacy)
    assert tw.rework_g_alert(old_weeks, old_weeks, 20) is None


def test_hook_process_exits_zero_when_skill_log_is_unwritable(claude, team):
    (claude / "aicj" / "telemetry").mkdir(parents=True, exist_ok=True)
    (claude / SKILL_LOG).mkdir()
    for payload in ({"hook_event_name": "PostToolUse", "tool_name": "Skill", "session_id": "s1",
                     "tool_use_id": "t1", "tool_input": {"skill": "prd"}},
                    {"hook_event_name": "UserPromptSubmit", "session_id": "s1", "prompt": "/prd go"}):
        r = run_hook(claude, payload)
        assert (r.returncode, r.stdout) == (0, "")
    assert [e["via"] for e in spool_events(claude) if e["type"] == "skill_use"] == ["tool", "slash"]


def test_task_day_denominator_counts_days_without_a_snapshot():
    # day 1: a correction and a question before any commit (no snapshot); day 2: a snapshot
    rows = [ev(1, "correction", "2026-10-20T10:00:00Z", task=tk(1)),
            ev(2, "ask_user", "2026-10-20T10:01:00Z", task=tk(1), questions=3),
            ev(3, "task_snapshot", "2026-10-21T10:00:00Z", task=tk(1)),
            ev(4, "install", "2026-10-21T11:00:00Z", command="install", ok=True)]  # no task: not a task-day
    m = metrics_for(rows, min_n=1)
    assert m["corrections_per_task_day"] == {"value": pytest.approx(1 / 2), "n": 2}
    assert m["ask_user_per_task_day"] == {"value": pytest.approx(3 / 2), "n": 2}


def test_rework_shares_use_the_newest_rules_and_skip_a_cross_version_baseline():
    old = [ev(800 + i, "correction", "2026-10-21T10:00:00Z", cause="D", cause_rules="r1") for i in range(3)]
    new = [ev(810 + i, "correction", "2026-10-22T10:00:00Z", cause="E", cause_rules="r2") for i in range(2)]
    m = metrics_for(old + new, min_n=1)
    assert m["rework_share:E"] == {"value": pytest.approx(1.0), "n": 2} and m["rework_share:D"]["value"] == 0
    window, prev = sane(new), sane(old)
    assert (tw.rules_version(window), tw.rules_version(prev)) == ("r2", "r1")
    prev_m = {"rework_share:D": {"value": 1.0, "n": 3}, "test_green_rate": {"value": 0.5, "n": 4}}
    dropped = tw.drop_cross_version_rework(prev_m, "r2", "r1")
    assert dropped == {"rework_share:D": {"value": None, "n": 0}, "test_green_rate": {"value": 0.5, "n": 4}}
    assert tw.drop_cross_version_rework(prev_m, "r2", "r2") is prev_m
    assert tw.drop_cross_version_rework(prev_m, "r2", None) is prev_m
