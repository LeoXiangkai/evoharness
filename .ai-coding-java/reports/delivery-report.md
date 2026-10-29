# Delivery Report

Latest delivery for this repository. Overwrite this file on each delivery; history stays in git.

## Summary

Three new team telemetry signals for EvoHarness (design: `docs/team-signals-design.md`):

- rework cause: each correction is classified on the member machine into A–G by keyword rules `r1`; only
  the letter, the rule version and the skill used within 10 minutes before (same session) are uploaded;
- skill use: Skill tool calls and leading slash commands (built-in commands excluded); framework skills by
  name, others as a salted hash;
- ask_user: question count of each AskUserQuestion call.

Aggregate and weekly review gain `rework_share:<A..G>`, `skill_corrected_ratio:<skill>`,
`ask_user_per_task_day`, the four-stage table uses them, plus a G-share alert and idle-skill candidates.
A `--team-config` / `-TeamConfig` install adds the lesson adapter.

Changed files: `core/hooks/aicj_telemetry.py`, `core/hooks/telemetry-capture.py`, `core/manifest.json`,
`installer/aicj.py`, `scripts/windows/install.ps1`, `scripts/telemetry_aggregate.py`, `scripts/team_weekly.py`,
`docs/telemetry-fields.md`, `docs/team-telemetry-admin.md`, `tests/telemetry/test_team_signals.py`,
`tests/telemetry/test_team_e2e.py`.

## Verification

- command: `python3 -m pytest tests -q -p no:cacheprovider` — result: passed, 741 passed, 0 skipped
- command: `python3 scripts/sanitize_check.py` — result: ok, exit 0
- command: `python3 scripts/template_integrity_check.py` — result: ok, exit 0
- command: `python3 scripts/context_budget_check.py` (main checkout) — result: ok, exit 0
- command: `tests/telemetry/test_team_signals.py` — result: design tests S1–S9, S11 (42 cases) passed
- command: team e2e (S10): temporary-HOME install with `--team-config` (lesson adapter registered) → real hook
  entry for Skill / slash / AskUserQuestion → upload → aggregate → `team_weekly.py` → doctor → uninstall —
  result: passed; uploaded files contain no correction text or question text; skill names are uploaded verbatim (user decision 2026-10-29)

## Review

- P0: none
- P1: none
- P2: one scoped independent review (12 candidates); fixed: idle skills listed before 4 weeks of data exist, pre-rollout corrections without a cause counted as G (could fire the G alert), telemetry import on every plain prompt, hook exit code only checked in-process; kept as judgment calls: adapter skills in the idle list, hash-token helper reuse, report overwrite (repo convention)
- P2: known risk — skill-log pruning during collect can drop a skill-log row appended by a hook at the same
  moment (local association data only; worst case one missing `after_skill`)

## Not-tested

- item: `-TeamConfig` adding lesson in `scripts/windows/install.ps1` — reason: no Windows host — remaining risk: PowerShell edge cases
- item: keyword rule accuracy on real corrections — reason: no real data yet — remaining risk: high G share; watch the G-share alert
- item: Claude Code payload field names for Skill (`tool_input.skill`) and UserPromptSubmit (`prompt`) on a live session — reason: tested with synthetic payloads — remaining risk: empty signal if a field name differs

## Delivery decision

Merged into `main`, not pushed; push awaits user confirmation.
