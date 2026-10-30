# Git Policy

This policy separates reusable template source from runtime or target-project artifacts.

## Commit To This Component Repository

These files are source for `ai-coding-java` and should be versioned:

```text
README.md
AGENTS.md
CLAUDE.md
TOOL.md
USAGE.md
.gitignore
.claudeignore
docs/
rules/
workflow/
templates/
examples/
scripts/
artifacts/
hooks/
.omx/project-memory.json
```

Notes:

1. `.omx/project-memory.json` may be committed in this component only because it contains stable, non-sensitive component facts.
2. `examples/` should contain sanitized fixtures only. No real customer data, secrets, credentials, internal hostnames, or production logs.
3. `scripts/` must stay dependency-free unless a dependency is explicitly justified.

## Ignore In This Component Repository

These files are local runtime state or temporary development artifacts and should not be committed:

```text
.omx/logs/
.omx/state/
.omx/metrics.json
.omx/notepad.md
.omx/knowledge-candidates/
.Codex/
*.log
__pycache__/
*.pyc
.worktrees/
node_modules/
target/
build/
dist/
.DS_Store
```

Notes:

1. `.omx/notepad.md` is current-task working memory, not stable source.
2. `.Codex/memory/MEMORY.md` is useful locally, but should not be required for the shared template package.
3. Runtime logs and hook state are evidence during a run, not source artifacts.

## Target Java Project Policy

When `scripts/init_target_project.py` injects `.ai-coding-java/` into a business project, the target project must choose one of two policies:

```text
local-auxiliary:
  .ai-coding-java/ is local AI development assistance and should be ignored by the business repo.

committed:
  .ai-coding-java/ is reviewed, sanitized, and committed as a project-specific rule package.
```

For target business projects, never commit:

```text
.ai-coding-java/project-profile.md if it contains environment-sensitive details
.ai-coding-java/AGENTS.ai-coding-java-snippet.md after it has been merged into AGENTS.md
.ai-coding-java/CLAUDE.ai-coding-java-snippet.md after it has been merged into CLAUDE.md
.ai-coding-java/artifacts/ when it contains local drafts, raw evidence, private business details, or unsanitized output
.git/hooks/pre-commit, .git/hooks/pre-push, and backup hook files, because Git hook files are local Git metadata, not repository source
runtime logs
temporary reports
real data exports
curl outputs with personal or business-sensitive data
```

## Personal Development Branches

Daily personal development should happen on a named work branch:

```text
feature/<name>
bugfix/<name>
hotfix/<name>
refactor/<name>
chore/<name>
test/<name>
release/<version>
```

### Task branch naming contract

For ordinary worktree-backed tasks, `<name>` is the task name only. Do not append
the assignee name, date, agent name, or a generated task number unless the project
explicitly defines that as part of the task name. For example:

```text
feature/task25-data-extraction
```

is valid, while `task-25-data-extraction-design` and
`feature/kaixiang-20260813-task25-data-extraction` are not valid ordinary task
branch names under this contract.

The task orchestrator must validate the branch prefix and base branch before
dispatching implementation work. A project may override the base branch, but the
override must be explicit in the project contract or user request; never infer it
from a personal global configuration.

Multica agents and other external orchestrators should treat this policy as the
authoritative branch/worktree contract. They must not invent additional naming
rules or infer branch ownership from chat context, runtime memory, or global
runtime config.

### Worktree lifecycle contract

For a worktree-backed task, record the project, repository, task, branch, base
branch, absolute or repository-relative path, owner, status, and creation time in
the target project's registry. The path and branch must be unique among active
worktrees for that repository.

Before creation, verify the repository is clean enough for the requested operation,
the branch name passes `git check-ref-format --branch`, and the declared base branch
exists locally or is explicitly fetched by the project workflow. Do not implement
on an integration or release branch unless the project contract explicitly allows it.

Use `git worktree remove` for normal cleanup and `git worktree prune` only to remove
stale administrative records after checking for recoverable changes. Use
`git worktree lock --reason "..."` for a worktree on a removable or intermittently
mounted path. Never delete `.git/worktrees` administrative entries by hand.

The project contract remains responsible for the merge and publication chain. A
workspace or agent must not infer it from another project, global runtime settings,
or the current user's last task.

`main`, `master`, `develop`, and `dev` are treated as integration branches. The default `pre-push` hook warns when pushing directly from those branches; `Hook mode: strict` blocks the push.

### Branch Ownership

AI works on task branches only, and merges into / pushes only the baseline branch the
project declares. `main`/`master` is never merged or pushed by AI; the user merges it on
the hosting web UI (exception: projects whose declared baseline itself is `main` — there
AI may push the baseline after asking the user to confirm). `rules/git-workflow.md` restates
this as its 分支归属语义 bullet; this section is the authority.

## Remote Hosting

The component repository should remain compatible with GitHub and Gitee.

Recommended remote names:

```text
origin  GitHub
gitee   Gitee
```

Keep `main` as the default branch on both hosts. Do not add host-specific workflow requirements to the core template unless they have a portable fallback.

## Releasing

1. On `main`, set `VERSION` to the new `X.Y.Z` and commit.
2. Create an annotated tag on that commit: `git tag -a vX.Y.Z -m "vX.Y.Z"` (the tag name minus `v` must equal `VERSION` in that commit).
3. Push `main` and the tag to both GitHub and Gitee; the mirror does not sync automatically.

Target project placement rule:

```text
Generated AI Coding support files go under .ai-coding-java/ by default.
The only expected target-root edits are bounded ai-coding-java marker blocks in AGENTS.md and CLAUDE.md.
Do not place helper scripts, reports, or knowledge drafts in the business root.
```

## Development Product Records

Development records are useful, but their destination differs:

| Record | Destination |
|---|---|
| Stable component decision | `.omx/project-memory.json` or docs |
| Current task progress | `.omx/notepad.md`, ignored |
| Unsanitized knowledge candidate | `.omx/knowledge-candidates/`, ignored |
| Reusable rule/template/example | docs, rules, workflow, templates, examples; committed |
| Reusable artifact directory guidance | artifacts/README.md; committed |
| Reusable hook source | hooks/ and scripts/install_git_hooks.py; committed |
| Verification command result | final report or sanitized docs if reusable |
| One-off command output/log | ignored runtime logs |
| Target project generated `.ai-coding-java/` | target policy decides |
