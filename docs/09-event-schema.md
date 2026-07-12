# 审计事件 Schema

`AuditEvent` 字段为 `event_id/task_id/step_id/request_id/sequence_number/event_type/timestamp/actor/status/risk_level/decision/summary/details`。同一任务 `sequence_number` 严格单调增加；不同任务分别从 1 开始；`timestamp` 是 UTC aware datetime。

Scheduler 和业务模块只能调用统一 `AuditRecorder.record`，不得生成 `event_id`、`sequence_number` 或 `timestamp`。Recorder 负责构建与保存事件；公共 API 不再暴露旧 `emit`。当前 `InMemoryAuditRecorder` 用于测试和最小应用，不是 SQLite 仓库。

同一 `request_id` 的不同语义请求记录 TOOL_REQUESTED 和 STEP_FAILED，失败事件 details 包含 `error_code=REQUEST_ID_CONFLICT`，且不会覆盖首个执行结果。

事件类型冻结为任务/计划/工具请求、风险与权限、审批、checkpoint、执行、深检、commit、rollback、阻断、失败、完成和取消生命周期，具体枚举以 `contracts/enums.py` 为唯一来源。

SANDBOX Mock 按顺序记录 CHECKPOINT_CREATED、EXECUTION_STARTED/FINISHED、DEEP_CHECK_STARTED/FINISHED、COMMIT_STARTED/FINISHED 或 ROLLBACK_STARTED/FINISHED；失败额外记录 STEP_FAILED。审批创建记录 APPROVAL_REQUESTED；批准记录 APPROVAL_GRANTED；拒绝和过期记录 APPROVAL_DENIED，并在 details 中保留明确 `approval_status`。

事件进入存储前递归脱敏嵌套字典和列表。键名匹配不区分大小写，当前覆盖 `api_key`、`authorization`、`password`、`secret`、`token`、`access_token` 和 `refresh_token`。
