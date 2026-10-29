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
| correction | cause (A, B, C, D, E, F, G), cause_rules, after_skill |
| lesson_card | card, level (hint, warn, block), scope_kind (global, path), status (active, retired), created |
| lesson_recall | card, level (hint, warn, block) |
| skill_use | skill, via (tool, slash) |
| ask_user | questions |

Aggregate output (`scripts/telemetry_aggregate.py`) derives, without adding event fields: hook gate counts by mode, test passed/failed/skipped sums, lesson recalls, By task (test runs, first green index) and By lesson card (recalls, corrections after first recall; best-effort by task hash and ts).

## Notes on test_run and token fields

- `test_run` without a parseable summary (Gradle success, `mvn -q`) carries only `framework` and `green`; count fields are omitted and aggregate as 0.
- `go test` without `-v` counts packages (ok/FAIL lines), not test cases.
- `verify_probe.layers` / `failed_layers` and `hook_gate.rule_tag` are matched against a closed vocabulary (`KNOWN_LAYERS`, `KNOWN_RULE_TAGS` in `aicj_telemetry.py`, taken from verify-probe `LAYER_SPECS` and the `[tag]` prefixes of push-review-gate / git-safe). Any other value is replaced by `x-` + the first 10 hex of sha256(salt+value).
- Phone numbers (including `+86` / `86` / `0086` prefixed forms) cause the field to be dropped from the event, not redacted in place. Builds that skip tests (`-DskipTests`, `-Dmaven.test.skip=true`, gradle `-x test` / `--exclude-task test`) produce no `test_run` event. Credential patterns include AIza keys, `glpat-`, Stripe `sk_`/`rk_` live/test keys and `xox[abprs]-` tokens.
- `correction.cause` is the rework cause class A-G, classified on the member machine by the keyword rules `REWORK_RULES` (version `cause_rules`, now `r2`: the cause whose keyword appears earliest in the text wins, ties in the order A, B, F, E, C, D, no match G; `r1` used the fixed order only). The correction text itself is never part of the event. `after_skill` is the latest skill token used in the same session within 10 minutes before the correction (from the local `telemetry/skill-log.jsonl`, kept 14 days, never uploaded).
- `skill_use.skill` (and `after_skill`) are uploaded as the real skill name, private and plugin skills included. A name that would fail the token check (characters outside `[A-Za-z0-9._:+-]`, longer than 64, or a secret-like pattern) becomes `x-` + the first 10 hex of sha256(salt+name) so the event is not dropped. `KNOWN_SKILLS` only feeds the weekly list of unused framework skills. `via` is `tool` for the Skill tool and `slash` for a leading slash command; Claude Code built-in commands (`BUILTIN_SLASH`) produce no event.
- `ask_user.questions` is the question count (1-4) of one AskUserQuestion call; no question text is recorded.
