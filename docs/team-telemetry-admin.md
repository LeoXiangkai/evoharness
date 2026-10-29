# Team telemetry admin guide

Contract: `docs/team-telemetry-design.md`. Fields: `docs/telemetry-fields.md`.

## 1. Create the collect repo

Create an empty private git repository that every member can push to (branch `main`). Events are pushed as `<date>/<user>-<install>.jsonl`. Any git host works; pick one every member reaches without a VPN (for teams in mainland China, Gitee).

Adding a member: invite their account to the collect repo with write access and have them accept it before installing. Members sign in once per machine: clone the repo over https so git stores the credential, or add their SSH key to the host. `aicj install --team-config` runs a read-only `git ls-remote` against the collect repo and warns when it cannot reach it; the install still completes and events wait locally. Write access is only proven by the first upload, so check `aicj-telemetry status` after a member's first day.

## 2. Publish team.json

```json
{"team_id": "team1", "collect_repo": "<git url>", "salt": "<shared salt>", "branch": "main"}
```

Optional: `framework_repo`: git URL of an EvoHarness mirror members can reach (for example on Gitee). `install.ps1 -TeamConfig` clones from it, and switches the origin of its own managed clone (`~\src\evoharness`) to it, unless `-RepoUrl` is given; keep the mirror in sync with every push. `upload_interval_hours`; `spool_max_mb` (default 20): when the local spool exceeds it, the oldest day files are deleted first. Host it at an http(s) URL, or hand members the file.
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

## Weekly review

Administrators run a weekly review on the collect repo: `python3 scripts/team_weekly.py --repo <collect repo clone> [--week YYYY-Www] [--format md|json] [--min-n 20]` prints the week's numbers, due trials and subtraction candidates (read-only, exit 2 when the repo is missing or not a git repository). Install the `team-weekly` skill with `--adapters team-admin`; it turns the report into one batch confirmation and writes decisions and the trial ledger under `admin/`. Contract and thresholds: `docs/team-weekly-design.md`.

### Rework cause, skill use and ask_user

A `--team-config` install (Windows `-TeamConfig`) always adds the `lesson` adapter, because rework causes come from its corrections log. Field contract: `docs/telemetry-fields.md`; design: `docs/team-signals-design.md`.

- `rework_share:A..G`: share of the week's corrections per cause (A requirement, B missed impact, C environment/tool/data, D code defect, E review, F test). The keyword rules are coarse; G is unclassified and only reported, not judged. When G is above 50% two weeks in a row the subtraction section asks you to tune `REWORK_RULES` and bump its version.
- `skill_corrected_ratio:<skill>`: corrections within 10 minutes after the skill in the same session, divided by its calls. Non-framework skills appear as `x-<hash>`.
- `ask_user_per_task_day`: AskUserQuestion questions per task-day; questions Claude asks in plain text are not counted.
- `corrections_per_task_day` and `ask_user_per_task_day` divide by active task-days: distinct user + task + day over any event that carries a task, not only `task_snapshot` (a snapshot needs a commit on the task branch, so a day of corrections before the first commit would otherwise have no denominator).
- `rework_share:*` uses only the corrections of the newest `cause_rules` version in the week. If last week's newest version differs, last week's shares show no data instead of being compared across keyword rules.
- Idle skill: a framework skill with 0 calls in the last 4 weeks is listed as a signal only; nothing is listed before the first `skill_use` event exists.
