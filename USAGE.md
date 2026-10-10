# ai-coding-java Usage

全局安装入口见 [README](README.md)，安装结构与行为以 [global-install-design](docs/global-install-design.md) 为准。本文保留项目档案层用法；全局 `core/`、`packs/`、`adapters/` 由 `installer/aicj.py` 安装。

## Quick Start

安装后先跑 `python3 installer/aicj.py doctor`；健康安装会在 `summary` 行之后打印「下一步」三行，照做即可。

| 场景 | 组合拳 |
|---|---|
| 只有粗糙想法 | `flow` → `req-intake` → `add-module` → `local-verify` → `code-review` → `git-commit` |
| 需求文档完整 | `prd` 或现有文档 → `auto-sdd` → `code-review` → `git-commit` |
| 小改 | `flow` → 在 worktree 里直接改 → `local-verify` → `git-commit` |
| 大需求 / 会议纪要 | `prd` → `req-analysis` → 逐 Story 走前两种 |
| 补测试 | `qa-testcase-pro` → `qa-baseline` |

三条口诀：

- 不知道下一步就 `flow`。
- 需求不成熟别硬写 PRD，先 `req-intake`；说不清就让 `grilling` 追问。
- 改文件前先建 worktree，收口走 `git-commit`。

质量门按任务规模分档：小改只过门2 自审，轻改过门1 轻量 + 门2 + 门3，中改/完整流程门1–门4 全过（判据见 `sdd-pipeline`）。`flow` 输出的「门状态」取 通过 / 未过 / 未记录，`未记录` 表示缺证据，不等同于通过。

`grilling` 为可选增强：未安装时 `req-intake` / `flow` 降级为直接问答。

## Initialize A Target Java Project

1. Run or follow `$setup-ai-coding` in the target repository; Java/Python/Vue targets get `.ai-coding-java/` through this flow and write the Claude marker to `CLAUDE.local.md`.
2. If the machine lacks `$setup-ai-coding`, explicitly link its project-init source using `scripts/install_setup_ai_coding_skill.py` from the `ai-coding-java` component repository.
3. Confirm project type:
   - new project
   - legacy project
   - maintenance project
4. Confirm technology stack from suggestions or custom input.
5. Confirm build, test, start, database, cache, and external-system dependencies.
6. Confirm data isolation boundary, such as tenant, organization, school, year, or region.
7. Confirm default verification level: lightweight, standard, or strict.
8. Decide template placement:
   - local auxiliary `.ai-coding-java/` not committed
   - committed project rule package after review

## Scripted Injection

```bash
python3 /path/to/ai-coding-java/scripts/init_target_project.py /path/to/target-project \
  --project-type legacy \
  --stack "Java 8 + Spring Boot 2.x + Maven + MyBatis + MySQL + Redis" \
  --verification-level standard \
  --template-policy local-auxiliary \
  --data-boundary "school + school_year"
```

By default, the script writes `.ai-coding-java/` and adds small marker blocks to target root `AGENTS.md` and `CLAUDE.md`, so Codex and Claude Code can discover the rules.

`$setup-ai-coding` calls this injection path for Java/Python/Vue projects; Java rules remain conditional on Java tasks. Use the scripted command directly when you want explicit control over parameters or refresh behavior.

When direct injection should avoid changing team `CLAUDE.md`, add:

```bash
--claude-entry local
```

Optional `--hooks {install,skip}` (default `install`) controls whether hooks are installed during this
call, and `--build-cmd` / `--test-cmd` / `--start-cmd` fill the corresponding `project-profile.md` fields.

If the target is a Git repository and `--hooks install` is used, the script also installs local
`pre-commit` and `pre-push` hooks through:

```bash
python3 .ai-coding-java/scripts/install_git_hooks.py .
```

`pre-commit` scans staged files and blocks deterministic P0 findings. `pre-push` checks personal development branch naming and build/test command settings. When `core.hooksPath` is already set (e.g. husky), the installer prints `SKIP` and does not write anything unless `--allow-hooks-path` is passed.

It also writes snippets for review:

```text
.ai-coding-java/AGENTS.ai-coding-java-snippet.md
.ai-coding-java/CLAUDE.ai-coding-java-snippet.md
```

## Static Review

After injection, run from the target project:

```bash
python3 .ai-coding-java/scripts/static_review_check.py .
```

Use this before AI semantic review when you need an explicit scan outside commit flow. The same scanner is also used by the auto-installed `pre-commit` hook for staged-file checks.

## TAP Fullstack Collaboration

For frontend/backend multi-repository tasks, initialize an optional TAP channel from the component repository:

```bash
python3 /path/to/ai-coding-java/scripts/init_tap_fullstack.py /path/to/system-root \
  --channel-name fullstack \
  --repo server=/path/to/system-root/code/server:cc_server_agent \
  --repo web=/path/to/system-root/code/web:cc_web_agent
```

This creates shared `.tap/<channel>-comms`, per-repository `tap-config.json`, Claude `.mcp.json`, Claude `sync-tap-pending` slash-command shells, and Codex `.tap-comms/bin/codex-tap` wrappers without editing global Codex config. See `docs/tap-fullstack-collaboration.md`.

## GitHub And Gitee

The component works with ordinary Git remotes and does not depend on host-specific features.

Recommended remote names:

```text
origin  GitHub
gitee   Gitee
```

Use [docs/remote-hosting-guide.md](docs/remote-hosting-guide.md) for setup and sync verification.

## Target Project File Placement

Generated AI Coding support files should stay under `.ai-coding-java/` by default.

The project-profile injection changes target-root entry marker blocks only: `$setup-ai-coding` uses `AGENTS.md` and `CLAUDE.local.md`; direct injection defaults to `CLAUDE.md`. Workspace initialization may additionally create the local support files described in the skill.

When a task needs RD process records, put them under:

```text
.ai-coding-java/artifacts/<work-id>/
```

Use `docs/rd-integrated-workflow.md` and the brief templates only for complex requirements, risky refactors, release-sensitive changes, or work that needs traceability. Do not turn artifacts into a mandatory hook or gate by default.

For new requirements, complete modules, legacy behavior changes, or unclear behavior changes, invoke global `$grilling` 若已安装; otherwise confirm requirements, scope and acceptance directly before design-gated implementation.

## Minimal Target `AGENTS.md` Pointer

```markdown
## ai-coding-java

Use `.ai-coding-java/docs/rule-index.md` as the first rule routing file.

Load only matching rules:
- Controller / VO: delivery + review + verification matrix
- Service write flow: transaction + delivery + verification matrix
- Mapper XML / SQL: sql + delivery + verification matrix
- Security or logging: security-logging + review

Project business rules, data isolation, environment commands, and API contracts in this `AGENTS.md` override generic ai-coding-java suggestions.

For new requirements, complete modules, legacy behavior changes, or unclear behavior changes, invoke global `$grilling` 若已安装; otherwise confirm requirements, scope and acceptance directly before design-gated implementation.
```

## Minimal Target `CLAUDE.md` Pointer

```markdown
## ai-coding-java

Use `.ai-coding-java/docs/rule-index.md` as the first ai-coding-java routing file for Java development rules.

Read the nearest `AGENTS.md` for Codex-compatible project execution rules when present.
Use `.ai-coding-java/docs/verification-matrix.md` before claiming completion.
For new requirements, complete modules, legacy behavior changes, or unclear behavior changes, invoke global `$grilling` 若已安装; otherwise confirm requirements, scope and acceptance directly before design-gated implementation.
```

## Agent Loading Order

```text
1. nearest AGENTS.md
2. user task
3. project memory or current task notes
4. .ai-coding-java/docs/rule-index.md
5. matched rule/workflow/template files only
6. matched reusable knowledge entries only when relevant
7. matched artifact guidance only when RD process records are useful
8. relevant code, tests, config, and history
```

## Expected Delivery Report

Use `templates/delivery-report-template.md`.

Every report must include:

1. Changed files.
2. Impacted modules, APIs, or tables.
3. Verification evidence.
4. `Not-tested` items.
5. P0/P1/P2 review result.
