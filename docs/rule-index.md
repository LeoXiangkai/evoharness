# ai-coding-java Rule Index v1.1

本文件是项目档案层的轻量入口；根 `rules/` 保留 Java 8 / Spring Boot 2 规则。全局 core/packs/adapters 入口见 [README](../README.md)，安装行为以 [global-install-design](global-install-design.md) 为准。注入后这些链接指向项目档案副本；全局组件源与安装命令应从组件仓库使用。

本文件是轻量入口。Agent 先读最近的 `AGENTS.md`，再按本索引命中专项规则；除非任务复杂，不需要全量读取总纲。

## 规则等级

```text
P0：阻断交付，必须修复。
P1：原则上必须修复；如豁免，需负责人和原因。
P2：记录建议，不阻断。
```

## 通用 P0

| ID | 规则 | 触发 |
|---|---|---|
| P0-SEC-001 | 禁止在代码逻辑文件中硬编码密码、token、密钥、生产凭据；配置文件允许明文配置 | 代码逻辑文件（Java/Kotlin/Python/TypeScript/JavaScript/Vue/Go 等） |
| P0-SQL-001 | 禁止 SQL 注入风险 | Mapper XML、SQL 拼接、动态排序 |
| P0-SQL-002 | 禁止无业务 where 的 update/delete | SQL、Mapper、数据脚本 |
| P0-DATA-001 | 禁止遗漏项目数据隔离条件 | 租户、组织、学校、年度、区域等边界 |
| P0-TX-001 | 多表写必须有事务 | Service 写流程 |
| P0-TX-002 | 事务和异步注解必须通过 Spring Bean 代理调用 | `@Transactional`、`@Async` |
| P0-DDL-001 | 生产破坏性 DDL 必须明确确认 | DDL、数据清理、字段删除 |

## 通用 P1

| ID | 规则 | 触发 |
|---|---|---|
| P1-TEST-001 | 核心业务变更必须有业务断言测试 | Service、规则、bugfix |
| P1-TDD-001 | 高风险业务变更必须声明 TDD 等级，L3 必须保留 RED/GREEN 证据 | 核心规则、bugfix、库存、金额、权限、数据隔离、幂等 |
| P1-REQ-001 | 新需求、完整模块和不清晰行为变更开发前若已安装先执行 `$grilling` 需求拷问；否则直接问答确认范围与验收 | 新功能、完整模块、跨模块、二开行为改造、需求边界不清 |
| P1-DESIGN-001 | 新项目、完整模块和二开改造编码前必须完成设计门 | 宏观模块、微观模块、既有模块、影响范围、不改范围 |
| P1-API-001 | Controller / VO 字段变更必须验证接口契约 | API 入参、出参、兼容字段 |
| P1-SQL-001 | 新增或修改复杂 SQL 必须说明场景和性能风险 | join、分页、排序、聚合 |
| P1-IDEMP-001 | 批量、异步、外部回调必须说明幂等边界 | job、MQ、HTTP 回调、重试 |
| P1-DEP-001 | 禁止未确认新增依赖 | `pom.xml`、插件、SDK |
| P1-CACHE-001 | 写流程改缓存必须说明一致性窗口和失败处理 | Redis、本地缓存 |
| P1-IMPL-001 | 禁止用临时脚本、窄实现或兜底小改替代应有功能 | 需求实现、测试、组件能力扩展 |

## 通用 P2

| ID | 规则 | 触发 |
|---|---|---|
| P2-NAME-001 | 命名应表达领域含义 | 新增类、方法、变量 |
| P2-LOG-001 | 关键日志应包含定位上下文且不泄露敏感信息 | 业务流、异常流 |
| P2-DUP-001 | 轻微重复可记录，不阻断小 diff | 局部重复 |

## 变更类型到专项文件

| 变更类型 | 必读 |
|---|---|
| Controller / VO | `rules/java8-springboot2-mybatis.md`、`rules/delivery-rule.md`、`docs/verification-matrix.md` |
| Service 写流程 | `rules/transaction-rule.md`、`rules/java8-springboot2-mybatis.md`、`docs/verification-matrix.md` |
| Mapper XML / SQL | `rules/sql-rule.md`、`rules/delivery-rule.md`、`docs/verification-matrix.md` |
| DDL / 数据脚本 | `rules/sql-rule.md`、`rules/delivery-rule.md`、`docs/verification-matrix.md` |
| 安全 / 日志 / 配置 | `rules/security-logging-rule.md`、`rules/delivery-rule.md` |
| 新项目初始化 / 存量项目注入 | `docs/project-harness.md`、`docs/project-onboarding-template.md`、`docs/project-integration-guide.md`、`templates/project-business-rule-template.md` |
| 任务执行流程 | `workflow/agent-workflow.md` |
| 新需求拷问 / 编码前设计 / 新项目宏观设计 / 二开影响设计 | `workflow/agent-workflow.md`、`docs/design-first-policy.md`、`docs/rd-integrated-workflow.md`、`templates/domain-type-model-template.md`、`templates/design-brief-template.md`、`templates/architecture-review-template.md`、`templates/implementation-plan-template.md` |
| 需求测试 / 代码测试 / 测试计划 / TDD 分级 | `docs/testing-workflow.md`、`docs/tdd-policy.md`、`docs/verification-matrix.md`、`templates/test-plan-template.md`、`templates/test-case-brief-template.md` |
| Git hook / commit-push 预检 | `docs/git-hooks-guide.md`、`docs/auto-review-guide.md` |
| 复杂需求 / 研发一体化 | `workflow/agent-workflow.md`、`docs/rd-integrated-workflow.md`、`docs/design-first-policy.md`、`docs/tdd-policy.md`、`templates/requirement-brief-template.md`、`templates/requirements-checklist-template.md`、`templates/domain-type-model-template.md`、`templates/design-brief-template.md`、`templates/architecture-review-template.md`、`templates/implementation-plan-template.md`、`templates/test-plan-template.md`、`templates/test-case-brief-template.md`、`templates/handoff-template.md` |
| 复杂需求产物一致性检查 | `docs/rd-integrated-workflow.md`、`templates/requirement-brief-template.md`、`templates/domain-type-model-template.md`、`templates/architecture-review-template.md`、`templates/implementation-plan-template.md`、`templates/design-brief-template.md`、`templates/test-plan-template.md`、`templates/test-case-brief-template.md`、`templates/release-impact-template.md`、`templates/handoff-template.md` |
| 目标漂移 / 反复返工 / 补丁震荡 | `workflow/agent-workflow.md`、`docs/design-first-policy.md`、`docs/rd-integrated-workflow.md`、`templates/requirement-brief-template.md`、`templates/design-brief-template.md`、`templates/adr-template.md` |
| 项目定位变化 / 业务目标重定向 | `docs/rd-integrated-workflow.md`、`docs/design-first-policy.md`、`docs/testing-workflow.md`、`docs/tdd-policy.md`、`templates/requirement-brief-template.md`、`templates/domain-type-model-template.md`、`templates/design-brief-template.md`、`templates/architecture-review-template.md`、`templates/implementation-plan-template.md`、`templates/test-plan-template.md`、`templates/handoff-template.md` |
| 类型系统探索 / 架构设计 / 架构评审 / 落地方案 | `docs/design-first-policy.md`、`docs/rd-integrated-workflow.md`、`templates/domain-type-model-template.md`、`templates/design-brief-template.md`、`templates/architecture-review-template.md`、`templates/implementation-plan-template.md`、`templates/test-plan-template.md` |
| 交付证据检查 | `templates/delivery-report-template.md`、`rules/delivery-rule.md`、`docs/verification-matrix.md` |
| 目标项目画像 / 代码地图 | `docs/project-harness.md`、`docs/project-integration-guide.md` |
| 全栈多仓协作 / TAP 通道 / 前后端 agent 对齐 | `docs/tap-fullstack-collaboration.md`、`templates/tap-state-template.md`、`templates/tap-sync-tap-pending-template.md` |
| Multica worktree / 分支约束 | `docs/runtime-skill-boundary.md`、`docs/git-policy.md`、`templates/multica-worktree-agent-template.md` |
| 组件结构 / 文件命名检查 | `docs/structure-and-naming.md`、`docs/project-harness.md` |
| 文档表达 / 外部经验转化检查 | `docs/documentation-tone-and-reuse.md`、`docs/sdd-reference-analysis.md` |
| 研发一体化规范演进 / 流程取舍 | `docs/sdd-reference-analysis.md`、`docs/rd-integrated-workflow.md`、`docs/design-first-policy.md`、`docs/tdd-policy.md`、`docs/runtime-skill-boundary.md` |
| 发布影响说明 | `templates/release-impact-template.md`、`rules/delivery-rule.md`、`docs/verification-matrix.md` |
| 运行时技能边界 | `docs/runtime-skill-boundary.md` |
| Review | `rules/review-level.md`、`templates/ai-review-template.md`、`docs/review-output-template.md` |

## 文件路由原则

1. 先读项目最近的 `AGENTS.md`。
2. 再读本文件。
3. 只打开上表命中的规则文件。
4. 复杂任务再打开总纲，不把总纲作为每次必读文件。
