# 执行语义冻结

## 两类 COMMITTED

LOW/FAST_EXECUTE 不产生 Pending，也不调用真实 CommitGate。执行成功即进入 COMMITTED，表示直接执行结果已经可信。

MEDIUM/SANDBOX_CHECK 必须先产生 Pending 结果，经过安全检查和真实 CommitGate 后才进入 COMMITTED，表示 Pending 已提交到 Trusted。Phase 1 尚未实现该完整路径，不能降级为 FAST_EXECUTE。

`pending` 是未经确认、不得被后续可信步骤消费的结果；`trusted` 是通过 commit gate 后可见的状态。Checkpoint 保存恢复所需的最小元数据和备份，不能被表述为所有副作用的通用快照。

中风险路径同时启动受控执行和深检。只有执行成功、深检通过、授权有效、请求未取消且幂等键未提交时才能 commit。任一失败进入 rollback；rollback 失败进入 FAILED 并产生高优先级审计事件，不得继续使用 pending 结果。

超时取消执行与检查，清理 pending 并尝试 rollback；用户取消遵循相同语义。非可逆操作不得进入投机执行。`request_id` 是副作用幂等键：相同语义重试等待或复用首个结果，不同语义复用同一 ID 时返回 `REQUEST_ID_CONFLICT`，同一请求至多执行和 commit 一次。
