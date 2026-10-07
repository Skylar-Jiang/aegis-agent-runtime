# Aegis Runtime Base v0.4 主链路改造说明

## 完成范围

本次改造把原来的“每句话手填 TaskContract 的 Task Agent”升级为“持续安全配置下的 Conversation Agent”，同时保留每次执行的 TaskRun、审批恢复、checkpoint、sandbox、deep check、commit、rollback、Effect 和 Audit。

不包含 Aegis Core 后续共通线中的密码协议、签名日志、意图偏移模型、在线阈值学习、多模态检测、MCP Adapter、认证和多租户。

## 数据分层

1. `SecurityProfile`：用户长期允许的工具、Workspace 资源范围、外发开关、数量上限和附加审批策略。每次保存产生新版本。
2. `Conversation`：用户看到的连续会话，保存消息和受限上下文摘要，并绑定一个 Profile 标识。
3. `TaskRun`：每条需要 Planner 执行的用户消息。保存当时继承的 Profile ID/版本和完整有效 TaskContract 快照。
4. `ToolCallRequest`：Planner 的单次工具请求。必须通过 Registry 参数 Schema、TaskContract、风险、策略、权限和执行检查。

有效权限仍遵循：

```text
长期用户授权 ∩ 本次 TaskContract 快照 ∩ Runtime 系统策略 ∩ 可信工具定义
```

Profile 允许某项能力只表示“不因每次调用重复询问”。它不会允许访问 Workspace 外路径，也不会越过更严格的系统风险策略。

## Approval 语义

旧字段 `requires_reconfirmation` 只为读取历史任务保留，不再把带副作用操作直接变成 `BLOCKED`。新主链统一使用：

- 普通已授权操作：`ALLOW → FAST_EXECUTE/SANDBOX_CHECK`；
- Profile 附加审批或系统高风险策略：`REQUEST_APPROVAL → WAITING_APPROVAL`；
- 用户拒绝：终止原请求；
- 用户批准：消费与原 `request_id` 和语义指纹绑定的决定，恢复同一个请求；
- 越出能力或资源边界：`BLOCKED`。

## Tool Schema

`backend/src/ra_agent/tools/arguments.py` 是所有生产工具的参数模型来源。Registry 同时向 Planner 和 `/api/tools` 暴露 JSON Schema，并在风险判断及执行之前校验不可信参数。新增工具时必须同时注册参数模型；不能只增加 Handler 和一段 Prompt 文案。

## 上下文管理

每次新 TaskRun 使用：

```text
较早消息的有界摘要（最多 4000 字符）
+ 最近 12 条原始消息
+ 当前用户目标
+ 当前 Profile 生成的 TaskContract 摘要
+ 本次已完成工具结果（每项受长度限制）
```

`MAX_AGENT_TURNS` 仍限制一次 TaskRun 的 Planner 循环，不限制 Conversation 总轮数。历史工具输出仍按不可信数据处理，不能自动成为授权或新指令。

## 数据库迁移

Alembic `004_workbench_conversations`：

- 为 `agent_tasks` 增加 `conversation_id`、`security_profile_id`、`security_profile_version`；
- 新增 `security_profile_versions`；
- 新增 `conversations`；
- 新增 `conversation_messages`。

启动非 offline Runtime 时会自动执行迁移。迁移不会删除旧任务；旧任务的新增字段保持 `NULL`。

## 前端入口

- `/`：Conversation Agent；
- `/settings/security`：长期安全设置；
- `/tasks`：底层 TaskRun 历史和高级兼容入口；
- `/runtime?task_id=...`：单次执行事实；
- `/approvals`、`/audit`、`/experiments`：审批、审计和实验。

## 验收重点

- 保存 Profile 后刷新和重启仍存在；
- Profile vN 创建的任务始终保留 vN 快照；
- 同一对话第二轮包含上一轮用户消息和 Agent 回复；
- 已授权普通读写不产生逐次审批；
- 高风险请求进入 Approval，批准后恢复原请求；
- 任何 Workspace 外路径仍被 SafePath 和资源范围拦截；
- 所有生产工具具有 JSON Schema，错误参数在执行前失败；
- A/B/C Demo 仍使用真实 Runtime 调度、审批和恢复机制。
