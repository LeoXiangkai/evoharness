# 全局安装与分层结构设计

本文是组件从"项目级规则包"升级为"全局安装 + 项目档案"的冻结契约。实施批次、安装器行为和目录职责以本文为准；与旧文档冲突时以本文为准，旧文档在最后一批改写。

## 1. 已冻结决策

| 项 | 决策 |
|---|---|
| 安装层级 | 全局为主：rules / refs / agents / skills / hooks / bin 装进 `~/.claude`；目标项目只放项目档案（`.ai-coding-java/` + 入口文件标记块） |
| 结构 | `core/`（语言无关）+ `packs/{java,python,vue}`（语言包）+ `adapters/`（可选适配）；仓库名不改 |
| 个人环境能力 | 抽象成可选适配：外部执行体只给通用契约与示例；教训闭环只给机制与空数据骨架；JEV、verify-probe 探测到才启用。飞书、云帆、DevOps、Gitee、CPA、账号切换、遥测、代理一律不进仓库 |
| 拦截类钩子默认模式 | `warn`；用户通过配置 `AICJ_HOOK_MODE=block` 或 `install --strict` 切换 |
| Codex | 全局只装 skills（软链指向 CC 安装位置）；规则走项目 `AGENTS.md` 标记块；不改 `~/.codex/AGENTS.md`（OMX 生成物）；`~/.codex/hooks.json` 仅 `--codex-hooks` 时合并 |
| 第三方 skill | `~/.agents/skills/*`（grilling、research、prototype、domain-modeling、writing-for-agents）不入仓；`doctor` 探测到即提示可用，探测不到给出来源与安装指引 |
| 本机对接 | 本机 `~/.claude` / `~/.codex` 是迁移源头，开发期间**不得写入**：迁移只读取源文件，安装器的所有验证一律用 `--home <临时目录>`；对本机只允许跑只读的 `aicj status` / `doctor`。是否以及何时在本机执行安装由用户另行决定 |

## 2. 目录职责

```
core/
  rules/       常驻规则（会被每轮注入，只放高频刚需）
  refs/        按需规则（命中触发器才读）
  agents/      角色定义 pm / sa / dev / qa / scanner，不写 model
  skills/      通用 skills
  hooks/       通用 Claude Code 钩子脚本
  bin/         git-safe 等可执行工具
  templates/   CLAUDE.global.md 全局入口
packs/<lang>/
  pack.json    语言探测、规则清单、检查命令、自测入口
  rules/ skills/ checks/
adapters/
  executor/          外部执行体契约 + 示例适配 + 轮询拦截钩子
  lesson/            教训闭环机制 + 空数据骨架
  optional-plugins/  jev、verify-probe，各含 plugin.json
installer/
  aicj.py      入口
  lib/         manifest、冲突策略、settings 合并、codex、探测
project/       项目档案层（由现有 init_target_project 演进）
tests/         安装器与钩子测试，全部使用临时 HOME
```

`core/`、`packs/`、`adapters/` 内禁止出现个人绝对路径、个人账号、私有服务地址与密钥；由 `scripts/sanitize_check.py` 扫描，命中即失败。

## 3. 安装器契约

### 平台差异

安装器支持 macOS、Linux、Windows 和 WSL。Windows 使用 `py -3 installer/aicj.py ...`，建议安装 Git for Windows。钩子统一写为带 `args` 的 exec 形式，解释器使用安装时的绝对 `sys.executable`；`--link` 与 `--codex` 遇到软链权限错误会回退为复制并记录 checksum。Windows 额外生成 `.cmd` bin 启动器；`--codex-hooks` 在 Windows 暂不合并，doctor 标记为 SKIPPED。

安装步骤还会默认探测并安装 `claude-hud` 状态栏（目录、git 分支、上下文、模型）；缺少 Claude Code 或 Node.js 时只跳过并告警，已有其它状态栏时不替换，可用 `--no-hud` 关闭。

WSL 内运行的安装器按 Linux 工作，并使用 WSL 的用户目录。若在 WSL 中将有效 `--home` 指向 `/mnt/<盘符>/...`（或 `AICJ_WSL_MOUNT_ROOT` 下的单字母盘符目录），安装器会把脚本、目标路径及显式的 `--source` / `--project` 转换为 Windows 路径，交给 Windows Python 执行；需先安装 Python 3.9+。WSL 中的仓库源位于 Windows 盘时只给出性能、换行符和权限位警告，建议迁移到 WSL 文件系统。

### 3.1 命令

```
aicj install   [--global | --project <dir>] [--packs auto|java,python,vue] [--adapters executor,lesson,jev,verify-probe]
               [--codex] [--codex-hooks] [--strict] [--enable-hook <name>]... [--link] [--on-conflict skip|backup] [--no-hud] [--dry-run] [--home <dir>]
aicj uninstall [--global | --project <dir>] [--dry-run] [--home <dir>]
aicj status    [--home <dir>]      只读：逐项列出 已装/未装/与仓库不同，并给出 diff 命令
aicj doctor    [--home <dir>]      只读：PASS / WARN / MISSING / SKIPPED
aicj selftest  [--home <dir>] [--source <dir>] [--with-claude]  在临时仓库验证安装效果
```

- 只用 Python 3 标准库；不创建虚拟环境。
- `--home` 覆盖 `~`，测试与 dry-run 校验一律用临时目录，测试不得触碰真实 `~/.claude` / `~/.codex`。
- `--packs auto` 在当前目录探测：`pom.xml` / `build.gradle*` → java；`pyproject.toml` / `requirements*.txt` / `setup.py` → python；`package.json` 含 `vue` 依赖 → vue。全局安装且无法探测时安装全部语言包规则，但仅 `paths` frontmatter 限定的文件按语言生效。

### 3.2 写入位置

| 类别 | 目标 |
|---|---|
| rules / refs / agents | `~/.claude/{rules,refs,agents}/<name>.md` |
| skills | `~/.claude/skills/<name>/` |
| 钩子脚本 | `~/.claude/hooks/aicj/`（独立子目录，与个人钩子隔离） |
| bin | `~/.claude/bin/`；不在 PATH 时 doctor 给 WARN |
| 全局入口 | `~/.claude/aicj/CLAUDE.global.md`；`~/.claude/CLAUDE.md` 只追加 `<!-- aicj:begin -->…<!-- aicj:end -->` 标记块，块内为对该文件的引用，不存在则创建 |
| 状态 | `~/.claude/aicj/manifest.json` |

### 3.3 冲突策略

| 目标状态 | 动作 | manifest action |
|---|---|---|
| 不存在 | 写入 | `created` |
| 存在且 sha256 相同 | 不写 | `adopted` |
| 是指向仓库同一文件的软链 | 不动 | `adopted-symlink` |
| 存在且内容不同 | 默认跳过并在报告中列出；`--on-conflict backup` 时改名为 `<file>.aicj-bak-<ts>` 后写入 | `skipped` / `replaced` |

重装时以上次 manifest 为准：上次是 `created` / `replaced` 的条目沿用原归属；目标仍等于上次记录的 sha256 时视为未被用户改动，直接更新为新源内容；新版本不再提供的条目保留在 manifest 中，卸载时照常清理。

`--link` 时以软链代替复制（本机一处维护用），manifest kind 记 `symlink` 并在 `link` 字段记录目标 realpath；卸载与 doctor 比对链接目标而非内容。`--link` 与复制模式切换时，我方条目按 `updated` 重写。

目录型 skill 按整个目录判定：任一文件冲突则整个 skill 跳过（不建 codex 链接）；`--on-conflict backup` 时整目录改名为 `<skill>.aicj-bak-<ts>`，manifest 记 `skill-dir` 条目，卸载时目录清空后还原。备份文件名已存在时加序号，不覆盖；已有最早的用户原件备份时不丢失。

组件清单约束：目标不得是 `settings.json`、`CLAUDE.md`、`aicj/manifest.json`；目标不得重复；`source` 的 realpath 必须在组件根内，目录来源中的软链被跳过。

### 3.4 settings.json 钩子合并

1. 写前备份为 `settings.json.aicj-bak-<ts>`（每次安装最多保留最近 1 份，旧的我方备份随之删除）；文件不存在则从 `{}` 开始。目标是软链时写到软链指向的真实文件并保持其 mode，软链本身保留。
2. 以 `(事件, matcher, command, args)` 为唯一键：已存在跳过，不存在则追加到对应事件列表末尾；不改已有条目顺序与内容。
3. 我方钩子使用 `{"type":"command","command":"<绝对解释器>","args":["<绝对脚本>"]}` exec 形式，脚本路径经规范化后必须位于 `hooks/aicj/` 下；旧 shell 形式在升级时替换，用户钩子不动。仅当脚本条目为 created/replaced/adopted 时才注册。`SessionStart` / `Stop` / `UserPromptSubmit` / `SessionEnd` / `PreCompact` / `Notification` 不写 `matcher` 键。
4. 常规合并只触碰 `hooks` 键；`env`、`permissions`、`enabledPlugins` 等一律不读不写（`permissions` 不随安装带出），唯一例外：team.json 带 `gateway_base_url` 时只写 `env.ANTHROPIC_BASE_URL`（冲突默认跳过，`--on-conflict backup` 覆盖，旧值记入 manifest 的 `gateway`，卸载仅在值未被改动时移除或恢复；绝不读写任何 key/token）。`statusLine` 仅由 claude-hud 步骤按独立归属规则管理。
5. 写回用临时文件 + 原子 rename，写后重新解析校验。
6. 卸载按 manifest 中记录的键精确删除；删除后为空的 matcher 组与事件键一并清除。用户预先已有的同 command 条目记为 adopted，不进 manifest、卸载不删。
7. 卸载后 settings 与用户原件一致时，清理我方创建的备份。

### 3.5 manifest

```json
{
  "version": 1,
  "source": {"repo": "<abs path>", "commit": "<sha>"},
  "installed_at": "<iso8601>",
  "options": {"packs": [], "adapters": [], "codex": false, "strict": false, "link": false},
  "entries": [{"path": "...", "kind": "file|symlink|md-block|settings-hook", "action": "created|adopted|adopted-symlink|skipped|replaced", "sha256": "...", "backup": "..."}],
  "settings_hooks": [{"event": "...", "matcher": "...", "command": "..."}]
}
```

卸载只删 `created` / `replaced` 且当前 sha256（软链为 `link` 目标）仍等于记录值的文件；sha256 为空或用户改过的文件保留并告警；`replaced` 卸载时恢复备份，被用户改过时保留文件、告警写出备份路径、manifest 只保留该条目；`adopted*` 不删（含已存在的 CLAUDE.md 标记块）。

写入顺序与一致性：先读取并校验全部输入（settings、CLAUDE.md 解码与标记块、目标冲突预判），再落盘。manifest 为日志式：开始写 `"state": "in-progress"`，每落地一个条目即追加，结束标记 `"complete"`；重跑时 in-progress 日志中的条目沿用原归属。IO / 解码错误转为退出码 2 的错误信息，不输出 traceback。manifest 另记 `created_dirs`（我方创建的目录，卸载只清理这些）、md-block 条目的 `block` 原文（卸载只删精确匹配的块）；载入时拒绝绝对路径、含 `..` 或不在 `.claude` / `.codex` / `.agents` 下的路径，并拒绝未知 `version`。

CLAUDE.md 标记块：BEGIN、END 各恰好出现一次且顺序正确才是合法块；否则 install / uninstall 报错并不改文件。

### 3.6 组件清单来源

安装器不硬编码文件列表，而是读取：

- `core/manifest.json`：core 各类别的条目，以及钩子注册表（事件、matcher、脚本、是否拦截类）。
- `packs/<lang>/pack.json`、`adapters/<name>/adapter.json`、`adapters/optional-plugins/<name>/plugin.json`：同构结构，额外带 `detect`（探测条件）。

拦截类钩子脚本统一优先读取环境变量 `AICJ_HOOK_MODE`，其次读取 `.claude/aicj/config.json`（`warn` 默认 / `block`）；`--strict` 写入该配置文件。

**可选钩子**：manifest 钩子条目可带 `"optional": true`。optional 钩子的脚本文件照常安装，但**只有被 `install --enable-hook <name>`（可重复，name = 钩子脚本去扩展名）点名才注册进 settings.json**。未点名时 install 报告列出"可选钩子未启用：<name>（用 --enable-hook 开启）"；重装时不带该参数视为关闭，移除我方先前注册的该钩子（与 `--strict` 切换同样收敛）；doctor 对未启用的 optional 钩子报 SKIPPED，不报 MISSING。传入未知 name 时 install 报错退出 2。

## 4. 语言包

| 包 | 规则 | 检查 | 自测入口 |
|---|---|---|---|
| java | Java/Spring Boot、MyBatis/SQL、Java 17+/Spring Boot 3 差异规则 + Spring 隐式失效自审 | 包内 `static_review.py`（密钥、`${}`、无 where、异常吞掉、日志、代理注解、javax/jakarta） | Maven/Gradle 探测；编译与相关单测；启动日志检查；真实数据接口 curl |
| vue | Vue 3 + TypeScript + Element Plus + Pinia 主线规则，附 Vue 2 兼容差异 | 项目 eslint、vue-tsc（不新写静态检查器） | `package.json` scripts 探测 lint、type-check、test:unit / test、build、dev / serve；Playwright UI 自动化 skill 衔接 |
| python | 新写：类型注解、异常、资源、SQL 注入、Pydantic、Alembic 迁移、密钥；FastAPI / Flask Web 规则，Django 仅探测 | ruff（必需）、mypy/pyright（项目配置时）、包内 `static_review.py` | uv / poetry / pip 探测命令前缀；pytest；FastAPI / Flask 启动入口探测，无法确定报 MISSING |

## 5. 实施批次

| 批 | 内容 | 验证 |
|---|---|---|
| 1 | 安装器骨架 + manifest/冲突/settings 合并 + 测试 + `core/manifest.json` 空清单 + `sanitize_check.py` | `python3 -m pytest tests -q`；`python3 installer/aicj.py install --dry-run --home <tmp>` |
| 2 | core 常驻规则、agents、`CLAUDE.global.md` | `sanitize_check.py`；`context_budget_check.py` |
| 3 | core refs + SDD 链路 skills | `sanitize_check.py`；SKILL frontmatter 校验 |
| 4 | code-review + push 审查钩子 + git-safe 及其钩子 | 各自测试；临时 HOME 下 install → doctor → uninstall |
| 5 | java 包 | static_review good/bad 夹具 |
| 6 | vue 包 | 最小 vue 夹具探测 |
| 7 | python 包 | 最小 python 夹具探测 |
| 8 | executor、lesson 适配 | 临时 HOME install/uninstall 后无残留 |
| 9 | jev、verify-probe 可选插件 | 无依赖时 doctor 为 SKIPPED |
| 10 | 通用 skills、Codex 适配、入口文档与 setup-ai-coding 改写、旧脚本转发 | `template_integrity_check.py`；`install --codex --dry-run` |

## 6. 不迁移清单

规则治理与事故档案、大文件下载与代理、`/loop` 约束、飞书/TAP/Obsidian/配额切换、e2e 台账钩子、个人遥测与 `permissions`、教训数据文件、`verify-profiles` 实例、第三方插件资产。

## 7. 团队遥测

可选的团队采集（`install --team-config`）契约见 `docs/team-telemetry-design.md`，不属于本文冻结范围。
