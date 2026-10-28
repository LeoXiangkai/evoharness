# Delivery Report

Latest delivery for this repository. Overwrite this file on each delivery; history stays in git.

## Summary

Team telemetry for EvoHarness: default-on, stdlib-only collection of install health, hook gate and bypass,
task convergence, rework signals (test runs, corrections) and lesson cards; local de-identification with a
field whitelist; daily upload to a shared git repository; admin aggregation script. Also: display name
EvoHarness and GitHub repository renamed to `evoharness` (runtime identifiers `aicj` / `.ai-coding-java/` unchanged).

Changed files: `core/hooks/aicj_telemetry.py`, `core/hooks/telemetry-*.py`, `core/bin/aicj-telemetry`,
`installer/lib/telemetry.py`, `installer/lib/{engine,doctor,manifest}.py`, `installer/aicj.py`,
`scripts/windows/install.ps1`, `scripts/telemetry_aggregate.py`, `docs/team-telemetry-*.md`,
`docs/telemetry-fields.md`, `tests/telemetry/*`.

## Verification

- command: `python3 -m pytest tests -q -p no:cacheprovider` — result: passed, 648 passed, 0 skipped
- command: `python3 scripts/sanitize_check.py` — result: ok, exit 0
- command: `python3 scripts/template_integrity_check.py` — result: ok, exit 0
- command: `python3 scripts/context_budget_check.py` — result: ok, exit 0
- command: temporary-HOME e2e (install `--team-config` → hook events → report → aggregate → doctor → uninstall)
  — result: passed; 9 events uploaded, re-upload 0, PII grep 0 hits, no telemetry hooks or team.json left after uninstall

## Review

- P0: none
- P1: none open; four independent review rounds, all findings fixed
- P2: three edge cases documented under "Known limitations" in `docs/team-telemetry-design.md`

## Not-tested

- item: real Windows machine install — reason: no Windows host available — remaining risk: PowerShell/path edge cases
- item: hooks firing inside a live Claude Code session — reason: verified via direct hook entry points only — remaining risk: event payload shape drift
- item: PostToolUse on non-zero exit — reason: depends on Claude Code behavior — remaining risk: failed test runs under-counted
- item: http(s) team-config URL — reason: only local file used — remaining risk: fetch/TLS errors

## Delivery decision

Merged into `main` and pushed. Operations phase (daily optimization proposal → admin approval) not started.
