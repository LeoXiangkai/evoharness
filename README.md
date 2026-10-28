# ai-coding-java

**EvoHarness**：AI Coding 规则 + Harness 约束 + 自进化循环（使用记录采集 → 管理员复盘 → 框架改进）。

可复用的 AI Coding 全局安装组件与项目档案工具。仓库名保留，能力覆盖 Java、Python、Vue：统一规则路由、需求与设计流程、验证证据、交付模板和轻量 Git 保护。

## 两层职责

- **全局层**：`installer/aicj.py` 将语言无关的 `core/`、语言包 `packs/` 和按需选择的 `adapters/` 安装到 `~/.claude`。默认复制，`--link` 使用软链接。`--codex` 额外把 skills 链接到 `~/.agents/skills`。
- `aicj install` 默认安装 `claude-hud` 状态栏（目录、git 分支、上下文、模型），需要 Node.js；已有其它状态栏时不会替换。可用 `--no-hud`（Windows 脚本为 `-NoHud`）关闭，安装后可用 `/claude-hud:configure` 调整显示项。
- **项目档案层**：`skills/setup-ai-coding/SKILL.md` 初始化目标项目工作区，用 `scripts/init_target_project.py` 写入 `.ai-coding-java/` 和入口 marker。素材来自根 `rules/`、`workflow/`、`templates/`、`docs/`；根 `rules/` 是 Java 8 / Spring Boot 2 项目档案规则，保留供既有项目使用。

> **平台支持：macOS / Linux / Windows / WSL。** Windows 建议安装 Git for Windows；使用 `py -3 installer/aicj.py ...`。`--link` 与 `--codex` 没有软链权限时自动改为复制，改仓库不会即时生效。Windows 暂不合并 Codex hooks（行为尚未确认）。

### WSL

WSL 内的 Claude Code 使用 WSL 自己的 `~`，可直接在 WSL 内安装。Windows 版 Claude Code 请在 Windows 终端用 `py -3 installer/aicj.py ...` 安装；也可以在 WSL 中把 `--home` 指向 `/mnt/c/Users/<名>`，安装器会自动转交 Windows Python。自定义 automount 根从 `/etc/wsl.conf` 的 `[automount] root` 读取；转交到 Windows 时 `--link` 会改为复制安装。已知限制：防重复转交的 `AICJ_WSL_HANDOFF` 未经 `WSLENV` 传到 Windows 侧，因 Windows 上不判定为 WSL，不会形成循环。仓库建议克隆到 WSL 文件系统（如 `~/src`），不要放在 `/mnt/c` 下，以避免读写、换行符和权限位问题。

安装行为以[全局安装设计](docs/global-install-design.md)为准；项目规则从[规则索引](docs/rule-index.md)进入。技能来源见[运行时边界](docs/runtime-skill-boundary.md)，项目注入细节见[使用指南](USAGE.md)。

## 目录地图

| 目录 | 职责 |
|---|---|
| `core/` | 语言无关规则、refs、角色、skills、hooks、bin 与全局入口模板 |
| `packs/java/`、`packs/python/`、`packs/vue/` | 语言规则与验证 skills |
| `adapters/` | executor、lesson 与 optional-plugins 适配契约 |
| `installer/` | CLI、安装清单、冲突处理、配置合并、status 与 doctor |
| `rules/`、`workflow/`、`templates/`、`docs/` | 项目档案素材；不替代全局语言包 |
| `skills/setup-ai-coding/` | 项目初始化 skill 源 |
| `scripts/`、`hooks/` | 项目初始化、结构/脱敏检查和项目 Git hooks；`scripts/static_review_check.py` 仍供仓库 pre-commit 使用 |
| `tests/` | 安装器、语言包、hooks、adapters 与入口文档测试 |

## 快速开始

在组件仓库执行，先看命令帮助并预览，不写真实用户目录：

```bash
python3 installer/aicj.py --help
python3 installer/aicj.py install --help
python3 installer/aicj.py install --codex --dry-run
```

再用临时目录完成安装、检查和卸载试用：

```bash
AICJ_TRIAL_HOME=$(mktemp -d)
python3 installer/aicj.py install --codex --home "$AICJ_TRIAL_HOME"
python3 installer/aicj.py status --home "$AICJ_TRIAL_HOME"
python3 installer/aicj.py doctor --home "$AICJ_TRIAL_HOME"
python3 installer/aicj.py uninstall --home "$AICJ_TRIAL_HOME"
find "$AICJ_TRIAL_HOME" -type f
# 检查残留后清理这次试用目录
rm -rf -- "$AICJ_TRIAL_HOME"
```

确认试用结果后，用户可执行真实安装；以下命令会写当前用户 home：

```bash
python3 installer/aicj.py install --codex
python3 installer/aicj.py status
python3 installer/aicj.py doctor
python3 installer/aicj.py uninstall --dry-run
python3 installer/aicj.py uninstall
```

目标项目初始化使用 `$setup-ai-coding`。它只读检查全局层；缺失时给出安装建议，不自动安装全局组件。Claude 项目 marker 默认写 `CLAUDE.local.md`。

### Windows 普通电脑安装

首选一键脚本。在 PowerShell 中运行下面一行即可自动安装 Git、Python、Claude Code，获取仓库，安装 aicj 并执行 selftest。远程 `iex` 写法用 scriptblock，执行结束后不会关闭当前窗口：

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/LeoXiangkai/ai-coding-java/main/scripts/windows/install.ps1).TrimStart([char]0xFEFF)))
```

带参数时同样保留 scriptblock 调用形式，例如：`& ([scriptblock]::Create((irm https://raw.githubusercontent.com/LeoXiangkai/ai-coding-java/main/scripts/windows/install.ps1).TrimStart([char]0xFEFF))) -Mode worker -CpaToken $env:CPA_TOKEN`。

已经克隆仓库时，在仓库根目录运行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\install.ps1
```

脚本会检查并安装前置工具、更新或克隆仓库、执行 `install`，再运行 `selftest`。默认 `-Mode cc`，B/C 档使用 Claude Code 子代理；需要让执行体经本机 CPA 使用其他后端或更便宜模型时选 `-Mode worker -CpaToken <key>`，它会安装 executor 适配并先检查 CPA。相同账号场景用 cc 更快更省。`-Mode worker` 未传 token 时交互读取，非交互会话会报错；`-InstallCpa` 可先安装本机 CLIProxyAPI。selftest 中 cc 模式不适用的 worker 项显示 `N/A`，不计入 SKIP。

默认会在 `claude` 同目录创建 `cc.cmd`（等价于 `claude --dangerously-skip-permissions`，跳过权限确认但 aicj 钩子照常生效）。用 `-NoCcLauncher` 关闭；`-HomeDir` 时不创建。它不随 `aicj uninstall` 删除，需要时手动删 `cc.cmd`。

CPA 可单独安装（不会默认登录任何服务商）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\install-cpa.ps1
```

使用各服务商账号须遵守其服务条款。

手动安装：没有 winget 时，从 python.org 和 git-scm.com 下载安装包，按默认选项安装，再执行 `py -3 installer/aicj.py install`。Git for Windows 是必需的。

`py -3 --version`（需 3.9+）、`git --version`、`claude --version` 都能打印版本后，下载仓库并安装：

```powershell
cd ~
git clone https://github.com/LeoXiangkai/ai-coding-java.git
cd ai-coding-java
py -3 installer/aicj.py install --dry-run
py -3 installer/aicj.py install
py -3 installer/aicj.py doctor
py -3 installer/aicj.py selftest
```

- 克隆失败时，可在 GitHub 页面用 Code → Download ZIP 下载并解压，再进入解压目录执行。
- `--dry-run` 只列出将写入 `C:\Users\<名>\.claude` 的文件；已有同名文件默认跳过，加 `--on-conflict backup` 则备份后覆盖。
- Windows 默认复制安装：更新时先 `git pull`，再执行一次 `install`。
- 卸载用 `py -3 installer/aicj.py uninstall`，只删除安装清单记录的文件。

## 常用参数

| 参数 | 用途 |
|---|---|
| `--dry-run` | 预览 install/uninstall，不落盘 |
| `--home <dir>` | 隔离用户目录；开发验证必须使用临时目录 |
| `--source <dir>` | 指定组件源仓库 |
| `--global` / `--project <dir>` | 默认全局安装；后者指定项目归档目录 |
| `--packs auto` / `--packs java,python,vue` | 自动探测或指定语言包 |
| `--adapters executor,lesson,jev,verify-probe` | 按需选择适配器；默认不装 |
| `--link` | 使用软链；源仓库需保持可访问 |
| `--on-conflict skip` / `--on-conflict backup` | 默认跳过冲突；或备份后替换 |
| `--strict` | 写入 `.claude/aicj/config.json` 将拦截类 hooks 切为 block；默认 warn，也可设置 `AICJ_HOOK_MODE=block` |
| `--enable-hook <name>` | 点名注册可选 hook，可重复；未点名脚本仍安装但不注册 |
| `--codex` | 额外安装 Codex skill 链接 |
| `--codex-hooks` | 显式合并 Codex hooks.json |
| `--no-hud` | 不安装 claude-hud 状态栏 |
| `selftest` | 在临时仓库执行安装行为自检 |

参数适用命令以对应 `--help` 为准。

## 团队安装

加入团队采集（可选，默认不采集）：`python3 installer/aicj.py install --team-config <URL|file://|路径>`（Windows：`install.ps1 -TeamConfig`）。只上传枚举/计数/哈希，不含源码、提示词或命令原文。字段见 `docs/telemetry-fields.md`，管理员操作见 `docs/team-telemetry-admin.md`，契约见 `docs/team-telemetry-design.md`。

## 不破坏用户内容的约定

- 已有文件 sha256 相同则 `adopted`，不写；内容不同默认 `skipped`。同源软链记为 `adopted-symlink`。
- `--on-conflict backup` 先备份再替换；skill 按整目录判定冲突，避免混装。
- `settings.json` 仅合并 `hooks` 键，保留已有 hooks 和其他设置；卸载精确移除我方注册项。
- `~/.claude/CLAUDE.md` 只增加或更新 ai-coding-java marker block，引用全局入口。
- manifest 记录归属与 sha256。卸载只删除我方拥有且未修改的文件，恢复替换前备份，保留 adopted 条目、用户修改和用户数据。用户修改导致残留时会告警。
- 源目录中的 `__pycache__`、`.pyc`、`.pyo` 不进入安装清单。

## 语言包探测与自测

`--packs auto` 在当前工作目录探测；全局安装未命中任何包时安装全部可用语言包。规则按语言路径生效。以安装器实际探测为准：

| 包 | 探测条件 | 自测入口 |
|---|---|---|
| Java | `pom.xml` 或 `build.gradle*` | `packs/java/skills/java-verify/scripts/detect_java.py`；`packs/java/skills/java-verify/scripts/static_review.py`；`tests/test_java_pack.py` |
| Python | `pyproject.toml`、`requirements*.txt` 或 `setup.py` | `packs/python/skills/python-verify/scripts/detect_python.py`；`packs/python/skills/python-verify/scripts/static_review.py`；`tests/test_python_pack.py` |
| Vue | `package.json` 的 dependencies 或 devDependencies 含 `vue` | `packs/vue/skills/vue-verify/scripts/detect_vue.py`；`tests/test_vue_pack.py` |

语言验证 skill 从目标项目提取实际构建、测试、启动命令；未验证的命令明确标记 `Not-tested`。

## 可选适配器

| 适配器 | 能力与前置条件 |
|---|---|
| executor | 外部执行体交接契约、示例 worker、交接后轮询保护；不提供个人环境配置 |
| lesson | 教训召回/捕获机制与空数据骨架；不携带已有教训数据 |
| jev | consumer 契约、工具壳和模板；需要 node 与若已安装的 jev-assist |
| verify-probe | 只读验证探针与示例 profile；需要 git，实际项目 profile 由项目提供 |

依赖不足的可选插件报 `SKIPPED`，装好依赖后即可用，不冒充已验证能力。

## Codex 与第三方技能

Claude skills 位于 `~/.claude/skills`；`--codex` 只额外创建 `~/.agents/skills` 链接。不改 `~/.codex/AGENTS.md`；规则由项目 `AGENTS.md` marker 路由。`~/.codex/hooks.json` 仅 `--codex-hooks` 时合并。

grilling、research、prototype、domain-modeling、writing-for-agents 是第三方 skills，本仓库不提供。**若已安装**，按对应场景调用；缺失时分别使用需求问答、来源调查、临时原型、术语/ADR 整理和紧凑文档编写作为人工流程兜底，不写未安装的 skill 调用。doctor 探测 `~/.agents/skills`，缺失提示手动安装。

doctor 状态：`PASS` 表示检查项可用；`WARN` 表示修改、冲突、PATH 或第三方能力等提示；`SKIPPED` 表示未选择、未启用或依赖不足的可选能力；`MISSING` 表示必要文件、清单、入口 marker 或已登记 hook 缺失。存在 `MISSING` 时退出 1；只有 WARN/SKIPPED 不使 doctor 失败。

## 维护验证

```bash
python3 -m pytest tests -q -p no:cacheprovider
python3 scripts/sanitize_check.py
python3 scripts/template_integrity_check.py
python3 scripts/context_budget_check.py
rg -n "v1\.0|reference-baseline|当前项目按本地|强制 hooks" README.md AGENTS.md CLAUDE.md TOOL.md USAGE.md docs rules workflow templates scripts
wc -c README.md AGENTS.md CLAUDE.md
find . -name __pycache__ -not -path "./.git/*"
```

安装闭环使用上述临时 home 试用。检查失败时报告具体项；缺失 `.omx/notepad.md` 也应如实报告，不为消除检查结果而生成无关文件。
