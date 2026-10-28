# Runtime Skill Boundary

ai-coding-java 提供全局组件与项目档案两层。安装结构以[全局安装设计](global-install-design.md)为准，人类入口见[README](../README.md)；技能发现、触发和执行由 Claude Code、Codex 或 OMX 运行时负责。

## 技能按来源分类

| 来源 | 内容 | 使用边界 |
|---|---|---|
| `core/skills/` | 需求、PRD、设计/SDD、模块开发、测试基线、用例、代码审查、验证、Git 交付等语言无关 skills | 随 core 安装；按任务加载 |
| `packs/java/skills/` | java-verify | Java 探测、静态检查、构建/接口验证 |
| `packs/python/skills/` | python-verify | Python 项目命令、静态检查、测试与 Web 启动验证 |
| `packs/vue/skills/` | vue-verify、playwright-ui-auto | Vue 项目验证与 UI 自动化 |
| `adapters/executor/skills/` | executor-handoff-ops | 选择 executor 后提供外部执行体交接流程 |
| `adapters/lesson/`、`adapters/optional-plugins/`、`adapters/team-admin/` | lesson 机制，jev、verify-probe 工具壳与引用，团队管理员周复盘 skill | 可选适配，不代表第三方 skill 已安装；依赖不足时 SKIPPED |
| `skills/setup-ai-coding/` | 项目工作区初始化 | 项目档案层 skill 源；不代为执行全局安装 |
| 第三方（本仓库不提供） | grilling、research、prototype、domain-modeling、writing-for-agents | **若已安装**才调用；doctor 只读探测，不自动下载 |

第三方缺失时，直接完成相应流程：需求问答确认边界与验收，调查并记录来源，用临时原型检验设计，整理术语/ADR，按紧凑指针编写文档。输出中记录缺失能力，不把未经调用的 skill 写成执行证据。

## 全局安装位置

- Claude：skills 装到 `~/.claude/skills`，规则、refs、角色、bin 分别在 `~/.claude` 的相应目录，hooks 在 `~/.claude/hooks/aicj`。
- `~/.claude/CLAUDE.md` 仅合并 ai-coding-java marker block，引用 `~/.claude/aicj/CLAUDE.global.md`；settings.json 仅合并 hooks。
- Codex：`--codex` 额外在 `~/.agents/skills` 建链接，指向 Claude 安装的 skills。安装器不改 `~/.codex/AGENTS.md`；只有 `--codex-hooks` 才合并 `~/.codex/hooks.json`。
- 全局拦截类 hooks 默认 warn；`--strict` 或 `AICJ_HOOK_MODE=block` 切换为 block。可选 hooks 需 `--enable-hook` 点名注册。

第三方 doctor 探测位置是 `~/.agents/skills`；WARN 只说明该位置未找到，不证明其他运行时也未安装。

## 项目档案边界

`$setup-ai-coding` 先只读运行 aicj status。全局缺失时建议用户安装，继续处理可执行的项目初始化，不静默改用户 home。

Java/Python/Vue 按与安装器一致的项目文件信号识别。目标 `.ai-coding-java/` 复用根 `rules/`、`workflow/`、`templates/`、`docs/`，并非把全局 core/packs/adapters 复制进项目。根 Java 8 / Spring Boot 2 规则只在匹配 Java 场景时读取；Python/Vue 验证由对应全局语言包负责。

项目入口 marker 指向 `.ai-coding-java/docs/rule-index.md`；Claude 默认使用 `CLAUDE.local.md`。项目业务规则、数据隔离、接口契约和环境命令以最近的项目契约及 project-profile 为准。根 `scripts/static_review_check.py` 和项目 Git hooks 保留，不能将其 P0 检查行为与全局 warn hooks 混为一谈。

## 分支与派发

全局分支规范来源是 `~/.claude/refs/git-policy.md`（组件源 `core/refs/git-policy.md`）；已注入项目使用 `.ai-coding-java/docs/git-policy.md`。初始化脚本复制整个 docs 目录，因此项目档案包含该文件。显式项目基准分支与用户指令优先。

Multica 不自动继承用户 home 的规则或记忆。派发前读取项目契约和可访问的分支规范，校验 branch、base_branch、worktree；使用 `templates/multica-worktree-agent-template.md` 记录这些输入。远端执行体无法访问全局目录时，应由派发方提供对应规范，而不是假定它已加载。

## 工作流路由

```text
读取项目契约 → 项目规则索引 → 命中的全局 skill / 项目规则
→ 需求边界确认 → 设计门 → 实现 → 实际验证 → 交付证据
```

新需求需要 grilling 时，若已安装则调用，否则直接完成需求问答并保留结论。项目设计门、TDD 分级和验证矩阵仍由项目档案提供；具体运行时编排由运行时负责。明确小修或纯分析按项目规则选择必要验证，不强制加载完整流程。
