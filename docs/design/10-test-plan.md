# 测试计划

## 工程骨架自动化

健康检查、Contract 创建/JSON 序列化、naive datetime 拒绝、风险与策略枚举、状态机合法/非法流转、Mock ToolCallRequest 调度、AuditEvent 顺序、YAML 配置解析和前端占位渲染。

## Phase 1：Runtime 基础闭环自动化

Contract v0.2 字段与共享 Fixture、可信 ToolSpec 多权限检查、AuditRecorder 并发序号与递归脱敏、FAST_EXECUTE 单次调用、BLOCK 零执行、SANDBOX Checkpoint/Pending/DeepCheck/Commit/Rollback、REQUEST_APPROVAL 等待/恢复/拒绝/过期、Agent-Scheduler 接线、所有跨模块关联错误，以及危险 Fixture 的 Mock 风险语义。

## 后续安全验收

- 正常任务：低风险不触发深检，审计完整。
- 危险命令：CRITICAL/BLOCK，不产生真实副作用。
- 敏感文件：`.env` 读取进入审批或阻断，日志不泄密。
- 间接提示注入：外部内容诱导的后续工具请求仍被分类。
- Memory 投毒：只写 pending，失败后不进入 trusted。
- 状态污染：检查失败后文件、memory 和结果不可见。
- 超时/取消：终止执行并清理/回滚。
- Rollback 失败：进入 FAILED、告警且不继续任务。

所有 CI 测试使用 Mock，不调用真实外部 API 或 LLM。

## Phase 2–5 集成验证

- 模式装配：`offline` 不创建运行时目录；`rules-only` 使用确定性规则和 SQLite 状态；
  `live-agent` 使用受控文件 Handler、Checkpoint、DeepCheck、Commit/Rollback。
- Agent：DeepSeek 兼容 HTTP 请求使用 MockTransport 验证；模型缺失、失败或畸形 JSON 时，
  AgentRuntime 必须 FAILED 且 Scheduler 零调用。
- API/SSE：创建任务触发 AgentRunner；SSE 在订阅和历史回放重叠时无漏事件、按 sequence
  去重；前端在重连时按 event_id 去重。
- 持久化并发：同一 task 的 Audit sequence 通过数据库唯一约束和原子分配保持连续；同一
  Approval 决策竞争返回领域冲突而非原始 IntegrityError。
- 性能 smoke：使用临时 SQLite 连续记录 100 个 durable Audit 事件并验证 1..100 序列；
  该检查只作为回归信号，不替代部署环境基准测试。
