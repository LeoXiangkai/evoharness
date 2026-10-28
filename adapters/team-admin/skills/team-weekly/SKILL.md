---
name: team-weekly
description: 团队管理员每周复盘：读采集仓库周报数字，减法先行提优化方案，一次批量确认后写回决策与在试台账。仅管理员使用。
---

# 团队周复盘

设计契约：`docs/team-weekly-design.md`（EvoHarness 源码 clone 内）。本 skill 只编排；数字全部来自脚本，不自己算。

## 流程

1. 让管理员在采集仓库 clone 里 `git pull`，再到 EvoHarness 源码 clone 运行：
   `python3 scripts/team_weekly.py --repo <采集仓库 clone> --format json` 与 `--format md`（可加 `--week YYYY-Www`）。
2. **减法先行**：至少提 1 项删除或合并；本期新增提案数不得超过删除数。来源是报告第 3、4 节。
3. **在试项复查**：到期项按报告判定给去留；未改善就撤并按 `undo` 还原；`deferred=1` 的不得再延期。
4. **落点分流**：每条新增提案先分类。固定模式可判定的（命令写法、路径、命名、可由脚本判断的状态）落成 hook / lint / pre-commit 检查并附脚本草稿；需要判断的才写进 rules 或经验卡。
5. **流程四段表**：点名最拖效率的一段，只给一项改进建议，落点同第 4 步。
6. **一次批量确认**：所有提案并入一次多选确认；决策类单选可标推荐，标注类多选不标推荐。
7. **写回**（采集仓库 `admin/`）：每条提案追加 `decisions.jsonl`（`{week, proposal_id, kind, decision, ref}`）；批准的新机制同时追加 `trials.jsonl`，指标、基线、复查日、撤掉条件、undo 必填，缺任一项不登记、不执行；存档本周 `weekly/<YYYY-Www>.md` 与 `.json`；提交并推送 `admin/`。
8. **执行**：批准的提案交框架正常开发流程（worktree、测试、合并）；本 skill 不直接改框架代码。

## 硬约束

- 结论必须引用报告里的数字；没有数据写「无数据」，样本不足只报数不判定。
- 未勾选 = `unknown`，不写 `rejected`。
- 永不自动执行任何改动，包括撤掉在试项。
- 疑似误拦只是相关性信号，措辞用「疑似」。
