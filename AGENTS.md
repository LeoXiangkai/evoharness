# EvoHarness (ai-coding-java) Project Contract

Codex reads this entry; Claude Code reads `CLAUDE.md`.

## Project

`ai-coding-java` is a reusable global AI Coding install component plus a project-profile tool for Java, Python and Vue. Global components install into the selected home; project initialization writes `.ai-coding-java/` and bounded entry markers. This is a Python tooling/documentation repository, not a Java application.

## Source Of Truth

- Human index: `README.md`; installation authority: `docs/global-install-design.md`.
- Global sources: `core/` (language-neutral), `packs/` (language rules/verification), `adapters/` (optional integrations), `installer/` (CLI and install lifecycle).
- Project-profile sources: root `rules/`, `workflow/`, `templates/`, `docs/`; root rules retain Java 8 / Spring Boot 2 guidance. Enter via `docs/rule-index.md`, then load only matched files.
- Project initialization: `skills/setup-ai-coding/SKILL.md` and `scripts/init_target_project.py`.
- Skill ownership and runtime boundary: `docs/runtime-skill-boundary.md`.
- Shared stable context: `.omx/project-memory.json`; current notes: `.omx/notepad.md`. Runtime state/logs are not versioned source.

## Development Rules

1. Keep changes scoped, reversible and dependency-free unless executable tooling requires a dependency. Use the machine global Python; do not create local environments.
2. Keep Codex and Claude entry docs compact and aligned. Preserve user content; markers and settings hooks are bounded merges.
3. Test installations only with `--home` pointing to a temporary directory; never write real user configuration during component development.
4. Read CLI `--help` for flags. Blocking global hooks default to warn; `--strict` or `AICJ_HOOK_MODE=block` enables blocking.
5. Codex gets skills links only with `--codex`; do not modify its global AGENTS.md. Codex hooks merge only with `--codex-hooks`.
6. Keep root project-profile rules and `scripts/static_review_check.py` (used by repository pre-commit). They remain separate from global language packs.
7. Third-party skills are optional: 若已安装 use them; otherwise perform the corresponding requirement, research, prototype, domain or document workflow directly and report the gap.
8. Do not write secrets, personal paths, private service configuration or runtime logs into reusable source. Follow `docs/git-policy.md` for branch and versioning constraints.
9. Implement the requested capability fully; assertions must verify observable behavior. Report verification gaps explicitly.

## Verification

```bash
python3 -m pytest tests -q -p no:cacheprovider
python3 scripts/sanitize_check.py
python3 scripts/template_integrity_check.py
python3 scripts/context_budget_check.py
```

Installer changes also require a temporary-home install → doctor → uninstall check. Project initialization changes require temporary-target integration. Report exit codes, test counters including skips, remaining files and `Not-tested` items. A missing `.omx/notepad.md` budget failure is reported, not hidden.
