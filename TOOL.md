# EvoHarness (ai-coding-java) Tooling Contract

全局安装入口见 [README](README.md)，安装结构与行为以 [global-install-design](docs/global-install-design.md) 为准。本文保留项目档案层用法；全局 `core/`、`packs/`、`adapters/` 由 `installer/aicj.py` 安装。

`ai-coding-java` is a reusable global AI Coding component with Java/Python/Vue packs and a separate project-profile layer.

## What It Provides

1. Project onboarding questions for Java technology stack, commands, data boundaries, and verification level.
2. P0/P1/P2 Java delivery rules.
3. Scoped workflow routing so agents load only relevant rules.
4. Verification matrix and `Not-tested` reporting format.
5. Review and delivery report templates.
6. Lightweight auto-installed Git hooks for deterministic P0 checks and pre-push validation reminders.
7. Codex and Claude Code compatible project entry guidance.
8. GitHub and Gitee remote-hosting guidance.
9. A versioned `setup-ai-coding` project-init skill source with a separate link helper.
10. Global core, language packs and optional adapters installed through `installer/aicj.py`.

## Default Scope

1. Target-project initialization.
2. AI-readable Java rules and templates.
3. Lightweight Git `pre-commit` and `pre-push` protection.
4. Manual or AI-driven verification through the verification matrix.
5. Project `AGENTS.md` / Claude entry marker blocks for project-profile discovery.
6. Source-host neutral Git usage.

## Recommended Runtime Use

Scenario-to-skill combinations are in [USAGE.md Quick Start](USAGE.md#quick-start).

For this component project:

```bash
python3 -m pytest tests -q -p no:cacheprovider
python3 scripts/sanitize_check.py
python3 scripts/context_budget_check.py
python3 scripts/template_integrity_check.py
python3 scripts/static_review_check.py examples/static-review-good
```

For a target Java project:

```text
1. Run or follow $setup-ai-coding in the target project; it initializes the workspace and injects .ai-coding-java for Java/Python/Vue targets, using CLAUDE.local.md for the Claude marker.
2. If $setup-ai-coding is missing, explicitly link its project-init source using scripts/install_setup_ai_coding_skill.py from the ai-coding-java component repository.
3. Confirm project stack and verification level.
4. Add a short pointer from the target AGENTS.md to the injected ai-coding-java rules.
5. Load docs/rule-index.md first, then only the matching rule or knowledge files.
```

Direct use of `scripts/init_target_project.py` remains available for explicit injection or refresh workflows. Use `--claude-entry local` when the injection is personal/local, and `--hooks skip` when hook installation should wait for explicit user consent (`$setup-ai-coding` always passes `--hooks skip`).

For target project static review:

```bash
python3 .ai-coding-java/scripts/static_review_check.py .
```

Target-project initialization installs hooks when `--hooks install` (the script default) is used:

```bash
python3 .ai-coding-java/scripts/install_git_hooks.py .
```

The installed hooks scan staged files before commit and check personal branch / verification settings before push. When `core.hooksPath` is already set, the installer skips and prints `SKIP` instead of writing tracked files.

Target-project generated support files should live under `.ai-coding-java/` by default. Root changes are limited to bounded entry marker blocks: `$setup-ai-coding` uses `AGENTS.md` and `CLAUDE.local.md`; direct injection defaults to `CLAUDE.md`.

Recognition note: Codex uses root `AGENTS.md`; Claude Code uses root `CLAUDE.md`. `scripts/init_target_project.py` writes bounded marker blocks that point both runtimes to `.ai-coding-java/docs/rule-index.md`.
