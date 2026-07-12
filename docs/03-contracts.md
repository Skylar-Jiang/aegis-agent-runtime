# 公共 Contract v0.2

所有 ID 都是字符串。公共时间字段统一使用共享 `UTCDateTime`：拒绝 naive datetime，并把任意 aware datetime 归一化为 UTC；序列化使用 Pydantic `model_dump(mode="json")`。统一标识名为 `task_id`、`step_id`、`request_id`、`checkpoint_id`、`event_id`。列表和字典使用 Pydantic `default_factory`，不得共享可变默认值。

| Contract | 用途 |
| --- | --- |
| `TaskCreateRequest`, `TaskResponse`, `TaskStep` | 任务入口、快照和步骤状态 |
| `ToolSpec`, `ToolCallRequest` | 可信工具元数据与唯一调用请求 |
| `RiskVerdict`, `PermissionDecision`, `PermissionCheckResult` | 风险证据、策略建议和多权限汇总 |
| `ApprovalRequest`, `ApprovalDecision` | 人工审批生命周期 |
| `CheckpointResult`, `ToolExecutionResult` | 受控执行状态、产物、pending 变更和时间 |
| `DeepCheckResult`, `CommitResult`, `RollbackResult` | pending 到 trusted 或恢复的结果 |
| `AuditEvent` | UI、实验和追踪的统一事实 |
| `APIError`, `APIResponse[T]` | HTTP 统一响应封装 |

## v0.2 冻结字段

`ToolCallRequest` 包含 `task_id/step_id/request_id/tool_name/arguments/objective/context_summary/source_type/requested_at`。`objective` 是用户原始目标；`context_summary` 只保存产生本次调用所需的上下文摘要；`source_type` 使用 `user/agent/external_document/tool_output`。Contract 不记录、传输或要求模型隐藏推理过程。

`ToolExecutionResult` 包含 `task_id/step_id/request_id/status/output/error/error_code/checkpoint_id/sandbox_path/artifacts/pending_changes/started_at/finished_at`。`REQUEST_ID_CONFLICT` 使用结构化 `error_code` 表达。

`request_id` 同时是请求标识和幂等键。指纹包含任务、步骤、工具、参数、目标、上下文和来源，不包含 `requested_at`；相同语义重试返回首个结果，不同语义使用相同 ID 时安全失败。

`RiskVerdict` 除风险、建议和原因外，还携带 `signals/matched_rules/requires_deep_check/requires_checkpoint`。`DeepCheckResult` 携带 `signals`。`PermissionCheckResult` 汇总每项 `PermissionDecision`，并明确 `allowed/requires_approval/reason`。

权限检查只接受注册表中的可信 `ToolSpec`：

```python
async def check(
    request: ToolCallRequest,
    tool_spec: ToolSpec,
) -> PermissionCheckResult: ...
```

Agent 不声明也不能覆盖工具所需权限。

Contract 变更必须先说明原因、缺失字段和影响模块，由组长统一修改 Contract、测试和本文档；其他成员同步 `develop` 后继续开发，不得在业务模块定义同义模型、枚举或字段。
