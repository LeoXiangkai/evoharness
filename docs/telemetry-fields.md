# Team telemetry fields

Authoritative schema: `SCHEMA` / `ENVELOPE` in `core/hooks/aicj_telemetry.py`. Contract: `docs/team-telemetry-design.md`.
Values are tokens (`[A-Za-z0-9._:+-]{1,64}`), hashes (`[0-9a-f]{8,64}`), ints, bools or fixed enums. No free text is ever collected.

## Envelope (every event)

| field | kind |
|---|---|
| schema | int |
| type | enum of the event types below |
| ts | UTC `YYYY-MM-DDTHH:MM:SSZ` |
| event_id | hash |
| team | token |
| user | salted hash token |
| install | hash |
| repo | hash |
| task | hash |
| version | token |
| platform | darwin, linux, windows, wsl |

## Event types

| type | fields |
|---|---|
| install | command (install, uninstall, doctor), ok, error_kind, packs, adapters, hook_mode, failed_checks, python |
| project_init | ok, error_kind |
| hook_gate | hook, event (deny, ask_dropped), mode (warn, block, unknown), rule_tag, rule_hash, tool |
| bypass | kind (no_verify, hook_mode_override, skip_tests) |
| test_run | framework, passed, failed, skipped, errors, green |
| review | kind (scope, outcome), review_gate, risk_signals, file_count, changed_lines, reviewers, findings, confirmed |
| task_snapshot | commits, files_changed, files_touched_repeatedly, commits_after_review |
| verify_probe | overall, layers, failed_layers |
| correction | (no fields) |
| lesson_card | card, level (hint, warn, block), scope_kind (global, path), status (active, retired), created |
| lesson_recall | card, level (hint, warn, block) |

Aggregate output (`scripts/telemetry_aggregate.py`) derives, without adding event fields: hook gate counts by mode, test passed/failed/skipped sums, lesson recalls, By task (test runs, first green index) and By lesson card (recalls, corrections after first recall; best-effort by task hash and ts).
