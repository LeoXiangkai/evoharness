---
name: setup-ai-coding
description: "为当前项目初始化可复用的 AI Coding 工作区（Codex / Claude Code / OMX 共用），并在 Java/Python/Vue 项目中接入 ai-coding-java 规则组件。只做项目级，不动全局环境层。"
user-invocable: true
---

# /setup-ai-coding — 项目级 AI Coding 初始化

目标：把当前项目初始化成 **Codex / Claude Code / OMX 三方都能识别**的可复用工作区，
且在 Java/Python/Vue 项目中接入 `.ai-coding-java/` 规则组件，同时**不把重型命令体系或全局运行时目录复制进仓库**。

它是统一入口，不把 CC 与 Codex 初始化拆成两套互相漂移的流程。

> 可版本化源文件位于 `skills/setup-ai-coding/SKILL.md`（相对于组件仓库）。
> 全局入口可由 `scripts/install_setup_ai_coding_skill.py` 链接到该源；
> 本 skill 的项目档案初始化与 `installer/aicj.py` 的全局组件安装是两个独立层。

---

## 作用域边界（先划清，否则会越权）

环境分两层，本 skill **只做项目层**：

| 层 | 内容 | 谁配置 | 本 skill 怎么处理 |
|----|------|--------|------------------|
| **全局层** | `~/.claude/settings.json`（hooks / `enabledPlugins` / 权限 / env）、`~/.claude/rules`、`~/.codex/config.toml`、`~/.agents/skills`、已装插件与 MCP | 用户一次性配好，跨项目共享 | **只读探测、不改**；缺关键能力时提示用户，不代为安装 |
| **项目层** | `AGENTS.md`、`CLAUDE.md` / `CLAUDE.local.md`、`.omx/`、ignore 与权限、Java/Python/Vue 项目的 `.ai-coding-java/`、项目特化 skills/agents | 每项目独立 | **本 skill 的职责范围** |

---

## 全局层前置检查

开始项目初始化前，先只读运行 `aicj status`；若没有命令入口，在组件源仓库用
`python3 installer/aicj.py status`。这一步只报告安装状态，不写全局目录。

全局层缺失时，只建议用户从组件源执行 `python3 installer/aicj.py install --codex`，
并说明应先 dry-run 和临时 `--home` 试用。**绝不自动执行安装**；全局层缺失不阻止
可独立完成的项目工作区初始化，涉及全局能力的步骤标明 `Not-tested`。

## 总原则

- 先读取项目事实，再生成配置；不把其他项目的业务内容复制进来。
- 默认低风险、可逆；**合并或补充**已有文件，不覆盖。覆盖 / 删除 / 写敏感路径前必须问用户。
- **启动上下文保持精简**：只写项目差异，不塞全局 skill 目录、model 表、关键词矩阵、长编排协议。
- 稳定事实进 `.omx/project-memory.json`，当前任务进 `.omx/notepad.md`。
- 不写密码、token、密钥、完整内部凭据、长的一次性日志。
- Codex 与 Claude Code 的入口必须互相一致，不能各说各话。

### 🔒 铁律：Claude Code 入口一律写 `CLAUDE.local.md`，默认不碰 `CLAUDE.md`

**本 skill 产出的所有 Claude Code 侧内容，默认目标文件永远是 `CLAUDE.local.md`**，与仓库归属无关、
与 `CLAUDE.md` 存在与否无关。

理由：

- `CLAUDE.local.md` 与项目已有的 `CLAUDE.md` **天然不冲突**——两者会被同时加载，各写各的。
- 它总是**新文件**，可以直接进排除规则；`CLAUDE.md` 一旦已被跟踪，`.gitignore` 对它无效，
  想脱离版本控制只能 `git rm --cached`，而那在共享仓库里是破坏性操作。
- 已验证 Claude Code 会自动加载它（哨兵实测，验证方法见第五阶段第 9 条）。

**例外一**：用户**显式要求**产出一份入库、供团队共用的 `CLAUDE.md`。此时才创建或修改它，
且必须先确认仓库归属（第零阶段）并征得同意。

**例外二（单人模式）**：项目 README「交付约定」的「开发模式」行写明「单人」时，入口改为本机 `CLAUDE.md`，
已跟踪的旧 `CLAUDE.md` 从 git 移除而非并存，做法见第零阶段「单人模式分支」。

除这两种例外之外（即多人模式）：

- ❌ 不创建 `CLAUDE.md`
- ❌ 不修改已存在的 `CLAUDE.md`（哪怕只加一行 `@AGENTS.md`）
- ❌ 不把 `CLAUDE.md` 写进 `.gitignore`、不 `git rm --cached`

---

## 第零阶段：判仓库归属（决定排除机制与红线强度）

> 入口文件**不再由这一步决定**——按上面的铁律，多人模式恒为 `CLAUDE.local.md`，单人模式为本机 `CLAUDE.md`。
> 这一步现在只回答两件事：**排除规则写哪里**、**红线要多严**。

**这一步不能跳。** 写任何文件前先判：

```bash
git rev-parse --show-toplevel              # 项目根
git shortlog -sne --all | head             # 贡献者
git remote -v                              # 是否公司/团队远程
git ls-files --error-unmatch CLAUDE.md     # CLAUDE.md 是否已被跟踪
git log --format='%an' -- CLAUDE.md | sort -u   # 谁写的
```

分三种情况。**入口文件三种情况都是 `CLAUDE.local.md`**，区别只在排除机制：

| 情况 | 判据 | 排除机制 | 红线 |
|------|------|---------|------|
| **A. 个人仓** | 单一贡献者 = 自己，或无远程 | `.gitignore` 追加即可（也可用 `.git/info/exclude`） | 常规 |
| **B. 团队共享仓，`CLAUDE.md` 未跟踪** | 多贡献者，但无 tracked `CLAUDE.md` | **`.git/info/exclude`**，不碰 `.gitignore` | 常规 |
| **C. 团队共享仓，`CLAUDE.md` 已被他人跟踪** | 多贡献者 + `CLAUDE.md` tracked 且作者不是自己 | **`.git/info/exclude`**，不碰 `.gitignore` | **最严**，见下 |

### 通用红线（三种情况都适用；仅多人模式，单人模式见下一节）

- ❌ **绝不** `git rm --cached CLAUDE.md` —— 共享仓库里那是把同事的文件删掉，他们 pull 后会消失。
- ❌ **绝不**把个人内容写进已存在的 `CLAUDE.md`，包括看似无害的 `@AGENTS.md` 一行引用。
- ✅ 已有的 `CLAUDE.md` **一字不动**，个人内容全部走 `CLAUDE.local.md`。

### 情况 C 追加约束

- 排除规则必须写 **`.git/info/exclude`**（本身不受版本控制、永不推送），做到**零 tracked 文件改动**：
  `git status --short` 与 `git diff --stat HEAD` 收工时都必须为空。
- 不新增任何会出现在 `git status` 里的未跟踪文件/目录（含 `scripts/` 这类新顶层目录）；
  确有需要则放进已排除的 `.omx/` 下。

### 单人模式分支（开发模式 = 单人）

**判据**：项目 README「交付约定」的「开发模式」行写明「单人」；未声明按多人处理，不得自行推断为单人。

此时 A / B / C 不再决定入口文件：入口恒为**本机 `CLAUDE.md`**，不入库、不另建 `CLAUDE.local.md`，
仓库里不保留任何 AI 工具入口文件。上面的通用红线（不 `git rm --cached`、不改已有 `CLAUDE.md`）在此模式下不适用。

1. **已跟踪的 `CLAUDE.md`**（含他人遗留）：按全局 worktree 规则在临时 worktree 内 `git rm CLAUDE.md` 并提交
   （Conventional Commits，不带任务编号），`--no-ff` 合入基线并推送基线；单人模式推基线无需再问。
   删除前记下该文件的最后提交 hash 与作者，写进输出。
2. **新内容不迁移旧文本**：旧文件常含他人开发机的路径 / host 与过期描述。项目事实按第一阶段重新探测，
   只写实跑验证过的命令；确需保留旧说明时，在新文件头写一行 `git show <旧提交>:CLAUDE.md` 指针即可。
3. **先排除、再写文件**：`.git/info/exclude` 追加 `CLAUDE.md`，然后写本机 `CLAUDE.md`；顺序反了文件会短暂出现在 `git status`。
4. 其余产物（`AGENTS.md`、`.omx/`、`.ai-coding-java` 等）照常按排除清单处理；它们若也已被他人跟踪，
   同样先从 git 移除再排除，并逐个在输出里列出文件名、原作者、最后提交。
5. **验证**：`git ls-files CLAUDE.md` 为空；`git check-ignore CLAUDE.md` 命中；`git status --short` 为空；
   `git rev-list --left-right --count origin/<基线>...HEAD` 为 `0 0`。

---

## 第一阶段：分析当前项目

1. 确认项目根：优先 git 根目录，否则当前工作目录。
2. 扫顶层结构：`ls` / `rg --files`，不做无目标全量阅读。
3. 识别技术栈：`pom.xml` / `build.gradle*` / `pyproject.toml` / `requirements*.txt` / `setup.py` / 含 Vue 依赖的 `package.json` / README。
4. 识别现有规范：`AGENTS.md`、`CLAUDE.md`、`CLAUDE.local.md`、README、构建脚本、lint/test 配置。
5. 抽样读 2-3 个入口/典型业务文件，提取分层、命名、错误处理、测试习惯。
6. **提取并实跑验证** build / test / 启动命令——只写验证过的，没验证的显式标 `Not-tested`。
7. 检查记忆状态：`.omx/`、CC 自动记忆（`~/.claude/projects/<encoded-cwd>/memory/`）是否已有内容。
8. **全局前置自检（只读）**：全局规则硬依赖以下工具，逐个 `test -x`，缺失只列出、不代装：
   `~/.claude/bin/git-safe`（git 写操作）；选用 executor 时探测 `~/.claude/bin/aicj-worker`。
   外部执行体按 `adapters/executor/refs/executor-contract.md` 配置，未选用的能力不视为硬依赖。
9. **本地自测入口探测**（Java / 后端项目）：
   - DDL / 数据脚本工具：`从项目已有脚本或已配置命令中探测数据库工具`，并看它读取哪些环境变量（如 `DBCLI_YML`）。
     **只记录位置与变量名，不生成脚本、不写任何凭据**；缺失标 `Not-tested: db-cli 未接入`。
   - 启动参数：从 `.vscode/launch.json`、`.idea/runConfigurations/`、`application-*.yml`（profile 名与
     `server.port`）提取；只把实跑验证过的启动命令写进产物，其余标 `Not-tested`。

> 常见坑：既有文档里的工具链路径可能是**别的操作系统遗留**（如 Windows `D:\...` 出现在 macOS 仓库）。
> 实跑一次 `mvn -v` / `node -v` 之类核实，别照抄。

---

## 第二阶段：生成或补齐配置

### 默认产物表

| 文件 | 用途 | 默认动作 |
|---|---|---|
| `AGENTS.md` | Codex 项目契约 | `omx agents-init` 生成，或无 OMX 时手写兜底 |
| **`CLAUDE.local.md`** | **Claude Code 入口（多人模式默认，恒用这个）** | 创建或合并，本地排除 |
| `CLAUDE.md` | 团队共享的项目契约 | 多人模式**默认不碰**，仅用户显式要求入库共享时才创建/修改；**单人模式下是本机入口**（不入库），见「单人模式分支」 |
| `.omx/project-memory.json` | 结构化长期记忆 | 缺失则按 schema 创建 |
| `.omx/notepad.md` | 当前任务记忆 | 缺失则创建 |
| `.claude/settings.local.json` | 敏感文件读取阻断 | 按需创建 |
| `.git/info/exclude` 或 `.gitignore` | 排除运行时/本地产物 | 按第零阶段的情况选 |
| `.worktrees/REGISTRY.md` | worktree 登记（全局 worktree 规则第一步必读） | 缺失则建空表 |

> ⚠️ **不再生成 `.claudeignore`**：原生 Claude Code **不读取**该文件，Codex 也不读。
> 官方排除机制是 `.gitignore` / `.git/info/exclude` + `permissions.deny` + `claudeMdExcludes`。
> 历史项目若有遗留 `.claudeignore`，提示用户它无效、迁移到上述机制。

### 1. AGENTS.md（先做——它是 Codex 侧主入口）

- **`omx` 可用**：跑 **`omx agents-init`**（轻量，只铺 AGENTS.md 脚手架；支持 `--dry-run` / `--verbose`）。
  它会为**目标目录 + 直接子目录**各生成一份，已有非 omx 管理的文件不加 `--force` 不会覆盖。
- ⛔ **不要跑 `omx setup --scope project`** 除非用户明确要求。它会：
  - **改写已跟踪的 `.gitignore`**（破坏"零 tracked 改动"）；
  - 把 `.codex/` 全套运行时目录（skills / prompts / native agents / hooks / HUD）复制进仓库——
    正是本 skill 开宗明义要避免的事。
  拿不准先 `--dry-run` 看它到底动什么。
- **生成后补齐项目事实**，写进末尾的 `<!-- OMX:AGENTS-INIT:MANUAL -->` 块（该块在 `agents-init`
  刷新时会被保留）：技术栈、模块职责、已验证的 build/test/启动命令、编码与验证约束。
- `<!-- OMX:RUNTIME -->` / `<!-- OMX:TEAM:WORKER -->` / `<!-- OMX:MODELS -->` 等 marker 是运行时
  注入锚点，**不要手工删改**。
- 旧项目若还在用 `/deep-interview` 老触发语法，对齐到 `$` 新语法。

### 2. Claude Code 入口 → `CLAUDE.local.md`（多人模式恒定；单人模式见「单人模式分支」）

**多人模式下，不管仓库归属是 A / B / C，也不管 `CLAUDE.md` 是否存在，都写 `CLAUDE.local.md`；单人模式写本机 `CLAUDE.md`（内容要求相同，冲突裁决顺序里去掉“项目 CLAUDE.md”一档）。**

已有 `CLAUDE.md` 时**不引用、不修改它**——两个文件会被同时加载，重复引用只会浪费上下文。
开头写清冲突裁决顺序，让两者关系明确：

```markdown
# <项目名> · 本机专属约定（Claude Code）

> 本文件仅存在于本机，由 <排除机制> 排除，永不入库。
> 项目通用规范见同目录 CLAUDE.md（若存在，CC 会一并自动加载），本文件不复制、只补本机特有的。

## 冲突裁决顺序
当前任务指令 > 项目 CLAUDE.md > 本文件 > 全局 ~/.claude/CLAUDE.md > ~/.claude/rules/*
```

项目**没有** `CLAUDE.md` 时同样写 `CLAUDE.local.md`，只是把"见同目录 CLAUDE.md"那句去掉——
不要因为"位置空着"就顺手建一个 `CLAUDE.md`。真需要团队共享版本时，由用户显式提出。

本文件该写什么：冲突裁决顺序、**当前基线分支**（见下）、已验证的工具链与命令、DDL 工具位置
（或 `Not-tested: db-cli 未接入`）、能力路由、本仓特有的坑、记忆分层。
不写的：项目通用规范（属于 `CLAUDE.md` 或 `AGENTS.md`）、全局 skill 目录、model 表。

#### `@AGENTS.md` 全量导入的判断门槛（写在 `CLAUDE.local.md` 里，不写进 `CLAUDE.md`）

`omx agents-init` 生成的**根 AGENTS.md 常达 20KB+**，主体是 OMX 多智能体编排协议（model 表 /
team-worker 协议 / lore commit protocol）——对 CC 是纯噪音，全量导入会让每次会话白吃约 6k token。

**判据**：`wc -c AGENTS.md`

- **≤ 5KB 且主体是项目事实** → 可以写 `@AGENTS.md`
- **> 5KB 或主体是编排协议** → **不写 `@import`**，改指针式引用：说明 AGENTS.md 是 Codex 侧入口、
  项目事实只在 `OMX:AGENTS-INIT:MANUAL` 块，需要时按需 Read 那一段

#### 当前基线分支声明

全局 git 规则要求基线分支名由项目声明，而非写进规则或记忆。推断候选：
`git symbolic-ref --short refs/remotes/origin/HEAD`，无远程时取当前分支；**向用户确认一次**后，
在 `CLAUDE.local.md` 与 AGENTS.md 的 `OMX:AGENTS-INIT:MANUAL` 块各写一行：

```markdown
当前基线分支：<branch>
```

换基线时只改这一行。不要写 `git config branch.<task>.baseline`——那是创建任务分支时的运行时记录。

#### `.worktrees/REGISTRY.md` 骨架

缺失时创建（`.worktrees/` 已在排除清单内，不产生 tracked 改动），字段与全局 worktree 规则一致：

```markdown
# Worktree Registry

| 目录 | 分支 | 类型 | 任务 | 范围 | 状态 | 基线 | 运行中 | 更新时间 |
|------|------|------|------|------|------|------|--------|----------|
```

已存在时不改动，只核对与 `git worktree list` 是否一致，不一致写进输出。

### 3. `.omx/` 记忆层

`omx agents-init` **不创建** `.omx/`（只有 `omx setup` 会，但它有上面的副作用）。因此默认**手工建**
这两个文件即可：

`.omx/project-memory.json`（只写已验证、稳定、非敏感的事实）：

```json
{
  "techStack": {},
  "structure": {},
  "build": {},
  "conventions": {},
  "notes": [],
  "directives": []
}
```

`.omx/notepad.md`：

```markdown
# OMX Notepad

## Priority Context

## Working Memory

## Manual Notes
```

notepad 只放当前任务，不归档长流程日志。

### 4. 排除规则与权限

**情况 C（团队仓）——用 `.git/info/exclude`**，做到零 tracked 改动（**情况 B 同 C**，同样写
`.git/info/exclude`）：

```
AGENTS.md
CLAUDE.local.md
.ai-coding-java
.omx
.claude/
.worktrees/
```

> 注意 gitignore 语义：`AGENTS.md` 无前导斜杠时匹配**任意层级**，子目录那几份一并覆盖。
> `.ai-coding-java`、`.omx` **不带尾斜杠**：带斜杠只匹配目录，匹配不到 worktree 适配阶段建的同名软链。

**情况 A（个人仓）——先问用户 `AGENTS.md` / `.claude/` / `.ai-coding-java/` 是否入库**，默认只排除
`CLAUDE.local.md`、`.omx/`、`.worktrees/` 和运行时产物（追加到 `.gitignore`，整块加注释标来源，
已存在条目不重复）：

> 理由：个人仓通常希望这些文件入库；默认忽略 `.claude/` 会挡住项目级 skills/settings 入库。

```gitignore
# setup-ai-coding 生成的本地协作配置
CLAUDE.local.md
.omx
.worktrees/
```

用户明确表示不希望入库时，再按需追加 `AGENTS.md` / `.claude/` / `.ai-coding-java`（不带尾斜杠）条目。

> **多人模式的清单里没有 `CLAUDE.md`**：本 skill 默认不产出它，所以也没有理由去 ignore 它。
> 单人模式的清单多一行 `CLAUDE.md`，且必须先把已跟踪的 `CLAUDE.md` 从 git 移除再追加排除，否则排除无效。
> 若项目已有 tracked 的 `CLAUDE.md`，把它加进 ignore 也不会生效（gitignore 对已跟踪文件无效）。

运行时状态无论哪种情况都该排除：`.omx/logs/`、`.omx/state/`、`.omx/metrics.json`、`*.log`、
`node_modules/`、`target/`、`build/`、`dist/`。

**注意**：当前项目本身就是**可共享组件或团队标准仓库**时，不要自动忽略 `AGENTS.md` / `CLAUDE.md` /
README / docs / templates。

**敏感文件阻断读取**——写进 `.claude/settings.local.json`：

```json
{ "permissions": { "deny": [
  "Read(./.env)", "Read(./.env.*)", "Read(./**/secrets/**)",
  "Read(./**/*.pem)", "Read(./**/*.key)", "Read(./**/*.jks)", "Read(./**/*.p12)"
] } }
```

普通构建产物靠 gitignore 即可（CC 默认 `respectGitignore=true`）。

---

## 第三阶段：能力路由（不要重复造全局已有的）

全局插件与 skill 是共享资产；项目层只补"全局覆盖不到"的特化能力。

1. **盘点全局能力**：`enabledPlugins`、`~/.claude/skills`、`~/.claude/commands`、`~/.agents/skills`。
2. **路由优先级**：全局已覆盖的 → 在项目入口文件写"本项目用 `<能力>`"指过去，**不在项目
   `.claude/skills` 重造同名能力**。
3. **只在确有项目特化**（专属构建流程 / 专属业务校验 / 专属 e2e 入口）时才新建，并登记到能力路由表。
4. 不引用未安装的 skill / 插件 / MCP / agent。

---

## 第四阶段：Java/Python/Vue 项目接入 ai-coding-java

`$setup-ai-coding` 必须把 Java/Python/Vue 项目的 `.ai-coding-java/` 接入纳入同一轮初始化；不要让用户再手工猜
`init_target_project.py`。

### 触发条件

执行完工作区入口文件后，按以下规则处理：

1. **当前仓库就是 `ai-coding-java` 组件源**：跳过注入。判据是同时存在
   `skills/setup-ai-coding/SKILL.md` 和 `scripts/init_target_project.py`。
2. **Java/Python/Vue 项目**：自动接入，判据与 pack detect 一致：
   Java 为 `pom.xml` 或 `build.gradle*`；Python 为 `pyproject.toml`、`requirements*.txt` 或 `setup.py`；
   Vue 为 `package.json` 的 dependencies 或 devDependencies 含 `vue`。
   项目档案仍复用根素材，Java 规则仅在 Java 场景加载，Python/Vue 使用对应全局语言包验证。
3. **已存在 `.ai-coding-java/`**：不要重跑覆盖式初始化；优先执行
   `python3 .ai-coding-java/scripts/check_target_project.py .`，必要时再用组件源的
   `scripts/refresh_target_project.py . --source <ai-coding-java-root>` 做 dry-run。
4. **未命中 Java/Python/Vue 或无法确认**：跳过 `.ai-coding-java/`，在输出里写明 `Not-tested/Skipped` 原因。

### 组件源定位

优先解析当前 skill 的真实源路径：仅当其位于组件仓库的 `skills/setup-ai-coding/` 时，
上两级目录才是组件根；不要把全局安装目录误当组件源。否则检查环境变量：

```bash
test -n "$AI_CODING_JAVA_HOME" && test -f "$AI_CODING_JAVA_HOME/scripts/init_target_project.py"
```

> 请设置 `AI_CODING_JAVA_HOME` 指向组件源仓库；只接受实际含初始化脚本的目录。

找不到组件源时，不要创建临时替代目录；输出 `Not-tested: ai-coding-java source not found`。

### 初始化命令

对尚未接入的 Java/Python/Vue 项目执行：

```bash
python3 <ai-coding-java-root>/scripts/init_target_project.py <project-root> \
  --project-type <new|legacy|maintenance|unconfirmed> \
  --stack "<detected-or-unconfirmed-stack>" \
  --verification-level standard \
  --template-policy local-auxiliary \
  --data-boundary "unconfirmed" \
  --claude-entry local \
  --hooks skip \
  --build-cmd "<第一阶段已实跑验证的 build 命令，未验证留空>" \
  --test-cmd "<第一阶段已实跑验证的 test 命令，未验证留空>" \
  --start-cmd "<第一阶段已实跑验证的 start 命令，未验证留空>"
```

参数选择：

1. `project-type`：已有业务源码或提交历史用 `legacy`；空项目用 `new`；无法判断用 `unconfirmed`。
2. `stack`：从已检测的构建文件、依赖文件、README、配置文件中提取；无法确认写 `unconfirmed`。
3. `data-boundary`：除非项目规则已明确租户/组织/学校/年度等边界，否则写 `unconfirmed`。
4. 多人模式固定 `--claude-entry local`，避免默认修改团队 `CLAUDE.md`；单人模式用 `--claude-entry team`——此时 `CLAUDE.md` 已移出 git 并被排除，标记块写进它不会入库。
5. 不加 `--force`，除非用户明确要求覆盖 `.ai-coding-java/`。
6. `--hooks skip` 是 `$setup-ai-coding` 的固定选择：钩子安装改为下面"git 钩子"小节里的显式征求同意步骤，不在初始化命令里静默装上。
7. `--build-cmd` / `--test-cmd` / `--start-cmd` 只填第一阶段**已实跑验证**的命令；没有验证过的参数留空，不要编造。

初始化后立即运行：

```bash
python3 <project-root>/.ai-coding-java/scripts/check_target_project.py <project-root>
```

结果要求：`fail` 数必须为 0。`project-profile.md` 中数据边界等仍未确认的字段，在最终输出里显式列为
`Not-tested`，不得编造确认值。

### git 钩子

默认**不装** git 钩子（初始化命令固定带 `--hooks skip`）。钩子会在 `pre-commit` 时做 P0/P1 静态检查，
P0 命中会**拦截提交**；仓库已有同名钩子时会被自动备份（`.before-ai-coding-java.<timestamp>` 后缀）并
串联执行，不会丢失原钩子。

向用户说明上述行为后，**用户同意**才执行：

```bash
python3 .ai-coding-java/scripts/install_git_hooks.py .
```

安装前先查一次 `git config core.hooksPath`：非空（如 husky 场景）时安装脚本会自动打印 `SKIP` 并跳过——
钩子目录可能被跟踪，强行安装会产生带本机绝对路径的 tracked 改动。这种情况下不要用
`--allow-hooks-path` 强行绕过，除非用户明确要求。

---

## 第四点五阶段：worktree 适配

被排除机制（`.git/info/exclude` 或 `.gitignore`）挡住的未跟踪文件**不会出现在新建的 worktree 里**，
派 Codex `--cd <worktree>` 时会读不到 `AGENTS.md`。新建 worktree 后执行：

```bash
for f in AGENTS.md CLAUDE.local.md .ai-coding-java .omx; do
  [ -e "<main>/$f" ] && [ ! -e "<worktree>/$f" ] && ln -s "<main>/$f" "<worktree>/$f"
done
```

说明：

- `.git/info/exclude` 在 common dir（`.git/`）里，对所有 worktree 生效，软链本身不会出现在
  `git status` 里。
- `.omx/notepad.md` 因此是**跨 worktree 共享**的；需要按 worktree 隔离当前任务记忆时，只软链
  `.omx/project-memory.json`，`notepad.md` 单独在各 worktree 内建各自的文件。

验证：worktree 内 `git status --short` 为空，且 `test -f AGENTS.md` 成立。

---

## 第五阶段：记忆分层（分清，别互相覆盖）

| 记忆层 | 位置 | 谁管 | CC 是否自动加载 |
|--------|------|------|----------------|
| **CC 自动记忆**（权威层） | `~/.claude/projects/<encoded-cwd>/memory/*.md` + `MEMORY.md` | CC 自动维护 | ✅ 每会话 |
| **本机项目入口** | `./CLAUDE.local.md` | 本 skill（默认产物） | ✅ 每会话（已哨兵实测） |
| **团队项目契约** | `./CLAUDE.md` | 项目团队，多人模式下本 skill 默认不碰；单人模式下此文件即本机入口 | ✅ 每会话（若存在） |
| **OMX/Codex 记忆** | `./.omx/`（`project-memory.json` / `notepad.md` / `state/`） | omx 或手工 | ❌ Codex 侧读 |
| **项目内人读摘要**（可选） | `./.claude/memory/MEMORY.md` | 手工 | ❌ **CC 不自动读** |

> 纠偏：CC 自动加载的是 `CLAUDE.md` / `CLAUDE.local.md` 与**全局** `~/.claude/projects/<encoded>/memory/MEMORY.md`，
> **不是**项目内 `.claude/memory/MEMORY.md`。别误以为写进项目内那个就会被加载。

沉淀约定写进 AGENTS.md：新增/改模块记路径+入口类+表名+测试命令；修 Bug 记现象+根因+方案+涉及文件
（不记敏感日志）；技术决策记背景+选择+理由+影响；改配置记配置项+用途+环境差异（敏感值脱敏）。

---

## 第六阶段：验证（逐条实跑，不靠推断）

1. 列出新增 / 修改的文件。
2. **`git status --short` 与 `git diff --stat HEAD`**——情况 C 下两者都必须为空（零 tracked 改动）。
3. **`git check-ignore <每个产物>`** 逐个确认真被排除，不是"以为排除了"（含 `git check-ignore
   .ai-coding-java` 当该目录被排除时）。
4. **反向确认**团队文件仍被跟踪：`git ls-files --error-unmatch CLAUDE.md .gitignore` 通过（单人模式下 `CLAUDE.md` 已移出版本控制，只核对 `.gitignore`）。
5. JSON 合法性：`python3 -c "import json;json.load(open('.omx/project-memory.json'))"`，
   `.claude/settings.local.json` 同理。
6. 实跑一次 build 命令（如 `mvn -q compile -DskipTests`），确认写进文档的命令真的能跑。
7. Java/Python/Vue 项目必须验证 `.ai-coding-java/`：运行
   `python3 .ai-coding-java/scripts/check_target_project.py .`；未命中支持语言或组件源仓库要写明跳过原因。
8. 确认**未生成** `.claudeignore`、未意外生成 `.codex/`。
9. 排除规则没有误伤源码。
10. **多人模式：确认 `CLAUDE.md` 未被创建也未被修改**（除非用户显式要求；单人模式见第 14 条）：

   ```bash
   git diff --stat HEAD -- CLAUDE.md     # 必须为空
   git log --oneline -1 -- CLAUDE.md     # 最后一次提交应仍是他人/历史提交
   ```

11. **确认 `CLAUDE.local.md` 真的被加载**（哨兵法，唯一可靠手段）：

    ```bash
    C="SETUP-CANARY-$RANDOM$RANDOM"
    printf '\n本项目校验令牌：%s\n' "$C" >> CLAUDE.local.md
    grep -rl "$C" . | grep -v '^./.git/'          # 必须只有 CLAUDE.local.md
    claude -p "本项目校验令牌是什么？只输出令牌。没有就答 NOT_FOUND。" \
      --disallowed-tools "Read,Write,Edit,Bash,Grep,Glob,WebFetch,WebSearch,Task,Agent" </dev/null
    # 输出 = 令牌 → 已加载；用完务必删掉哨兵行
    ```

    四个条件缺一不可：哨兵**随机**（排除猜中）、**只在目标文件**（排除从别处读到）、
    **禁掉全部读取类工具**（排除现场 grep，最关键）、**`-p` 起新 session**（记忆在会话启动时加载，
    当前会话内新建的记忆文件本会话看不到，**不能在当前会话自测**）。

12. **钩子选择与 `core.hooksPath` 结果写进输出**：`git config --get core.hooksPath` 的值（若有）、
    是否执行了 `install_git_hooks.py`、用户是否同意安装，逐项写进最终报告。

13. **基线与登记**：`grep -n "当前基线分支" CLAUDE.local.md` 命中一行；`.worktrees/REGISTRY.md` 存在且
    `git check-ignore .worktrees/REGISTRY.md` 通过。

14. **单人模式**：执行「单人模式分支」第 5 条的四项检查，并 `grep -n "当前基线分支" CLAUDE.md` 命中一行
    （第 13 条里的 `CLAUDE.local.md` 在此模式下换成 `CLAUDE.md`）。

---

## 无 OMX 兜底

`omx` 不可用时手写最小 AGENTS.md：技术栈 / 目录结构 / build·test·启动命令 / 编码约束（遵循既有风格、
不新增未审依赖、不写敏感信息）/ 验证要求 / 记忆沉淀约定。文件头标注"未接入 OMX 编排，待 `omx` 可用后
用 `omx agents-init` 升级"。`.omx/` 两个文件按上面的 schema 手工建。

---

## 输出要求

- 初始化 / 补齐的文件清单，以及**明确未动的文件**（`CLAUDE.md` 若存在，必须显式声明"未修改"）。
- 判定的仓库归属（A / B / C）与据此选择的排除机制。
- 开发模式（单人 / 多人）及依据；单人模式下被移出 git 的文件清单（文件名、原作者、最后提交 hash）。
- Codex 与 Claude Code 的入口行为摘要；Claude Code 入口应为 `CLAUDE.local.md`，
  若本次动了 `CLAUDE.md`，必须写明是用户哪一句显式要求的。
- Java/Python/Vue 项目的 `.ai-coding-java/` 接入结果；未命中支持语言或组件源仓库必须写明跳过原因。
- Git 钩子是否安装及理由（`--hooks skip` 默认跳过 / `core.hooksPath` 阻断跳过 / 用户同意后已安装）。
- worktree 适配方式：是否需要软链、已软链的文件清单。
- 当前基线分支（用户确认值）与 `REGISTRY.md` 状态（新建 / 已存在且一致 / 不一致项）。
- 全局前置自检结果：缺失的全局工具清单（未代装）。
- 本地自测入口：`db-cli.sh` 位置与环境变量名、启动命令与 profile / 端口，未验证项标 `Not-tested`。
- 三层记忆路径。
- 能力路由结论。
- **验证证据**（命令 + 实际输出，不是"应该没问题"）。
- 所有 `Not-tested` 项与刻意跳过项，都要写明理由。
