# 给组长的 P2 PR1 接口交接

分支：`feature/core-event-ui-p2`；目标：`aegis-core`；起点：`d59df71`。

BehaviorEvent 和 JsonlEventStore 已在 `backend/src/ra_agent/events/` 实现，前端 `/core` 提供固定 mock 场景。
详细字段、默认值、限制、异常和示例见 `docs/interfaces/p2-events-v1.md`；机器可读 Schema 见 `docs/interfaces/schemas/p2-events.openapi.json`。

## 可直接使用的方法

```python
JsonlEventStore(path: pathlib.Path, *, chain_factory=None)
await store.append_event(event: dict) -> dict
await store.list_task_events(task_id: str) -> list[dict]
await store.get_chain_head(task_id: str) -> str | None
```

append 输入必须有：event_id、task_id、parent_event_id、type、actor、source_ref、object_digest、state、decision、result_digest、occurred_at。
sequence 可省略，存储按任务从 1 自动分配；如果显式提供，必须等于下一序号。
parent_event_id、object_digest、decision、result_digest 可以为 null，但键必须存在。
decision 复用现有四类 GatewayDecisionType；request_id、reason_code、evidence_refs 已保留用于关联。

返回完整规范化事件，包括 `schema_version="1.0"` 和六个预留扩展字段。
同任务重复 event_id 报 EVENT_ID_CONFLICT；未知任务返回空列表。
未配置密码提供方时，非空任务 get_chain_head 报 CHECK_UNAVAILABLE；不会返回伪正式链头。

## 需要同步的参数

请在第二轮接线前一起核对：

- Gateway 发出的 actor、source_ref 由哪个可信组件提供；request_id、reason_code、evidence_refs 按此提案保留是否合适。
- 评估和执行事件的 object_digest / result_digest 如何切换到 3 号要求的完整签名对象摘要；当前原始 result 不直接存入 BehaviorEvent。
- `schema_version` 和所有 null/default 字段均进入完整事件与摘要输入，三方应使用同一序列化样例。
- P3 HashChain 可通过 chain_factory 注入；当前已用替身验证保存、重启重建和链记录比对。真实 JCS/SM3 与 AuditVerifier 接线是第二轮工作。

第一轮前端可独立演示：在 frontend 执行 `pnpm dev`，进入 `/core`。
本文件已通过 [PR #3 的单独评论](https://github.com/chiyi-creator/aegis/pull/3#issuecomment-5746286256)交接给 `@chiyi-creator`；第二轮联调前需确认上述参数。
