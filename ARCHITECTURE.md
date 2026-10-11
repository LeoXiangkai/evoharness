# 架构图与流程图

本文用 Mermaid 描述 ai-coding-java 的组成与运行流程。安装行为的权威契约是 [全局安装设计](docs/global-install-design.md)，阶段门见 [阶段门设计](docs/lifecycle-gates-design.md)；图与文字冲突时以这两份为准。

## 1. 总体架构

组件分两层：**全局层**把规则、角色、skills、钩子装进用户的 `~/.claude`；**项目档案层**往目标项目写 `.ai-coding-java/` 与入口标记块。

```mermaid
flowchart TB
    subgraph SRC["组件仓库（源）"]
        direction TB
        CORE["core/<br/>rules · refs · agents · skills · hooks · bin · templates<br/>+ manifest.json（条目与钩子注册表）"]
        PACKS["packs/<br/>java · python · vue<br/>pack.json · rules · skills · checks"]
        ADAPT["adapters/<br/>executor · lesson<br/>optional-plugins（jev · verify-probe）"]
        PROFILE["项目档案素材<br/>rules/ · workflow/ · templates/ · docs/ · knowledge/"]
        INIT["skills/setup-ai-coding<br/>scripts/init_target_project.py"]
    end

    subgraph INST["installer/（仅 Python 标准库）"]
        direction TB
        CLI["aicj.py<br/>install · uninstall · status · doctor · selftest"]
        LIB["lib/<br/>index（探测）· manifest · conflict<br/>settings · mdblock · hud · wsl · doctor · selftest"]
        CLI --> LIB
    end

    subgraph GLOBAL["全局层 ~/.claude"]
        direction TB
        G1["rules/ refs/ agents/ skills/"]
        G2["hooks/aicj/ · bin/"]
        G3["aicj/CLAUDE.global.md<br/>CLAUDE.md 标记块"]
        G4["settings.json<br/>仅合并 hooks 键"]
        G5["aicj/manifest.json<br/>安装日志 · sha256 · 归属"]
    end

    CODEX["~/.agents/skills<br/>--codex 软链"]
    HUD["claude-hud 状态栏<br/>探测到 Node.js 才装"]

    subgraph PROJ["目标项目（项目档案层）"]
        direction TB
        P1[".ai-coding-java/"]
        P2["AGENTS.md / CLAUDE.local.md<br/>入口标记块"]
        P3["Git hooks（用户同意后）<br/>pre-commit · pre-push"]
    end

    CORE --> CLI
    PACKS --> CLI
    ADAPT --> CLI
    LIB --> G1 & G2 & G3 & G4 & G5
    LIB -.->|--codex| CODEX
    LIB -.->|默认| HUD

    PROFILE --> INIT
    INIT --> P1 & P2 & P3
    G1 -.->|只读检查，缺失只提示| INIT

    classDef src fill:#eef6ff,stroke:#4a82c4
    classDef glob fill:#eefaf0,stroke:#4b9b5f
    classDef proj fill:#fff6e8,stroke:#c98a2b
    class CORE,PACKS,ADAPT,PROFILE,INIT src
    class G1,G2,G3,G4,G5 glob
    class P1,P2,P3 proj
```

### 目录职责

| 目录 | 职责 |
|---|---|
| `core/` | 语言无关：常驻规则、按需 refs、角色（pm/sa/dev/qa/scanner）、通用 skills、钩子、`git-safe` |
| `packs/{java,python,vue}` | 语言规则、静态检查、自测入口探测 |
| `adapters/` | 可选适配：外部执行体契约、教训闭环骨架、jev / verify-probe 探测即启用 |
| `installer/` | CLI 与安装生命周期：清单、冲突策略、settings 合并、卸载、体检 |
| `skills/`、`scripts/`、`hooks/` | 项目初始化 skill、检查脚本、项目 Git hooks |
| `rules/`、`workflow/`、`templates/`、`docs/` | 写入目标项目的档案素材（Java 8 / Spring Boot 2 规则保留） |
| `tests/` | 全部使用临时 HOME，不触碰真实 `~/.claude` / `~/.codex` |

## 2. 安装流程（`aicj install`）

先读取并校验全部输入，再落盘；manifest 为日志式，中断后可重跑。

```mermaid
flowchart TD
    A["aicj install<br/>--packs / --adapters / --link / --codex / --strict / --dry-run"] --> B{"--project ?"}
    B -- 是 --> B1["项目层入口<br/>（走 setup-ai-coding，见 §4）"]
    B -- 否 --> C["解析 --home（默认 ~）与 source"]
    C --> D["探测语言包<br/>pom.xml / build.gradle* → java<br/>pyproject / requirements → python<br/>package.json 含 vue → vue"]
    D --> E["读取清单<br/>core/manifest.json<br/>pack.json · adapter.json · plugin.json"]
    E --> F["预校验<br/>settings.json 可解析 · CLAUDE.md 标记块合法<br/>目标无重复 · 来源 realpath 在组件根内"]
    F -->|失败| FX["退出码 2，不改任何文件"]
    F --> G["manifest 写 state=in-progress"]
    G --> H{"逐个目标文件"}

    H -->|不存在| H1["写入 → created"]
    H -->|sha256 相同| H2["不写 → adopted"]
    H -->|同仓库软链| H3["不动 → adopted-symlink"]
    H -->|内容不同| H4{"--on-conflict"}
    H4 -->|skip 默认| H5["跳过并报告 → skipped"]
    H4 -->|backup| H6["改名 .aicj-bak-ts 后写入 → replaced"]

    H1 & H2 & H3 & H5 & H6 --> I["追加 manifest 条目"]
    I --> J{"还有文件?"}
    J -- 是 --> H
    J -- 否 --> K["CLAUDE.md 追加<br/>aicj:begin … aicj:end 标记块"]
    K --> L["settings.json 钩子合并<br/>备份 → 去重追加 → 原子写回 → 重新解析校验"]
    L --> M["可选步骤<br/>--codex 软链 skills · --codex-hooks · claude-hud"]
    M --> N["manifest 写 state=complete"]
    N --> O["输出报告：created / adopted / skipped / replaced"]
```

### 钩子模式

```mermaid
flowchart LR
    H["拦截类钩子触发"] --> M{"AICJ_HOOK_MODE<br/>或 .claude/aicj/config.json"}
    M -- "warn（默认）" --> W["输出提醒，放行"]
    M -- "block（--strict）" --> B["拒绝本次调用"]
    O["optional 钩子<br/>如 git-safe-rewrite"] -.->|"仅 --enable-hook 点名才注册"| H
```

## 3. 卸载与体检

```mermaid
flowchart TD
    subgraph UN["aicj uninstall"]
        U1["读 manifest（拒绝绝对路径 · .. · 未知 version）"] --> U2{"条目 action"}
        U2 -->|"created / replaced<br/>且 sha256 未变"| U3["删除（replaced 先还原备份）"]
        U2 -->|"用户改过 / sha256 为空"| U4["保留并告警"]
        U2 -->|"adopted*"| U5["不删"]
        U3 & U4 & U5 --> U6["按记录精确删除 settings 钩子与 md-block<br/>清理我方创建的目录与备份"]
    end

    subgraph CHK["只读检查"]
        S["aicj status<br/>已装 / 未装 / 与仓库不同 + diff 命令"]
        D["aicj doctor<br/>PASS · WARN · MISSING · SKIPPED"]
        T["aicj selftest<br/>临时仓库验证安装效果"]
    end
```

## 4. 项目初始化流程（`$setup-ai-coding`）

只做项目级，不动全局环境；全局层缺失时给出安装建议，不自动安装。

```mermaid
flowchart TD
    A["用户调用 setup-ai-coding"] --> B["只读检查全局层<br/>（aicj status / doctor）"]
    B -->|缺失| B1["给出安装建议，不自动安装"]
    B --> C["确认项目参数<br/>project-type · stack · verification-level<br/>template-policy · data-boundary · claude-entry"]
    C --> D["init_target_project.py 目标目录"]
    D --> E["复制素材到 .ai-coding-java/<br/>docs · rules · workflow · templates<br/>knowledge · artifacts · hooks · 检查脚本"]
    E --> F["写入口标记块<br/>AGENTS.md（Codex）<br/>CLAUDE.local.md 或 CLAUDE.md（Claude）"]
    F --> G{"用户同意安装 Git hooks?"}
    G -- 是 --> G1["install_git_hooks.py<br/>已有同名钩子自动备份并串联"]
    G -- 否 --> H
    G1 --> H
    H["check_target_project.py 自检<br/>结构 · 脱敏 · 模板完整性"]
    H --> I["输出初始化报告"]
```

## 5. 研发生命周期与阶段门

`flow` 只读，按产物与证据判断当前阶段并给出唯一下一步；门的判据只在 `sdd-pipeline` 写一份。缺证据标「未记录」，不等同通过。

```mermaid
flowchart LR
    R["需求<br/>req-intake / req-analysis / prd"] --> G1{{"门1<br/>需求冻结"}}
    G1 --> DEV["开发<br/>add-module / auto-sdd<br/>契约冻结 · TDD"]
    DEV --> G2{{"门2<br/>设计/编码<br/>diff 自审有结论"}}
    G2 --> T["测试<br/>local-verify<br/>qa-testcase-pro"]
    T --> G3{{"门3<br/>QA 证据<br/>PASS 也留证据"}}
    G3 --> ACC["验收<br/>code-review 判档"]
    ACC --> G4{{"门4<br/>验收判据<br/>需求全覆盖"}}
    G4 --> DONE["交付<br/>git-commit"]

    G1 -.未过.-> R
    G2 -.未过.-> DEV
    G3 -.BLOCKED.-> T
    G4 -.未过.-> ACC
```

### 规模分档

```mermaid
flowchart TD
    S["新任务"] --> Q{"判档"}
    Q -->|"文档 / 配置 / ≤5 行零风险"| X1["小改<br/>仅门2 自审<br/>门3 以一次实际运行代替"]
    Q -->|"单模块 · 无高风险信号"| X2["轻改<br/>门1 轻量 + 门2 + 门3<br/>门4 并入收口汇报"]
    Q -->|"中改 / 完整流程"| X3["门1–门4 全过<br/>add-module / auto-sdd 驱动"]
    Q -.->|"无分档信息"| X4["按完整流程提示<br/>由使用者降档"]
```

## 6. 日常使用的 Git 保护

```mermaid
sequenceDiagram
    participant Dev as 开发者 / Claude Code
    participant Hook as 钩子（warn 或 block）
    participant Git as git（项目 Git hooks）
    Dev->>Hook: git 写命令（PreToolUse）
    Hook->>Git: git-safe 重试包装（optional 钩子启用时）
    Dev->>Git: git commit
    Git->>Dev: pre-commit：static_review_check（P0 命中拦截提交）
    Dev->>Hook: git push（PreToolUse）
    Hook->>Hook: push-review-gate：检查是否完成审查
    alt block 模式且未审查
        Hook-->>Dev: 拒绝
    else warn 模式或已审查
        Hook-->>Dev: 放行 / 提醒
        Dev->>Git: git push
        Git->>Dev: pre-push：分支名与受保护分支检查
    end
```

## 查看方式

Mermaid 在 GitHub、GitLab、VS Code（Markdown Preview Mermaid Support 插件）和多数 Markdown 预览器中可直接渲染。
