# SDD Reference Analysis v1.1

本文件记录公开 SDD / Agent Skill / 研发一体化组件的参考价值评估。用途是帮助 `ai-coding-java` 选择可吸收的机制，同时避免默认流程变重。

`ai-coding-java` 的目标不是复刻完整 SDD 工具链，而是让企业级 Java 需求到交付更快、更准、更可验证。

## 评估原则

1. 优先吸收能减少返工的机制。
2. 默认流程必须短，小任务不能被阶段文档阻塞。
3. 高风险任务可以启用更完整的需求、领域类型模型、设计、架构评审、计划、测试和交接产物。
4. 只把稳定方法沉淀为模板或检查项，不绑定外部平台或特定 CLI。
5. 不引入会强制中断个人开发节奏的长门禁。

## 参考项目

| 项目 | 当前热度 | 维护信号 | 结论 |
|---|---:|---|---|
| GitHub Spec Kit | 约 124k stars / 11k forks | 2026-07 仍有更新 | 重点参考方法论和流程节点 |
| SDD Agent Skill Implicit Deny Edition | 0 stars / 0 forks | 2026-04 有更新 | 只参考严格门禁思想 |
| Open Spec Skill | 0 stars / 0 forks | 2026-06 有更新 | 只参考阶段交接格式 |

热度数据用于判断社区验证程度，不作为内容正确性的唯一依据。高 star 项目也不能整体照搬，低 star 项目也可以抽取局部有用设计。

## 可吸收机制

### 需求 Checklist

来源：GitHub Spec Kit 的需求澄清和 checklist 思路。

适合落地为：

1. 复杂需求的 `requirements-checklist` 模板。
2. 验收标准缺失、非目标不清、数据边界不清时的提示项。
3. Review 前的轻量自检，而不是默认阻塞门禁。

收益：

1. 提前暴露需求歧义。
2. 降低 AI 直接编码导致的返工。
3. 让测试用例能从验收标准自然派生。

流程影响：

1. 小任务不启用，不增加步骤。
2. 中高风险需求启用，通常增加一次需求核对。
3. 不要求所有 checklist 项都完美填写；未确认项可进入 `Open Questions` 或 `Assumptions`。

结论：值得落地，作为可选模板和复杂任务推荐项。

### 跨产物一致性分析

来源：GitHub Spec Kit 的 analyze / converge 思路。

适合落地为：

1. 需求、领域类型模型、设计、架构评审、实施计划、测试、发布影响之间的一致性检查说明。
2. 后续可选脚本，检查关键字段是否缺失，例如验收标准、验证命令、回滚方式。
3. Review 阶段的只读检查，不直接修改业务代码。

收益：

1. 防止需求写了但测试没覆盖。
2. 防止设计提到的表、接口、配置没有出现在发布影响里。
3. 防止交付报告只写结果，不写证据。

流程影响：

1. 默认不强制执行。
2. 复杂需求、发布前、重构前建议执行。
3. 只读分析不会阻塞开发；发现 P0/P1 风险时再转为必须处理。

结论：值得落地，先写入文档口径，后续再考虑脚本化。

### 阶段交接格式

来源：Open Spec Skill 的 stage result / handoff 思路。

适合落地为：

1. `.ai-coding-java/artifacts/<work-id>/handoff.md` 模板。
2. 长任务、跨会话、跨 Agent、提交前复盘时使用。
3. 记录当前阶段、已完成、未验证、阻塞项和下一步。

收益：

1. 减少跨会话丢上下文。
2. 让 Review 和继续开发能快速接上。
3. 比完整项目管理流程更轻。

流程影响：

1. 单次小改不启用。
2. 超过一个工作会话、涉及多模块或需要提测时启用。
3. 只记录事实和证据，不写长篇过程日志。

结论：值得落地，作为轻量交接模板。

### 严格门禁分级

来源：Implicit Deny 类 SDD Skill 的严格策略。

适合落地为：

1. 映射到当前 P0/P1/P2 Review 分级。
2. P0 明确阻塞，例如代码逻辑文件中的明文密钥、跨学校数据串读、事务不回滚、未授权数据修改；配置文件中的明文配置不属于此项。
3. P1/P2 给出风险和建议，不默认阻塞个人开发。

收益：

1. 高风险问题不被“先跑起来”掩盖。
2. 让安全、数据隔离、事务一致性有明确处理优先级。
3. 适合企业 Java 场景。

流程影响：

1. 不采用全量 implicit deny。
2. 只对 P0 做硬阻塞。
3. P1/P2 保持建议和证据化交付，避免流程过重。

结论：部分吸收，不能整体照搬。

## 不建议吸收的内容

1. 不引入完整 Spec Kit CLI，避免目标项目初始化后依赖外部命令体系。
2. 不要求所有需求都走完整五阶段文档，小任务继续按 `docs/rule-index.md` 快速路由。
3. 不引入单一 `shared-state.yaml` 作为全局状态门禁，避免状态文件变成新的维护负担。
4. 不把开源项目的术语和命令直接写成默认入口，避免 Codex、Claude Code 和本组件职责混淆。
5. 不把所有 checklist 变成阻塞项，只让 P0 级风险阻塞。

## 落地状态和下一步

已落地：

1. `templates/requirements-checklist-template.md`：只在复杂需求或高风险改动时使用。
2. `templates/handoff-template.md`：用于跨会话、跨 Agent 或提测前交接。
3. `docs/rd-integrated-workflow.md` 已增加一致性分析口径：需求、领域类型模型、设计、架构评审、实施计划、测试、发布影响必须能互相追溯。

下一轮可选：

1. 增加只读一致性检查脚本，检查关键产物字段是否缺失。
2. 保持脚本为手动或复杂任务推荐项，不作为当前默认安装门禁。

## 对整体流程的影响

默认流程保持不变：

```text
Intake -> Scope -> Context -> Impact -> Implement -> Verify -> Review -> Report
```

复杂需求可扩展为：

```text
Requirement Grilling -> Requirement -> Domain/Type -> Design -> Architecture Review -> Plan/Test -> Implement -> Verify/Review -> Release -> Knowledge
```

新需求、完整模块或不清晰行为变更先通过全局 `$grilling` 做需求拷问；新增模板只在复杂度、风险或协作成本足够高时启用。小需求不要求生成过程产物，避免把 `ai-coding-java` 变成冗长流程框架。

## 参考链接

1. GitHub Spec Kit: <https://github.com/github/spec-kit>
2. Spec Kit Agentic SDD: <https://github.com/github/spec-kit/blob/main/docs/reference/agentic-sdd.md>
3. Spec Kit Agent Context Extension: <https://github.com/github/spec-kit/blob/main/extensions/agent-context/README.md>
4. SDD Agent Skill Implicit Deny Edition: <https://github.com/devedale/Spec-Driven-Development_Agent-Skill-Implicit-Deny-Edition>
5. Open Spec Skill: <https://github.com/pluto-arch/open-spec-skill/blob/main/open-spec/SKILL.md>
