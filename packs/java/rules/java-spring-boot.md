---
paths:
  - "**/*.java"
---

# Java / Spring Boot 通用规则

- 先确认项目的 Java、Spring Boot、持久化框架和模块边界，再沿用现有 Controller、Service、Mapper、DTO/VO、异常和权限约定。
- Controller 只负责参数校验、上下文读取、调用 Service 和统一响应；业务编排、幂等、状态校验、事务边界放在 Service。
- 不在 Java/Kotlin 等代码逻辑文件中硬编码明文密码、令牌、密钥或敏感个人数据；允许在配置文件中明文配置；外部输入进入 SQL、路径、命令、URL 或反序列化前必须校验和约束。
- 多表写入使用 `@Transactional(rollbackFor = Exception.class)` 或经验证的等价机制；事务内避免远程调用、MQ 发送、文件上传和无界循环。
- `@Async` 使用明确的线程池；异步线程不继承调用方事务、ThreadLocal 和 MDC，需要显式传递并建立自己的事务边界。
- 统一异常处理不得吞掉异常；记录诊断所需业务上下文时脱敏，使用项目已有的错误码、响应和数据隔离条件。

## Spring 隐式失效自审

提交前逐条检查：

- `@Transactional`、`@Async`、`@Cacheable` 的调用是否经过 Spring 容器代理；同类自调用会绕过代理。
- 注解是否放在 `private` 方法或 `final` 方法/类上；这些形态可能无法被代理。
- 受检异常是否需要 `rollbackFor = Exception.class`；默认回滚规则不会覆盖所有受检异常。
- 事务内是否 catch 后仅记录或返回，导致异常被吞掉、事务继续提交。
- `@Async` 是否使用自定义线程池，是否考虑队列、拒绝策略和上下文传递。
- 事务内是否调用远程服务或发送 MQ；失败、重试、提交时序和幂等是否有明确方案。
