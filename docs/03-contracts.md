# 公共 Contract

所有 ID 都是字符串，时间字段使用带时区 UTC datetime，序列化使用 Pydantic `model_dump(mode="json")`。统一标识名为 `task_id`、`step_id`、`request_id`、`checkpoint_id`、`event_id`。

| Contract                                            | 用途                            |
| --------------------------------------------------- | ------------------------------- |
| `TaskCreateRequest`, `TaskResponse`, `TaskStep`     | 任务入口、快照和步骤状态        |
| `ToolSpec`, `ToolCallRequest`                       | 工具注册元数据与唯一调用请求    |
| `RiskVerdict`, `PermissionDecision`                 | 风险证据、建议策略和权限结果    |
| `ApprovalRequest`, `ApprovalDecision`               | 人工审批生命周期                |
| `CheckpointResult`, `ToolExecutionResult`           | 受控执行产物                    |
| `DeepCheckResult`, `CommitResult`, `RollbackResult` | pending 到 trusted 或恢复的结果 |
| `AuditEvent`                                        | UI、实验和追踪的统一事实        |
| `APIError`, `APIResponse[T]`                        | HTTP 统一响应封装               |

Contract 变更必须先修改本文件和 Contract 测试，经四个模块负责人评审后合并；其他模块不得定义同义枚举或字段。
