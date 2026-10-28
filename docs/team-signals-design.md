# 团队采集补充信号设计（返工原因 / skill 使用 / 询问次数）

状态：已确认（2026-10-28）。上游：`docs/team-telemetry-design.md`（采集与上传）、`docs/team-weekly-design.md`（周复盘）。

## 1. 目标与边界

周复盘的四段表里，需求、开发、测试、验收几段大多是「无数据」。本期补三项信号，让管理员能回答「返工主要因为什么」「哪些 skill 没用上或用了常被纠正」「Claude 向成员提问多不多」。

**目标**
- 返工原因：每条用户纠错在**成员本机**归入 A–G 一类，只上传类别字母。
- skill 使用：记录每次 skill 调用；框架自带的 skill 记名字，其他只记哈希；记录调用后同会话内是否被纠正。
- 询问次数：记录 Claude 通过 `AskUserQuestion` 向成员提问的问题数。

**非目标**
- 不上传纠错原话、skill 参数、问题文本。
- 不在成员机器上调用大模型；分类只用标准库关键词规则。
- 不统计 Claude 在普通回复里用文字提的问题（无法可靠识别）。

## 2. 组成与落点

| 部分 | 文件 | 说明 |
|---|---|---|
| 事件契约 | `core/hooks/aicj_telemetry.py` `SCHEMA` | `correction` 加字段；新增 `skill_use`、`ask_user` 两种事件 |
| 返工分类规则 | `core/hooks/aicj_telemetry.py`（`REWORK_RULES`） | 关键词表 + 规则版本号，采集时本机分类 |
| 采集 | `core/hooks/telemetry-capture.py` | 新增处理 `Skill`、`AskUserQuestion` 工具和 `UserPromptSubmit` 的斜杠命令 |
| 注册 | `core/manifest.json` | PostToolUse 加 `Skill\|AskUserQuestion` 匹配；新增 UserPromptSubmit（非阻塞） |
| 汇总 | `scripts/telemetry_aggregate.py` | 新增 rework_cause / skill / ask_user 三个数据族 |
| 周报 | `scripts/team_weekly.py` | 新指标键、四段表取数改用新信号 |
| 文档 | `docs/telemetry-fields.md`、`docs/team-telemetry-admin.md` | 字段说明与解读 |

## 3. 数据契约

### 3.1 `correction`（已有事件，加字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `cause` | enum `A`..`G` | 返工原因，见 3.4 |
| `cause_rules` | token | 规则版本，如 `r1`；调规则后旧数据可区分 |
| `after_skill` | token | 纠错前 10 分钟内同会话最近一次 skill 调用的名字（规则同 3.2），没有则缺省 |

来源仍是 lesson 适配器的 `lessons/corrections.jsonl`，在 `collect` 时读原话分类，原话不进事件。

### 3.2 `skill_use`（新事件）

| 字段 | 类型 | 说明 |
|---|---|---|
| `skill` | token | 名字在框架词表内记明文，否则 `x-<哈希>` |
| `via` | enum `tool` / `slash` | Claude 调用 Skill 工具，或成员输入斜杠命令 |

- 框架词表 = `core/skills/*` 与 `adapters/*/skills/*` 的目录名，写成常量；测试保证与目录一致。
- 插件 skill 名（`plugin:skill`）同样按词表判断，不在表内就哈希。
- 斜杠命令排除 Claude Code 内置命令（`/help`、`/clear`、`/compact`、`/model` 等，固定列表），不产生事件。
- 本机另写一份 `telemetry/skill-log.jsonl`（会话哈希、时间、skill 名），只供 3.1 的 `after_skill` 关联，保留 14 天，不上传。

### 3.3 `ask_user`（新事件）

| 字段 | 类型 | 说明 |
|---|---|---|
| `questions` | int | 本次 `AskUserQuestion` 的问题数（1–4） |

`event_id` 沿用现有做法，由 session_id + tool_use_id 生成，PostToolUse 与 PostToolUseFailure 重复触发时去重。

### 3.4 返工原因关键词规则（`r1`）

按顺序匹配，命中即停；都不命中记 `G`。中英文关键词、大小写不敏感。

| 类 | 含义 | 关键词示例 |
|---|---|---|
| A | 需求口径 | 需求、口径、不是这个意思、我要的是、理解错、不是我要的、requirement |
| B | 影响点后知后觉 | 漏了、还有…也要、其他地方、也得改、同步改、忘了、影响到、missed |
| F | 测试写错 | 测试、用例、断言、mock、test |
| E | 审查打回 | 审查、评审、review、规范、命名、注释、不符合约定 |
| C | 环境·工具·数据 | 环境、配置、端口、代理、依赖、版本、数据库、数据不对、权限、登录、proxy、env |
| D | 代码缺陷 | 报错、bug、异常、空指针、崩、跑不起来、不生效、逻辑错、error |

- 顺序理由：A/B 是流程问题，比「报错」这类症状词更能说明原因，所以排在前面；F 在 D 前，避免「测试报错」被归为代码缺陷。
- 规则是粗分类。周报同时给出 G 占比，作为规则覆盖率；G 连续两周超过 50% 时，周报提示管理员调规则。
- 纠错不等于返工：lesson 适配器的识别本身有误报，这里不再二次过滤。

## 4. 汇总与周报

**`telemetry_aggregate.py` 新增数据族**
- `rework_cause`：各类条数与占比、G 占比、按规则版本分开。
- `skill`：每个 skill 的调用数、`via` 拆分、调用后被纠正数（`after_skill` 命中）。
- `ask_user`：问题总数、每任务天问题数。

**`team_weekly.py` 新指标键**

| 键 | 含义 | 方向 |
|---|---|---|
| `rework_share:<A..G>` | 该类占本周纠错的比例，分母为纠错数 | 越低越好（G 仅提示，不判） |
| `ask_user_per_task_day` | 问题数 / 任务天数 | 越低越好 |
| `skill_corrected_ratio:<skill>` | 调用后被纠正数 / 调用数 | 越低越好 |

样本门槛沿用 min-n。

**四段表取数**

| 阶段 | 新增取数 |
|---|---|
| 需求 | A 类占比；`ask_user_per_task_day`；`req-intake` / `prd` / `req-analysis` 调用数与被纠正比 |
| 开发 | C、D 类占比 |
| 测试 | F 类占比 |
| 验收 | E 类占比；`code-review` 调用数 |

**减法候选**新增「闲置 skill」：框架自带 skill 连续 4 周调用 0 次，只列信号。

## 5. 隐私与失败隔离

- 上传内容只有类别字母、规则版本、词表内的 skill 名或哈希、问题数。
- 原话与问题文本只在本机分类时读取，不写入事件、spool 和 skill-log。
- 分类、关联、写 skill-log 任一步出错都吞掉，不影响 Claude Code 操作和其他事件。

## 6. 测试归属表

| # | 行为 | 层 | 断言 |
|---|---|---|---|
| S1 | 关键词规则按顺序分类，未命中为 G | unit | 每类至少 2 条样本句 + 冲突句（「测试报错」→ F） |
| S2 | correction 事件不含原话 | unit | 事件序列化后不含样本原话任一片段 |
| S3 | 框架词表与 `core/skills`、`adapters/*/skills` 目录一致 | unit | 集合相等 |
| S4 | 词表外 skill 名转哈希；内置斜杠命令不出事件 | unit | `x-` 前缀；`/help` 无事件 |
| S5 | `after_skill` 关联：10 分钟内同会话命中，超时或跨会话不命中 | unit | 三种情况 |
| S6 | `ask_user` 问题数与重复触发去重 | unit | 同 tool_use_id 两次只一条 |
| S7 | skill-log 超过 14 天的行被清掉，且不进上传 | unit | 文件内容 + 上传文件扫描 |
| S8 | 汇总与周报新指标数值、样本不足、G 占比提示 | unit | 造数据算出精确值 |
| S9 | 四段表使用新取数 | unit | 对应行包含新键 |
| S10 | 临时 HOME 端到端：真实 hook 入口产生三类事件 → 上传 → 汇总 → 周报 | integration | 事件计数、上传文件无原话、周报行 |
| S11 | 分类或 skill-log 出错不影响 hook 退出码 | unit | 注入异常后退出码 0 |

## 7. 验收

- pytest 全绿 0 skip；sanitize / template / context_budget 退出码 0。
- S10 端到端通过；上传文件对纠错原话、问题文本、私有 skill 名检索 0 命中。
- 独立审查通过后合入 main。

## 8. 风险与决策点

- **返工原因依赖 lesson 适配器**：纠错只由 lesson 适配器采集。团队安装若不带 lesson，返工原因整块为空。已定：带 `--team-config`（Windows `-TeamConfig`）安装时自动加入 lesson 适配器；用户显式给的适配器列表保留并追加 lesson。
- **关键词分类精度未知**：上线前无真实数据可校准，靠 G 占比和管理员抽查调规则。
- **新增 UserPromptSubmit hook**：每次提交提示词多一次进程启动（毫秒级），只做前缀判断。
- **Claude Code 内置命令列表会变**：漏掉的内置命令会以哈希形式出现，管理员能看到但不影响判断。
