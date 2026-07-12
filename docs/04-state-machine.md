# 运行状态机

当前阶段为 **Phase 1：Runtime 基础闭环**。

终态为 `COMMITTED`、`ROLLED_BACK`、`BLOCKED`、`FAILED`、`CANCELLED`，终态禁止再次转移。

## 固定路径

- 低风险：`PLANNED → RISK_CLASSIFYING → READY → EXECUTING_FAST → COMMITTED`
- 中风险通过：`PLANNED → RISK_CLASSIFYING → CHECKPOINT_CREATING → EXECUTING_SANDBOX → SAFETY_CHECKING → COMMITTING → COMMITTED`
- 中风险失败：前半段相同，随后 `ROLLING_BACK → ROLLED_BACK`
- 高风险：`RISK_CLASSIFYING → WAITING_APPROVAL → READY|BLOCKED`；获批后仍进入 checkpoint/sandbox/check/commit。
- 策略阻断：`PLANNED → RISK_CLASSIFYING → BLOCKED`

请求、可信 ToolSpec 或跨模块关联校验失败进入 FAILED；任何失败都不能降级执行。

当前 Mock 编排按依赖顺序执行：Checkpoint → Pending execution → DeepCheck → Commit/Rollback。DeepCheck 依赖执行结果，因此不伪造并行。提交必须同时满足 PENDING_COMMIT、关联一致和检查通过。

状态合法性校验不替代调度前置条件：没有 RiskVerdict 不得执行，BLOCK 不得执行，中风险写操作没有 checkpoint 不得执行，没有检查通过不得 commit，同一 `request_id` 不得重复产生副作用。

## COMMITTED 语义

- FAST_EXECUTE：LOW 操作直接执行，不进入 Pending，也不调用真实 CommitGate；执行成功后进入 COMMITTED，表示结果已经成为可信结果。
- SANDBOX_CHECK：MEDIUM 结果必须先进入 Pending，通过安全检查并由 CommitGate 提交后才能进入 COMMITTED；当前只实现无副作用 Mock 编排，真实 Pending/Trusted 存储尚未实现。

Runtime 在状态机前使用共享请求执行 Registry 原子声明 `request_id`。相同语义的串行或并发重试复用第一次结果；相同 ID 的不同语义请求返回 `REQUEST_ID_CONFLICT`，不进入执行状态。

WAITING_APPROVAL 是可恢复中间状态：重复 schedule 返回同一等待结果；批准、拒绝或过期后只有一个 resume 所有者能把 Registry 原子推进到最终结果。
