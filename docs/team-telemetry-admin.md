# Team telemetry admin guide

Contract: `docs/team-telemetry-design.md`. Fields: `docs/telemetry-fields.md`.

## 1. Create the collect repo

Create an empty private git repository that every member can push to (branch `main`). Events are pushed as `<date>/<user>-<install>.jsonl`.

## 2. Publish team.json

```json
{"team_id": "team1", "collect_repo": "<git url>", "salt": "<shared salt>", "branch": "main"}
```

Optional: `upload_interval_hours`. Host it at an http(s) URL, or hand members the file.
Members join with `aicj install --team-config <URL|file://|path>` (Windows: `install.ps1 -TeamConfig`). Without the flag nothing is collected, no hook is registered, and neither team.json nor `aicj/telemetry/` is created (hook code is installed but dormant).

## 3. Aggregate

```bash
git clone -b main <collect repo> collect && python3 scripts/telemetry_aggregate.py --repo collect --since 2026-10-01 --format md
```

Output: totals, plus tables by day and by user hash for the families install, hook, task, rework and lesson. `--format json` gives the same data. Rows are re-validated and de-duplicated by event_id; raw events are never printed.

## 4. Limits

The salt is shared by the whole team, so anyone holding it can brute-force a known identity against the user hash. Treat hashes as pseudonymous, not anonymous. Rotating the salt breaks per-user continuity.
Members can inspect status with `aicj-telemetry status` and force an upload with `aicj-telemetry report --force`.
