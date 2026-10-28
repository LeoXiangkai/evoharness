# 团队周复盘设计（team-weekly）

状态：已确认（2026-10-28）。上游：`docs/team-telemetry-design.md`（采集与上传）、`docs/team-telemetry-admin.md`（汇总）。

## 1. 目标与边界

把采集数据变成**每周一份优化方案**，管理员逐条确认后才执行。补上采集设计里标为「运营阶段，未实现」的那一段。

**目标**
- 每周一次，读采集仓库，输出：指标周对比、到期在试项的去留判定、减法候选、落点分流后的改进提案、流程四段表。
- 管理员一次批量确认；确认结果和在试项登记都写回采集仓库，下周据此复查。
- 方案里的每个结论都引用报告数字，没有数据的写「无数据」。

**非目标（本期不做）**
- 不新增采集字段或事件类型。返工原因分类、skill 使用、提问次数放到第二期（§9）。
- 不自动执行任何改动。批准的提案按框架正常开发流程（worktree → 测试 → 合并）由人或 AI 执行。
- 不做每日方案。阈值触发的当日提醒放到第二期。
- 不读任何成员的会话内容；输入只有采集仓库里已脱敏的事件。

## 2. 组成与落点

| 组件 | 落点 | 职责 |
|---|---|---|
| 周报脚本 | `scripts/team_weekly.py` | 确定性计算：周窗口、指标、周对比、在试项判定、减法候选、四段表。只读事件，不做判断性措辞 |
| 管理员 skill | `adapters/team-admin/skills/team-weekly/SKILL.md` | 跑脚本 → 按固定顺序写方案 → 一次批量确认 → 写回决策与在试登记 |
| 适配器 | `adapters/team-admin/adapter.json` | 仅管理员安装：`aicj install --adapters team-admin`；成员默认不装 |
| 管理员数据 | 采集仓库 `admin/` 目录 | 在试台账、决策记录、周报存档。`telemetry_aggregate.load_events` 只读日期目录，不会把 `admin/` 当事件 |

脚本放 `scripts/`，与 `telemetry_aggregate.py` 同处，复用它的 `load_events` / `sanitize`；管理员在 EvoHarness 源码 clone 里运行，与现有汇总用法一致。skill 只编排，不复制脚本逻辑。

## 3. 数据契约

### 3.1 周窗口

- ISO 周，周一 00:00 到周日 24:00，按事件 `ts`（UTC）划分。周标识 `YYYY-Www`。
- 默认取「上一个完整周」；`--week` 可指定。对比期 = 前一周。

### 3.2 指标键（`metric`）

全部由现有事件计算，只用本周窗口内的事件（闲置经验卡除外，见 §4），名字固定，在试台账只能引用这些键：

| 键 | 计算 | 方向 |
|---|---|---|
| `install_failure_rate` | 失败 install / 全部 install（`command=install`） | 越低越好 |
| `test_green_rate` | `green` 的 test_run / 全部 test_run | 越高越好 |
| `first_green_index_median` | 有过绿灯的任务，`first_green_index` 中位数 | 越低越好 |
| `corrections_per_task_day` | correction 数 / task_snapshot 数 | 越低越好 |
| `rework_files_per_task` | 最新快照 `files_touched_repeatedly` 之和 / 任务数 | 越低越好 |
| `commits_after_review_per_task` | 最新快照 `commits_after_review` 之和 / 任务数 | 越低越好 |
| `bypass_total` / `bypass:<kind>` | bypass 计数（按 kind） | 越低越好 |
| `gate_deny:<rule_tag>` | 该规则 deny 次数 | 看提案语义 |
| `card_corrections_ratio:<card>` | 该卡 `corrections_after_recall / recalls` | 越低越好 |

比率类指标的分母 < `MIN_N`（默认 20）时，值记为 `null`，展示「样本不足」，不参与任何判定。

减法候选（疑似误拦、低效经验卡）是提示不是判定，豁免该 min-n，但必须标注分母 n（见 §4）。

### 3.3 `admin/trials.jsonl`（在试台账，一行一条，追加写，后写覆盖同 id）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | token | 管理员定，唯一 |
| `title` | str ≤120 | 机制名 |
| `registered` | date | 登记日 |
| `metric` | 指标键 | 必须在 §3.2 内，否则该行判为无效 |
| `baseline` | number \| null | 可填登记时的数字，或 null 由脚本自动补算：取 `registered` 所在周的**前一周**的指标值；`gate_deny:*` 在该周有事件但无此键时记 0 |
| `direction` | `down` / `up` | 期望方向 |
| `review_date` | date | 复查日 |
| `revoke_if` | str ≤200 | 撤掉条件（人读） |
| `shared_metric_with` | [id] | 同期共用该指标的在试项 |
| `deferred` | 0 / 1 | 已延期次数，最多 1 |
| `status` | `active` / `kept` / `revoked` | |
| `undo` | str ≤200 | 撤掉时怎么还原（提交号或步骤） |

约束：
- 新增 `active` 项的 `metric` 若已被另一 `active` 项占用，必须在 `shared_metric_with` 里互相登记；脚本对这类项的判定一律输出「不可单独归因」。
- `deferred=1` 的项到期后不能再延期：脚本只给「保留 / 撤掉」两种建议。

### 3.4 `admin/decisions.jsonl`

`{week, proposal_id, kind, decision, ref}`：`kind` ∈ subtract / mechanize / rule / trial_review / stage；`decision` ∈ approved / rejected / unknown（未勾选 = unknown，不写 rejected）；`ref` = 执行后的提交号或在试项 id。

### 3.5 `admin/weekly/<YYYY-Www>.json` 与 `.md`

脚本输出的指标快照与报告存档。下周对比优先读上周的 `.json`；缺失时从事件重算。

## 4. 周报脚本行为

`python3 scripts/team_weekly.py --repo <采集仓库 clone> [--week YYYY-Www] [--format md|json] [--min-n 20]`

输出各节（顺序固定，无内容的节输出一行「本周无」）：

1. **概况**：本周事件数、活跃用户数、安装数；与上周对比。
2. **指标周对比**：§3.2 全部键，本周 / 上周 / 变化；样本不足标注。
3. **到期在试项**（`review_date` ≤ 周末且 `status=active`）：当前值 vs `baseline`，按 `direction` 给「已改善 / 未改善 / 持平 / 样本不足 / 不可单独归因」；`deferred=1` 的只给保留或撤掉。持平且当前值等于该指标最优值 → 建议「保留」，其余持平 → 「人工判定」（`deferred=1` 时为「保留或撤掉（二选一，不可再延期）」）；持平不进「到期未改善」。最优值：计数 / 比率类（`bypass*`、`card_corrections_ratio:*`、各 `*_rate` / `*_per_*`，`test_green_rate` 除外）= 0；`test_green_rate` = 1.0；`first_green_index_median` = 1；`gate_deny:*` 无最优值，持平一律人工判定。
4. **减法候选**（只列信号，不下结论）：
   - 疑似误拦：`gate_deny:<rule_tag>` ≥ 5 且同一 user+task 在 deny 后 30 分钟内出现 bypass 的占比 ≥ 0.5。bypass 不带规则标识，这只是相关性提示。
   - 低效经验卡：`recalls` ≥ 5 且 `card_corrections_ratio` ≥ 0.5 → 建议改写。
   - 减法信号是提示不是判定，豁免 min-n（门槛保持 5），但每条必须带分母 n（json `n`、md `n=`）；n < min-n 时标注「低样本」。
   - 闲置经验卡：全部历史里最新一条 `lesson_card` 的 `status` 为在用，且最近 4 周 0 次 recall → 建议归档。卡片创建时间取 `lesson_card` 的 `created` 日期（缺失退回事件 `ts`），创建不足 4 周的不算闲置。`lesson_card` 只在卡片新建或变更时产生，所以卡片清单要读全部历史，不能只读窗口内。
   - 到期未改善的在试项。
5. **流程四段表**：需求 / 开发 / 测试 / 验收四行，只用现有数字；无对应数据的格子写「无数据」。

   | 阶段 | 取数 |
   |---|---|
   | 需求 | `corrections_per_task_day`（第二期加返工 A 类） |
   | 开发 | `rework_files_per_task`、`first_green_index_median` |
   | 测试 | `test_green_rate`、bypass:`skip_tests` |
   | 验收 | review outcome 的 findings / confirmed、`commits_after_review_per_task` |

6. **在试台账问题**：无效行（未知指标键、缺字段、`deferred` > 1）逐行列出，不静默跳过。

异常处理：采集仓库路径不存在或不是 git 仓库 → 退出码 2；`admin/` 不存在 → 视为空台账；台账坏行 → 进第 6 节，不中断。

## 5. 管理员 skill 流程

1. 让管理员 `git pull` 采集仓库，跑周报脚本（json + md）。
2. **减法先行**：至少提 1 项删除或合并；本期新增提案数 ≤ 删除数。来源 = 脚本第 3、4 节。
3. **在试项复查**：到期项按脚本判定给去留；未改善就撤，用 `undo`；不得第二次延期。
4. **落点分流**：每条新增提案先分类——固定模式可判定的（命令写法、路径、命名、可由脚本判断的状态）→ 落成 hook / lint / pre-commit 检查并附脚本草稿；需要判断的 → 才写进 `rules/` 或经验卡。
5. **流程四段表**：点名最拖效率的一段，只给一项改进建议，落点按第 4 步分流。
6. **一次批量确认**：所有提案并入一次多选确认；决策类单选题可标推荐，标注类多选题不标推荐。
7. **写回**：对每条提案追加 `decisions.jsonl`；批准的新机制同时追加 `trials.jsonl`，列出 §3.3 全部字段（`id`、`title`、`registered`、`metric`、`baseline`、`direction`、`review_date`、`revoke_if`、`status=active`、`deferred=0`、`undo`，`shared_metric_with` 可省默认 `[]`），缺任一必填项不登记、不执行；复查决策写回 = 到期项追加同 id 新行（后写覆盖）：保留 → `status=kept`，撤掉 → `status=revoked`，延期 → `deferred=1` 并给新 `review_date`；存档本周 `.md` / `.json`；提交并推送采集仓库 `admin/`。
8. **执行**：批准的提案交框架正常开发流程。skill 不直接改框架代码。

硬约束：结论必须引用脚本输出的数字；永不自动执行；没勾选 = unknown。

## 6. 测试归属表

5 维总分均为 5–6（无外部依赖，临时目录造事件即可，幂等），全部自动化。

| # | 行为单元 | 总分 | 归属层级 | TDD | 备注 |
|---|---|---|---|---|---|
| T1 | ISO 周窗口：周日 23:59Z 归本周，周一 00:00Z 归下周；跨年周 | 5 | unit | Y | |
| T2 | 比率分母 < min-n 输出 null 且展示「样本不足」 | 5 | unit | Y | |
| T3 | 在试项判定：down/up 两方向的改善 / 未改善 / 持平（最优值、非最优值、gate_deny 三类） | 5 | unit | Y | |
| T4 | 共用指标的在试项判定为「不可单独归因」 | 5 | unit | Y | |
| T5 | `deferred=1` 到期只给保留 / 撤掉 | 5 | unit | Y | |
| T6 | 台账坏行（未知键、缺字段、deferred>1）进第 6 节且不中断 | 5 | unit | Y | |
| T7 | 疑似误拦：deny 后 30 分钟内同 user+task 有 bypass 才计入；超时或跨 task 不计 | 6 | unit | Y | |
| T8 | 经验卡低效 / 闲置候选的阈值边界 | 5 | unit | Y | |
| T9 | 四段表缺数据写「无数据」 | 5 | unit | N | |
| T10 | `admin/` 下文件不被 `telemetry_aggregate` 当事件读取 | 5 | unit | N | 回归保护 |
| T11 | 临时采集仓库：造两周事件 + 台账 → 脚本 md/json 各节内容正确，退出码 0；仓库不存在退出码 2 | 6 | integration | N | |
| T12 | 适配器安装 / 卸载：临时 HOME `--adapters team-admin` 装上 skill，doctor 正常，卸载无残留 | 6 | integration | N | 走现有安装器测试模式 |
| T13 | skill 文本：含减法先行、批量确认、未勾选=unknown、不自动执行四条约束 | 5 | contract | N | 静态断言 SKILL.md 关键约束，防止被改丢 |

skill 的实际对话效果（提案质量）属人工验证，不进自动化。

## 7. 验收

1. pytest 全绿、0 skip；sanitize / template / context_budget 三个检查退出码 0。
2. 临时目录端到端：安装 team-admin 适配器 → 造两周事件与台账 → 跑脚本 → 各节与手算一致 → 卸载无残留。
3. 不写真实 `~/.claude`；源码里不出现真实采集地址、个人路径。
4. `docs/team-telemetry-admin.md` 增加「每周复盘」一节，指向本文。

## 8. 风险与决策点

| 类别 | 项 | 当前处理 |
|---|---|---|
| 已决定 | 节奏每周，采集上传仍每日 | 用户已确认 |
| 已决定 | 本期不新增采集 | §1 |
| 已决定 | skill 放独立适配器 `team-admin`（只管理员安装）还是放 `core/skills`（全员都装） | 默认独立适配器：成员用不到，避免误触发 |
| 已决定 | 管理员数据放采集仓库 `admin/`（成员可读）还是单独私有仓库 | 默认 `admin/`：零新增基础设施；台账只含机制名和指标，不含成员信息 |
| 暂按默认 | 阈值：`MIN_N`=20、误拦 ≥5 次且占比 ≥0.5、经验卡 recall ≥5 且比率 ≥0.5、闲置 4 周 | 写成脚本参数，跑几周后据数据调 |
| 风险 | 团队初期数据量小，多数比率「样本不足」 | 设计上允许；只报数不判定 |
| 风险 | 误拦信号是相关性而非因果 | 报告文字明确标为「疑似」，只作减法候选 |

## 8.1 已知局限

- `first_green_index_median` 只看本周窗口，跨周任务的首次绿灯会偏向 1。
- `bypass_*`、`gate_deny:*` 为绝对次数，未按活跃度归一，活跃度下降会被误判为改善。

## 9. 第二期（不在本期）

需要新增采集，均为成员本机归类、只上传标签或计数：
- 返工原因分类 A–G（需求口径 / 影响点后知 / 环境工具数据 / 代码缺陷 / 审查打回 / 测试写错 / 其他）。
- skill 使用：名称（白名单外转哈希）与是否被纠正，用于闲置与误触发判断。
- 提问次数：衡量「少问用户」。
- 指标越过阈值时的当日提醒。
