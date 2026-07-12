# Phase 0 历史摘要

> 历史参考，不作为当前接口或实现规范。现行规范见 `README.md`、`docs/00-scope.md` 至 `docs/12-module-ownership.md`、`docs/adr/architecture-decisions.md` 和 `docs/SHARED-BASELINE-REPORT.md`。

## 当时背景

Phase 0 开始时，仓库只有原始任务提示词和竞赛策划书，没有可运行代码、依赖锁、测试或 CI。该阶段的目标是建立模块化单体骨架、冻结公共 Contract 和状态机，并让前后端工具链在无真实副作用的条件下可重复验证。

## 已完成事项

- 建立 FastAPI/Pydantic 后端、Vite/React 前端、YAML 配置、测试、CI 和跨平台检查脚本。
- 冻结 Python 3.11、Node.js 24.14.0、pnpm 10.12.4，以及 `uv.lock`、`pnpm-lock.yaml`。
- 建立唯一公共 Contract、UTC 时间类型、状态机和模块边界。
- 将审计公共写入口收敛为 `AuditRecorder.record`，并实现任务级序号与递归脱敏。
- 建立 `request_id` 串行/并发幂等和权限一致性校验。
- 在后续 Phase 1 基线上补齐 FAST_EXECUTE、BLOCK、SANDBOX_CHECK Mock、REQUEST_APPROVAL Mock、完整依赖装配、关联校验和 Agent 最小接线。

## 被替代的重要决策

- 早期 `AuditEventSink`/`emit` 设计已被单一 `AuditRecorder.record` API 取代。
- SANDBOX_CHECK 和 REQUEST_APPROVAL 从“未支持”升级为无副作用 Mock 编排；真实隔离、恢复和审批持久化仍未实现。
- ToolRegistry 从只保存 ToolSpec 升级为分离保存 ToolSpec 与 ToolHandler；Scheduler 仍只通过 ToolExecutor 执行。
- 固定审批字典不再冒充服务接入；正式 API/SSE/数据库集成由成员 D 实现。
- Phase 0 的详细提示词、设计稿、实施计划和冻结报告已由本摘要及现行规范取代。

## 当前规范位置

- 范围、架构、技术栈：`docs/00-scope.md`、`docs/01-architecture.md`、`docs/02-tech-stack.md`
- Contract、状态机、执行与审计：`docs/03-contracts.md`、`docs/04-state-machine.md`、`docs/07-execution-semantics.md`、`docs/09-event-schema.md`
- 安全、工具和 API：`docs/05-security-policy.md`、`docs/06-tool-specification.md`、`docs/08-api-specification.md`
- 测试、开发与所有权：`docs/10-test-plan.md`、`docs/11-development-guide.md`、`docs/12-module-ownership.md`
- 当前验收结论：`docs/SHARED-BASELINE-REPORT.md`
