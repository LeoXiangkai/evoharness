# Team telemetry admin guide

Contract: `docs/team-telemetry-design.md`. Fields: `docs/telemetry-fields.md`.

## 1. Create the collect repo

Create an empty private git repository that every member can push to (branch `main`). Events are pushed as `<date>/<user>-<install>.jsonl`.

## 2. Publish team.json

```json
{"team_id": "team1", "collect_repo": "<git url>", "salt": "<shared salt>", "branch": "main"}
```

Optional: `upload_interval_hours`; `spool_max_mb` (default 20): when the local spool exceeds it, the oldest day files are deleted first. Host it at an http(s) URL, or hand members the file.
Members join with `aicj install --team-config <URL|file://|path>` (Windows: `install.ps1 -TeamConfig`). Without the flag nothing is collected, no hook is registered, and neither team.json nor `aicj/telemetry/` is created (hook code is installed but dormant).

## 3. Aggregate

```bash
git clone -b main <collect repo> collect && python3 scripts/telemetry_aggregate.py --repo collect --since 2026-10-01 --format md
```

Output: totals, plus tables by day and by user hash for the families install, hook, task_days, rework and lesson. `--format json` gives the same data. Rows are re-validated and de-duplicated by event_id across all files (the same event can appear in two `<user>-<install>.jsonl` files after a local reset); raw events are never printed.

Per-user detail counters: hook gates split by event and by mode (`gate_mode_warn|block|unknown`), test runs with `tests_passed|failed|skipped` sums, and `lesson_recalls` (shown separately from lesson cards).
- By task: per task hash, `test_runs` and `first_green_index` (1-based, ordered by ts; `-` if never green).
- By lesson card: per card, `recalls` and `corrections_after_recall` (corrections in the same task after the card's first recall). Best-effort: matched only by task hash and ts, so it indicates, not proves, a lesson effect.

`aicj doctor` shows a `telemetry-report` row only for team installs. It WARNs when `last_error` is set, when `last_success` is older than 3 x `upload_interval_hours`, or when there has never been a success and spool events have been pending longer than that.

## 4. Limits

The salt is shared by the whole team, so anyone holding it can brute-force a known identity against the user hash. Treat hashes as pseudonymous, not anonymous. Rotating the salt breaks per-user continuity.
Members can inspect status with `aicj-telemetry status` and force an upload with `aicj-telemetry report --force`.

Install health events (`install`, `uninstall`, `project_init`) are emitted on failure too (`ok=false`, `error_kind` = exception class name). A failing `--team-config` fetch happens before team.json exists, so it produces no event.

Wiping the telemetry directory of an ANONYMOUS user (no git email) re-keys that user and re-uploads converted history; users with a git email keep a stable hash.

Uploads use `ssh -o BatchMode=yes` unless the member already set `GIT_SSH_COMMAND`, `GIT_SSH` or `core.sshCommand`, in which case their SSH setup is left untouched. Host key checking is not relaxed: the collect host must already be in the member's `known_hosts`.

`aicj uninstall` removes `telemetry/collect-repo/` (a clone of the whole team's data) and `telemetry/report.lock`; spool, state and identity are kept like lesson data.

## Reading the numbers

- The `task_days` family counts `task_snapshot` events: one snapshot per task per day, so it is task-days, not distinct tasks (`task_rework.tasks` counts distinct user+task).
- `bypass` counts ATTEMPTS, including ones a gate later blocked. This is intended.
- A member who switches from the anonymous identity to an email identity appears as two user hashes.
- Quiet test runs (Gradle printing only `BUILD SUCCESSFUL`, `mvn -q`) are recorded as a `test_run` with framework and green only; their counts are treated as 0 in `tests_passed|failed|skipped`.
