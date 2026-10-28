# Delivery Report

Latest delivery for this repository. Overwrite this file on each delivery; history stays in git.

## Summary

Weekly team review for EvoHarness (operations phase, first step): `scripts/team_weekly.py` reads the
telemetry collect repo and prints ISO-week metrics with week-over-week comparison, verdicts for due trials
from `admin/trials.jsonl` (improved / not improved / tie / low sample / not attributable), subtraction
candidates with sample size labels, and a four-stage table. The `team-weekly` skill (optional adapter
`team-admin`, admins only) turns the report into one batch confirmation and writes decisions and the
trial ledger back under `admin/`. Weekly cadence; nothing is executed automatically.

Changed files: `scripts/team_weekly.py`, `adapters/team-admin/**`, `tests/telemetry/test_team_weekly.py`,
`docs/team-weekly-design.md`, `docs/team-telemetry-admin.md`, `docs/runtime-skill-boundary.md`.

## Verification

- command: `python3 -m pytest tests -q -p no:cacheprovider` — result: passed, 693 passed, 0 skipped
- command: `python3 scripts/sanitize_check.py` — result: ok, exit 0
- command: `python3 scripts/template_integrity_check.py` — result: ok, exit 0
- command: `python3 scripts/context_budget_check.py` — result: ok, exit 0
- command: `team_weekly.py` on a temporary collect repo (30 test runs, 25 green) — result: test_green_rate 0.83 (n=30); with `--min-n 40` shown as low sample; missing repo exits 2
- command: temporary-HOME install `--adapters team-admin` → doctor → uninstall (test T12) — result: passed, no residue

## Review

- P0: none
- P1: none open; one independent review found 2 high and 3 medium issues (tie judged as not improved, review outcomes not written back to the ledger, low-sample candidates, gate_deny baseline, test gaps), all fixed
- P2: two limitations documented in `docs/team-weekly-design.md` (cross-week first-green bias, counts not normalized by activity)

## Not-tested

- item: weekly review on real team data — reason: collect repo not yet opened — remaining risk: thresholds (5 / 0.5 / 4 weeks / min-n 20) not calibrated
- item: `team-weekly` skill run end to end by an admin — reason: needs a real collect repo and admin session — remaining risk: write-back rows malformed
- item: real Windows machine install — reason: no Windows host available — remaining risk: PowerShell/path edge cases

## Delivery decision

Merged into `main`. Next: open the team collect repo and run the first weekly review.
