# V2 Graph、Approval 与 Experiment API 冻结

所有响应继续使用 `APIResponse { data, error }`；Audit/SSE 继续按 `sequence_number` 重放和去重。Graph API 是组长独占实现，成员 4 只消费下列 shape。

| Method | Path | Request / response | 语义 |
| --- | --- | --- | --- |
| POST | `/api/task-graphs` | body `TaskGraph`; data `{graph_id, task_id, status, created_at}` | 校验 task/graph/node 关联后后台提交，立即返回 `RUNNING`。重复 graph ID 或不一致 Contract 失败关闭。 |
| GET | `/api/tasks/{task_id}/graph` | data redacted graph snapshot | 返回 `TaskGraphResult` 的状态投影（`graph_id, task_id, node_id, request_id, step_id, status, checkpoint_id, error_code, blocked_nodes, started_at, finished_at`）；省略 `ToolExecutionResult.output` 和原始 artifact 内容。不存在或 task 不匹配为 404。 |
| POST | `/api/task-graphs/{graph_id}/cancel` | data `TaskGraphResult` | 取消 Runtime、fence 所有 WAITING_APPROVAL 节点并等待状态收敛。 |
| POST | `/api/task-graphs/{graph_id}/resume` | body `{approval_id}`; data `TaskGraphResult` | 仅已有 grant/deny 决定的 matching approval 可恢复；已取消 Graph 必须 fail-closed。 |
| GET | `/api/tasks/{task_id}/approvals` | data `ApprovalRequest[]` | task scoped，供任务页自动发现 pending approval。 |
| GET | `/api/approvals?status=PENDING&task_id=` | data `ApprovalRequest[]` | Approval 页列卡片；前端不得要求输入 approval ID。 |
| POST | `/api/approvals/{id}/grant|deny` | existing decision response | UI 从卡片携带内部 ID；graph decision 后调用 resume endpoint。 |
| GET | `/api/tasks/{task_id}/effects` | data redacted `EffectRecord[]` | 仅 `effect_id, kind, target_ref, status, checkpoint_id, artifact_refs, created_at`，用于 Pending/Commit/Rollback/Preserved 投影。 |
| GET | `/api/tasks/{task_id}/events` / `stream` | existing Audit JSON / SSE | Graph events 必须带 `details.graph_id`；不新增另一条未经审计的 Graph event channel。 |
| GET | `/api/experiments/results`, `/results/{filename}`, `/{run_id}` | file listing / validated raw result / run lookup | filename 只允许 basename 与 `.json/.jsonl/.csv`；错误使用 `error`，不能把错误伪装为 result data。 |

Graph snapshot 和 resume 是进程内状态；服务重启后不得宣称可恢复运行中的 Graph。正式 benchmark 必须在单一受控 runner 生命周期内完成，或在以后新增 durable graph-state Contract 后才可改变此限制。

前端展示只使用 snapshot、redacted EffectRecord 与 Audit 事实：不复制 Contract，不从 Audit `details.approval_id` 猜审批，也不显示工具原始输出或 chain-of-thought。
