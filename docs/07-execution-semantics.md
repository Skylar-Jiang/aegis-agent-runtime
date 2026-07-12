# 执行语义冻结

## 两类 COMMITTED

LOW/FAST_EXECUTE 不产生 Pending，也不调用真实 CommitGate。执行成功即进入 COMMITTED，表示直接执行结果已经可信。

MEDIUM/SANDBOX_CHECK 必须先产生 PENDING_COMMIT，经过安全检查和 CommitGate 后才进入 COMMITTED。Phase 1 只使用无副作用 Mock 验证这一编排，不能声称已实现真实 Sandbox、Trusted 存储或恢复。

`pending` 是未经确认、不得被后续可信步骤消费的结果；`trusted` 是通过 commit gate 后可见的状态。Checkpoint 保存恢复所需的最小元数据和备份，不能被表述为所有副作用的通用快照。

当前中风险 Mock 路径依次执行受控执行和结果深检。只有执行返回 PENDING_COMMIT、关联一致、深检通过、授权有效且幂等键未提交时才能 commit。任一失败在有效 checkpoint 存在时尝试 rollback；rollback 失败进入 FAILED，不得继续使用 pending 结果。

REQUEST_APPROVAL 不长期阻塞协程。首次 schedule 返回 WAITING_APPROVAL；resume 校验 approval_id、request_id、tool_name 和语义指纹并原子消费决定。批准后仍重新校验权限，并按 ToolSpec 进入 FAST/SANDBOX；不可逆或禁用工具保持阻断。

超时取消执行与检查，清理 pending 并尝试 rollback；用户取消遵循相同语义。非可逆操作不得进入投机执行。`request_id` 是副作用幂等键：相同语义重试等待或复用首个结果，不同语义复用同一 ID 时返回 `REQUEST_ID_CONFLICT`，同一请求至多执行和 commit 一次。
