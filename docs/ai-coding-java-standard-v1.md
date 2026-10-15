# ai-coding-java 企业级 Java Coding Agent 落地标准 v1.1

## 1. 文档目标

本文档定义企业 Java 项目引入 Coding Agent 后的标准落地方案。

目标不是让 AI 读更多文档，而是让 AI 在每次开发中做到：

1. 知道当前项目使用什么技术栈。
2. 知道哪些规则必须遵守。
3. 知道本次变更影响哪里。
4. 知道应该执行哪些验证。
5. 知道哪些问题必须阻断。
6. 知道哪些稳定知识需要沉淀。

本文档不统一定义具体业务规则。业务规则来自项目需求、领域模型、数据边界、接口契约、历史设计决策和负责人确认，应由项目级规则维护。

v1.1 定位：

1. 本文档是总纲，不要求 Agent 每次全量加载。
2. 技术栈不写死为唯一答案，而是在项目初始化时确认。
3. 模板组件本身在 `ai-coding-java` 专项项目中版本化；注入到业务项目的开发辅助模板可按项目策略不入库。
4. 默认自动安装轻量 Git `pre-commit` 和 `pre-push` 预检。
5. P0 规则用于阻断明显高风险交付；P1/P2 主要用于 Review 和交付报告，不把日常开发拖成长流程。
6. 需求测试和代码测试必须进入开发 workflow。清楚的功能目标不能降级成临时脚本、窄实现或后续再补。
7. 高风险业务变更必须按 `docs/tdd-policy.md` 声明 TDD 等级；核心规则、bugfix、库存、金额、权限、数据隔离和幂等变更按 L2/L3 保留测试优先或 RED/GREEN 证据。
8. 新需求、完整模块、二开改造和不清晰行为变更必须先通过全局 `$grilling` 完成需求拷问，再进入 `docs/design-first-policy.md` 的设计门；设计不完整时先补宏观/微观设计或模块/影响设计，不直接编码。

## 2. 规范分层

### 2.1 公司级规范

公司级规范约束所有 Java 项目的工程底线：

1. 技术栈基线
2. 分层架构约束
3. Java 编码规则
4. Spring Boot 使用规则
5. MyBatis / Mapper XML 规则
6. SQL 性能与安全规则
7. 事务规则
8. 安全与日志规则
9. 测试与交付验证规则
10. Agent Workflow 与 Review 规则

### 2.2 项目级规范

项目级规范不能被公司级规范替代。

项目级规范包含：

1. 业务规则
2. 领域对象
3. 状态流转
4. 权限边界
5. 数据隔离条件
6. 接口契约
7. 环境差异
8. 历史设计决策
9. 特殊发布要求

推荐维护位置：

```text
AGENTS.md
.omx/project-memory.json
.omx/notepad.md
project docs
ADR
```

## 3. Java 技术栈初始化基线

ai-coding-java 不强行迁移项目技术栈。

新项目初始化时，Agent 必须把技术栈作为确认项：

```text
请选择或输入项目技术栈：
1. 推荐存量企业 Java 基线：Java 8 + Spring Boot 2.x + Maven + MyBatis-Plus/MyBatis XML + MySQL + Redis
2. 推荐新项目现代基线：按企业架构当前标准填写
3. 自定义：由负责人输入 Java / Spring Boot / 构建工具 / ORM / DB / 缓存 / RPC / MQ / 前端栈
```

初始化结果必须落到项目级规则或项目画像中，例如：

```text
AGENTS.md
.omx/project-memory.json
docs/project-profile.md
```

当前常见企业存量 Java 项目建议基线：

```text
Java：Java 8
框架：Spring Boot 2.x
构建：Maven
分层：Controller -> Service -> Mapper -> XML
ORM：MyBatis-Plus + MyBatis XML Mapper
数据库：MySQL
缓存：Redis
```

Coding Agent 必须优先适配项目已确认技术栈，不得主动引入与现有项目不一致的 JDK、Spring Boot 大版本、ORM、响应式框架、大规模重分层或新依赖，除非需求明确要求并经过架构确认。

## 4. 推荐目录结构

ai-coding-java 专项项目推荐目录：

```text
ai-coding-java/
  README.md
  TOOL.md
  USAGE.md
  docs/
    ai-coding-java-standard-v1.md
    rule-index.md
    verification-matrix.md
    project-onboarding-template.md
    review-output-template.md
  rules/
    java8-springboot2-mybatis.md
    sql-rule.md
    transaction-rule.md
    security-logging-rule.md
    delivery-rule.md
    review-level.md
  workflow/
    agent-workflow.md
  templates/
    coding-agent-template.md
    ai-review-template.md
    project-business-rule-template.md
    delivery-report-template.md
    adr-template.md
```

说明：

- `ai-coding-java/` 是模板组件源码项目，应该入 Git，按版本演进。
- 注入业务项目的 `.ai-coding-java/` 是 AI 开发强制辅助模板，不等同 CI 工具目录。
- 注入业务项目的模板是否入 Git 由项目策略决定；默认可作为本地开发辅助不入业务代码仓。
- 项目规则仍以最近的 `AGENTS.md` 为最高执行入口。
- 模板更新必须保持轻量，新增能力应先进入独立文档、模板或可配置脚本。

## 5. 规则等级

所有规则必须分级，便于 Agent 判断是否阻断。

```text
P0 阻断级：
违反后禁止交付，必须立即修复。

P1 必修级：
原则上禁止交付，除非有明确豁免说明和负责人确认。

P2 建议级：
不阻断当前交付，但需要在 Review 或交付报告中记录。
```

典型规则：

```text
P0：
- 在代码逻辑文件中硬编码明文生产密钥（配置文件允许明文配置）
- SQL 注入风险
- update/delete 无业务 where
- 多表写无事务
- 跨租户/跨组织/跨学校/跨年度数据串读
- 生产破坏性 DDL 未确认

P1：
- 缺少核心测试
- Mapper SQL 性能风险
- 事务缺少 rollbackFor
- Controller 写复杂业务逻辑
- 接口字段变更未验证

P2：
- 命名不佳
- 轻微重复代码
- 日志上下文不足
- 方法偏长但风险可控
```

## 6. 分层架构规范

### 6.1 Controller

Controller 只允许负责：

1. 参数接收
2. 参数校验
3. 鉴权上下文读取
4. 调用 Service
5. 返回统一响应

Controller 禁止：

1. 编写业务规则
2. 直接访问 Mapper
3. 编写 SQL
4. 开启事务
5. 写复杂对象组装逻辑
6. 调用多个 Mapper 拼业务结果

### 6.2 Service

Service 负责：

1. 业务流程编排
2. 事务边界
3. 调用 Mapper
4. 调用外部服务适配器
5. 核心业务规则落地
6. 幂等与状态校验

Service 要求：

1. 多表写必须使用 `@Transactional(rollbackFor = Exception.class)`。
2. 事务方法必须通过 Spring Bean 调用。
3. 复杂业务逻辑应拆成私有方法、领域辅助类或既有本地组件。
4. 不得把 Controller 展示逻辑扩散到多个 Service。

### 6.3 Mapper / XML

Mapper 负责：

1. 单表查询
2. 必要的多表查询
3. 插入、更新、删除
4. 与数据库结构直接相关的数据访问

Mapper 禁止：

1. 承载复杂业务分支
2. 使用外部输入直接拼接 `${}`
3. 返回无约束的 `Map<String, Object>`
4. 编写无分页的大列表查询
5. 编写无 where 的 update/delete

### 6.4 VO / DTO / Entity

要求：

1. 入参和出参分离。
2. Entity 对应数据库结构。
3. VO 面向接口返回。
4. DTO 面向内部传输。
5. 字段变更必须同步接口验证。
6. 不允许为了省事直接把 Entity 暴露给外部接口。

## 7. Java 编码规范

### 7.1 基础要求

1. 遵守 Java 8 语法。
2. 不使用项目当前 JDK 不支持的 API。
3. 不新增未经确认的第三方依赖。
4. 优先复用已有工具类、枚举、异常、响应结构。
5. 小改动保持小 diff，不做无关重构。

### 7.2 命名

要求：

1. 类名表达职责。
2. 方法名表达动作和业务含义。
3. 变量名表达领域语义。
4. 常量使用统一命名。
5. 禁止 `doSomething`、`processData`、`handle` 这类无业务含义命名。

### 7.3 异常

要求：

1. 使用项目统一业务异常。
2. 异常信息要能定位业务原因。
3. 不吞异常。
4. 不无脑 `catch Exception`。
5. 不把底层敏感异常直接暴露给前端。

### 7.4 日志

要求：

1. 禁止 `System.out`。
2. 使用统一日志框架。
3. 关键业务操作记录 INFO。
4. 可恢复风险记录 WARN。
5. 系统异常记录 ERROR。
6. 禁止打印密码、token、密钥、身份证等敏感信息。

## 8. SQL 规范

### 8.1 通用 P0 规则

1. 禁止未经白名单映射的外部输入进入 MyBatis `${}`。
2. 禁止无 where 的 update/delete。
3. 禁止遗漏项目要求的数据隔离条件。
4. 禁止生产环境直接执行未评审 DDL。
5. 禁止明文敏感数据落库。

### 8.2 通用 P1 规则

1. 禁止 `select *`，应明确字段。
2. 列表查询必须分页或限制数量。
3. 高频查询必须考虑索引。
4. 复杂 SQL 必须说明查询场景。
5. 新增 SQL 必须考虑单表或多表联查性能。

### 8.3 单表查询性能规范

单表查询必须关注：

1. 查询字段是否明确。
2. where 条件是否可命中索引。
3. 是否存在低选择性条件。
4. 是否存在函数包裹索引字段。
5. 是否存在隐式类型转换。
6. 是否存在无分页大查询。
7. order by 是否可利用索引。

禁止示例：

```sql
select * from user;
select id from user where date(create_time) = '2026-08-01';
select id from user order by create_time limit 100000, 20;
```

推荐示例：

```sql
select id, name, status
from user
where status = 1
order by id
limit 20;
```

### 8.4 多表联查性能规范

多表联查必须说明：

1. 主表是什么。
2. 驱动表是什么。
3. join 字段是什么。
4. join 字段是否有索引。
5. 数据量级是多少。
6. 过滤条件是在 join 前还是 join 后生效。
7. 是否可能产生笛卡尔积。
8. 是否需要拆成两段查询。

P0 禁止：

1. 无关联条件 join。
2. 大表无边界 join。
3. 多表联查遗漏项目级数据隔离条件。
4. 未经白名单映射的用户输入拼接 order by / group by / where。

P1 要求：

1. 大表 join 字段必须有索引。
2. 多表查询必须明确字段。
3. 复杂查询必须提供 explain 或等价真实数据验证。
4. 慢 SQL 风险必须拆分或加索引。
5. 一对多 join 后分页必须确认结果语义。

### 8.5 写操作规范

要求：

1. update/delete 必须有 where。
2. 批量更新必须分批。
3. 批量写入必须考虑幂等。
4. 状态更新必须带当前状态条件。
5. 重要写操作需要记录操作人、时间、来源。

推荐示例：

```sql
update order_info
set status = 2,
    updated_time = now()
where id = #{id}
  and status = 1;
```

### 8.6 MyBatis XML 规范

要求：

1. 普通参数使用 `#{}`。
2. `${}` 只能用于经过白名单转换的列名、排序字段等不可参数化位置。
3. 使用 `${}` 必须有注释说明来源安全。
4. 动态 SQL 必须保证 where 条件完整。
5. foreach 批量参数必须控制数量。

## 9. 索引规范

新增索引必须说明：

1. 查询场景
2. 对应 SQL
3. 表数据量
4. 字段选择性
5. explain 结果
6. 是否已有相似索引
7. 写入成本影响

联合索引建议顺序：

```text
等值过滤字段
范围字段
排序字段
分组字段
```

禁止：

1. 重复索引。
2. 低选择性字段单独建索引。
3. 未说明查询场景的索引。
4. 为一次性脚本随意加索引。

## 10. 事务规范

### 10.1 P0 规则

1. 多表写必须加事务。
2. 事务必须放在 Service 层。
3. 禁止 Controller 开事务。
4. 多表写使用 `@Transactional(rollbackFor = Exception.class)`。
5. 禁止依赖同类自调用触发事务。
6. 事务方法必须通过 Spring Bean 调用。

### 10.2 P1 规则

1. 事务内避免 HTTP 调用。
2. 事务内避免 MQ 发送。
3. 事务内避免文件上传。
4. 事务内避免大循环。
5. 批量任务必须考虑分批提交。
6. 失败后必须有幂等、补偿或重试边界。

### 10.3 异步、定时与多节点风险

以下场景必须额外判断调用路径和幂等边界：

1. `@Async`、`@Transactional` 等代理敏感注解必须通过 Spring Bean 调用。
2. 定时任务在多节点部署时必须确认是否单节点执行、是否有分布式锁、是否可重复执行。
3. MQ、外部 HTTP、文件写入应避免和数据库事务强耦合；必要时使用事务后置事件、 outbox、补偿任务或明确的幂等键。
4. 缓存更新必须说明与数据库写入的顺序、一致性窗口和失败处理。
5. 批量任务必须说明分片、断点续跑、重复提交和部分失败策略。

### 10.4 推荐写法

```java
@Service
public class OrderService {

    @Transactional(rollbackFor = Exception.class)
    public void payOrder(Long orderId) {
        // 1. 校验状态
        // 2. 更新订单
        // 3. 写流水
        // 4. 写操作日志
    }
}
```

### 10.5 禁止写法

```java
@RestController
public class OrderController {

    @Transactional
    @PostMapping("/pay")
    public void pay() {
        // 禁止 Controller 开事务
    }
}
```

```java
public void outer() {
    this.inner();
}

@Transactional
public void inner() {
    // 同类自调用事务不会按预期生效
}
```

## 11. 测试规范

新增功能必须覆盖：

1. 正常流程
2. 异常流程
3. 边界条件
4. 权限或数据隔离
5. 状态流转
6. 幂等场景

测试要求：

1. 测试断言业务结果，不只断言 mock 调用。
2. 核心 Service 必须有测试。
3. Mapper SQL 变更需要真实数据或集成验证。
4. Controller / VO / Mapper 变更需要启动服务并 curl 验证。
5. 修 bug 应优先补回归测试。
6. 复杂需求应先把验收标准映射到需求测试、单元测试、集成测试、API/SQL 验证和人工验收。
7. 高风险行为变更应先选择 TDD 等级；L3 必须先有 RED 失败证据，再最小实现到 GREEN。

## 12. Agent Workflow 设计

### 12.1 总流程

```text
Intake
  -> Scope
  -> Context Load
  -> Impact Analysis
  -> Implement
  -> Verify
  -> Review
  -> Fix Loop
  -> Memory Candidate
```

### 12.2 Intake

识别需求类型：

```text
feature
bugfix
refactor
sql-change
ddl-change
config-change
release
review
```

### 12.3 Scope

必须输出：

1. 本次允许修改的模块。
2. 本次禁止修改的模块。
3. 是否涉及数据库。
4. 是否涉及接口兼容。
5. 是否涉及多表写。
6. 是否涉及定时任务、异步、缓存、文件、外部系统。

### 12.4 Context Load

只加载与本次 diff 相关的规则，禁止全量读取导致上下文污染。

固定前置读取顺序：

```text
1. 最近的 AGENTS.md
2. 用户本次任务说明
3. 项目级当前任务记忆或项目画像
4. 与本次变更类型匹配的 ai-coding-java 专项规则
5. 相关代码、测试、配置和历史提交
```

示例：

```text
改 Controller：
读取 controller/service/API/security/delivery 相关规则

改 Service：
读取 service/transaction/test/delivery 相关规则

改 Mapper XML：
读取 mapper/sql/performance/index 相关规则

改 DDL：
读取 migration/data-security/release 相关规则
```

如果任务只涉及小范围 bugfix 或一行配置修改，允许只读取 `rule-index.md` 中命中的规则条目，不强制加载总纲全文。

### 12.5 Impact Analysis

必须判断：

1. 影响哪些接口。
2. 影响哪些表。
3. 影响哪些调用方。
4. 是否需要兼容旧数据。
5. 是否影响性能。
6. 是否影响权限或数据隔离。
7. 是否需要新增测试。
8. 是否需要发布说明。

### 12.6 Implement

要求：

1. 小步修改。
2. 遵守现有项目结构。
3. 不新增无关抽象。
4. 不修改无关文件。
5. 不引入未经确认的依赖。
6. 不在代码逻辑文件中硬编码明文密码、token、密钥；允许在配置文件中明文配置。
7. 不把清楚的功能目标降级成临时脚本、局部兜底或后续再补；应完成对应的设计、实现、测试和验证入口。

### 12.7 Verify

根据变更类型触发验证矩阵：

```text
Java 代码：
compile + focused test

Controller / VO：
compile + local start + curl

Mapper XML：
compile + SQL/接口/真实数据验证 + explain when needed

事务：
检查 @Transactional、rollbackFor、Spring Bean 调用路径

DDL：
dev/test 执行 + post-state 查询
```

验证降级规则：

1. 能执行的验证必须执行，不能只写计划。
2. 因环境、账号、数据、依赖不可用导致无法执行时，必须报告 `Not-tested`。
3. 降级验证必须说明缺失证据、已执行的替代验证和剩余风险。
4. Controller / VO / Mapper SQL 变更无法本地启动时，至少执行编译、相关测试、SQL 静态检查，并说明未 curl 的原因。
5. 不允许把“未验证”写成“已验证”。

### 12.8 Review

只审本次 diff，不做全仓泛审。

Review 输出：

```text
P0 阻断问题
P1 必修问题
P2 建议问题
验证缺口
是否允许交付
```

### 12.9 Fix Loop

规则：

1. P0 必须修复。
2. P1 原则上必须修复。
3. P2 不阻塞当前任务。
4. 修复后重新执行 Verify 和 Review。
5. 不把当前变更的 P0/P1 问题堆积为后续任务。

### 12.10 Memory Candidate

只沉淀稳定知识。

应该沉淀：

1. 新业务规则
2. 新接口契约
3. 新表或字段语义
4. Bug 根因与修复方式
5. 架构决策
6. 验证方式

不应该沉淀：

1. 临时日志
2. 过程性猜测
3. 一次性命令输出
4. 已失效排查路径

## 13. Agent 角色设计

角色是职责划分，不是强制多 Agent 编排。

默认由单 Agent 完成本次任务。只有当搜索、实现、Review、验证可以独立并行且收益明显时，才拆分子 Agent。子 Agent 必须有边界、输入、输出和验证口径，不得无边界泛审。

### 13.1 Planner Agent

职责：

1. 需求拆解
2. 风险判断
3. 影响分析
4. 输出实现计划

禁止：

1. 修改代码
2. 修改 SQL
3. 执行发布动作

### 13.2 Developer Agent

职责：

1. 读取相关规范
2. 修改代码
3. 编写测试
4. 保持小 diff
5. 输出验证证据

必须：

1. 遵守 AGENTS.md。
2. 遵守项目级规则。
3. 遵守公司级技术规则。
4. 不碰无关文件。

### 13.3 Reviewer Agent

职责：

1. 审查本次 diff。
2. 检查 P0/P1/P2 问题。
3. 输出是否允许交付。
4. 给出明确修改建议。

禁止：

1. 泛泛评价。
2. 只输出 LGTM。
3. 忽略验证缺口。

### 13.4 Tester Agent

职责：

1. 执行编译。
2. 执行测试。
3. 启动服务。
4. curl 接口。
5. 验证 SQL 或 DDL。

输出：

```text
执行命令：
执行结果：
失败日志：
验证结论：
未验证项：
```

### 13.5 Memory Agent

职责：

1. 从已完成任务中抽取稳定知识。
2. 更新项目知识库。
3. 避免沉淀过程噪音。
4. 标注来源、适用范围和失效条件。

## 14. 自动 Review 落地方案

自动 Review 分两层。

### 14.1 确定性检查

适合工具化或 CI 化：

1. 编译
2. 单测
3. SQL 静态扫描
4. 敏感信息扫描
5. 事务注解扫描
6. 依赖变更扫描
7. DDL 危险操作扫描

### 14.2 AI 语义 Review

AI Review 负责判断工具难以判断的问题：

1. 是否破坏分层。
2. 是否绕过已有业务规则。
3. 是否遗漏事务边界。
4. 是否存在跨租户/组织/学校/年度风险。
5. SQL 是否有真实性能风险。
6. 是否缺少关键边界测试。
7. 是否引入不可回滚的数据变更。
8. 是否违反已有架构决策。

### 14.3 Review 输出格式

```text
P0 阻断问题：
P1 必修问题：
P2 建议问题：
验证缺口：
剩余风险：
是否允许交付：
```

### 14.4 阻断策略

```text
P0：禁止交付。
P1：原则上禁止交付，除非负责人确认豁免。
P2：记录，不阻断。
```

## 15. CI / Pipeline 集成建议

本文档不强制项目必须内置检测脚本，但建议企业平台逐步接入：

```text
compile
unit-test
sql-scan
transaction-scan
secret-scan
dependency-scan
ai-review
```

推荐 PR 流水线：

```text
checkout
setup java 8
mvn -DskipTests compile
mvn test
static gates
AI review
human approval
```

项目未完成存量治理前，建议先做“只拦新增变更”，不要让历史债务阻塞所有日常需求。

ai-coding-java 模板默认会安装轻量 Git hooks。`pre-commit` 对确定性 P0 风险阻断提交；`pre-push` 在 warn 模式检查个人开发分支和验证命令配置，在 strict 模式执行 build/test。

## 16. 企业知识沉淀

### 16.1 企业知识库

应沉淀：

1. 技术栈基线
2. 架构边界
3. 高频 Bug 根因
4. SQL 性能经验
5. 事务事故经验
6. 接口兼容规则
7. 发布检查项
8. 安全红线

### 16.2 项目知识库

应沉淀：

1. 业务规则
2. 数据隔离条件
3. 状态机
4. 表字段语义
5. 特殊接口契约
6. 环境差异
7. 历史架构决策

### 16.3 知识模板

```text
标题：
类型：
适用范围：
事实：
来源：
代码路径：
验证方式：
失效条件：
最后验证时间：
```

### 16.4 沉淀入口

建议从以下场景沉淀：

1. PR 合并
2. Bug 修复
3. 测试缺陷
4. 线上事故
5. 数据修复
6. 架构评审
7. 发布回滚

## 17. 项目接入方式

每个项目至少提供：

```text
AGENTS.md
项目业务规则
项目数据边界
项目接口契约
项目环境差异
项目验证命令
```

AGENTS.md 必须包含：

1. 技术栈
2. 目录结构
3. 构建命令
4. 测试命令
5. 启动命令
6. 项目特殊约束
7. 验证要求
8. 记忆沉淀要求

项目初始化必须确认：

1. 项目类型：新项目 / 存量项目 / 维护项目。
2. 技术栈：从建议项选择或自定义输入。
3. 构建、测试、启动命令。
4. 数据库、缓存、外部系统和本地验证依赖。
5. 数据隔离边界，例如租户、组织、学校、年度。
6. 强制验证等级：轻量 / 标准 / 严格。
7. 模板注入策略：业务仓入库 / 本地辅助不入库。
8. P1 是否允许负责人豁免，以及豁免记录位置。

## 18. Codex 调用规范

### 18.1 新功能

```text
请实现 xxx 功能。

要求：
1. 先读取 AGENTS.md 和相关 ai-coding-java 规则。
2. 输出影响分析。
3. 小步实现。
4. 补充测试。
5. 执行验证。
6. 输出变更文件、验证结果、剩余风险。
```

### 18.2 Bug 修复

```text
请修复 xxx 问题。

要求：
1. 先定位根因。
2. 给出证据。
3. 补回归测试。
4. 修复代码。
5. 执行验证。
6. 输出根因、修复方式、验证结果。
```

### 18.3 SQL 修改

```text
请修改 xxx SQL。

要求：
1. 说明涉及表和查询场景。
2. 检查单表/多表性能。
3. 检查索引。
4. 避免 SQL 注入。
5. 必要时提供 explain 或真实数据验证。
```

### 18.4 事务修改

```text
请修改 xxx 写入流程。

要求：
1. 判断是否多表写。
2. 检查事务边界。
3. 使用 rollbackFor = Exception.class。
4. 避免同类自调用。
5. 检查幂等和失败补偿。
6. 补充测试。
```

## 19. 当前能力闭环

ai-coding-java 当前提供：

1. 目标项目初始化和 Codex / Claude Code 入口 marker。
2. `docs/rule-index.md` 轻量规则路由。
3. Java / SQL / 事务 / 安全 / 交付 / Review 规则。
4. 全局 `$grilling` 新需求前置拷问。
5. `docs/design-first-policy.md` 编码前设计门。
6. `docs/testing-workflow.md`、`docs/tdd-policy.md` 和 `docs/verification-matrix.md` 测试验证闭环。
7. 复杂需求按需产物：Requirement Grilling -> Requirement -> Domain/Type -> Design -> Architecture Review -> Plan/Test -> Implement -> Verify/Review -> Release -> Knowledge。
8. 轻量 Git hooks、目标项目 doctor、交付证据检查、静态 Review 和项目画像。

OPC 默认保持短流程；只有新需求、新项目、完整模块、二开改造、影响不清或高风险变更才启用 `$grilling`、完整设计和研发产物。

## 20. 总结

ai-coding-java 的核心闭环：

```text
规范
  ↓
Agent 执行
  ↓
验证
  ↓
Review
  ↓
问题回修
  ↓
知识沉淀
  ↓
反哺下一次开发
```

最终目标：

1. AI 不凭空设计。
2. AI 不绕过项目边界。
3. AI 不跳过验证。
4. AI 不把业务规则误做成公司统一规则。
5. AI 每次交付都有证据。
6. AI 每次稳定结论都能反哺后续开发。
