# RA-Agent Shared Baseline Report

## 1. 当前阶段

Phase 1：共享 Runtime 基线已验证，进入成员 B、C、D 并行开发阶段。

## 2. 版本

- Python 3.11.9
- Node.js 24.14.0
- pnpm 10.12.4，以 `frontend/package.json` 的 `packageManager` 为准，并在 `frontend` 目录验证

## 3. 真实框架能力

- RuntimeScheduler 是唯一工具执行入口；FAST_EXECUTE 和 BLOCK 已跑通。
- `request_id` 支持串行、并发、等待审批和恢复幂等；公共时间统一为 UTC。
- 权限完整性、一致性及 Risk/Permission/Checkpoint/Execution/DeepCheck/Commit/Rollback/Approval 关联校验已启用。
- AuditRecorder 是唯一公共审计写入口；AgentRuntime 的所有工具请求只经过 RuntimeScheduler。
- ServiceContainer 提供统一依赖装配；ToolRegistry 分离管理 ToolSpec 与 ToolHandler。

## 4. Mock 能力

- SANDBOX_CHECK：Mock Checkpoint → PENDING_COMMIT → DeepCheck → Commit/Rollback。
- REQUEST_APPROVAL：内存 ApprovalService 支持等待、批准、拒绝、过期、原子一次性消费和独立恢复。
- Risk/Policy/Permission/DeepCheck、ToolExecutor、ToolHandler 和 Planner 均有无副作用 Mock。

Mock 不读写真实文件、不执行 Shell、不访问网络，不具备生产安全保证。

## 5. 尚未实现

- 真实风险规则、权限、深度检查、文件/网络/Memory/Shell 工具。
- 真实 Sandbox、Pending/Trusted 存储、Checkpoint、Commit 和 Rollback。
- 持久化审批、数据库审计、正式审批 API、SSE 和前端业务页面。
- LLM Planner、复杂 LangGraph 和实验系统。

## 6. Runtime 四类策略

| 策略 | 当前状态 |
|---|---|
| FAST_EXECUTE | 真实调度框架已跑通，当前执行器为无副作用 Mock |
| BLOCK | 已跑通，零 Executor 调用 |
| SANDBOX_CHECK | 完整 Mock 编排已跑通 |
| REQUEST_APPROVAL | 可恢复两阶段 Mock 编排已跑通 |

## 7. 成员接入接口

- 成员 B：`RiskClassifier`、`PolicyEngine`、`PermissionGate`、`DeepSafetyChecker`。
- 成员 C：`ToolExecutor`、`ToolHandler`、`CheckpointManager`、`CommitGate`、`RollbackManager`。
- 成员 D：`AuditRecorder`、`ApprovalService`、`RequestExecutionRegistry` 持久化实现，以及 API/SSE/数据库和前端消费。

三名成员只替换 ServiceContainer 中对应实现，不复制或修改 Runtime 主流程。

## 8. 测试结果

- Ruff format check、Ruff lint、Pyright、ESLint、TypeScript、Vitest、Vite build：通过。
- pytest：156 passed，覆盖率 88%。
- Checkpoint 成功建立后的执行、深检、提交及审计取消边界均验证会尝试 Rollback。

## 9. 统一检查

`python scripts/check.py`：本地 Windows 与当前清理前复验均返回码 0。

## 10. 已知限制

当前真实产品能力仍由第 5 节所列模块限制；Mock 只能证明调度、关联、幂等和替换边界，不能证明生产隔离或恢复能力。

## 11. 并行结论

成员 B、C、D 可以立即基于当前公共接口独立并行开发；当前未发现需要其修改 Runtime 主流程的阻塞项。

## 12. 共享基线提交哈希

`<待最终审核后填写>`
