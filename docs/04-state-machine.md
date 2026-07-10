# 运行状态机

终态为 `COMMITTED`、`ROLLED_BACK`、`BLOCKED`、`FAILED`、`CANCELLED`，终态禁止再次转移。

## 固定路径

- 低风险：`PLANNED → RISK_CLASSIFYING → READY → EXECUTING_FAST → COMMITTED`
- 中风险通过：`PLANNED → RISK_CLASSIFYING → CHECKPOINT_CREATING → EXECUTING_SANDBOX → SAFETY_CHECKING → COMMITTING → COMMITTED`
- 中风险失败：前半段相同，随后 `ROLLING_BACK → ROLLED_BACK`
- 高风险：`RISK_CLASSIFYING → WAITING_APPROVAL → READY|BLOCKED`；获批后仍进入 checkpoint/sandbox/check/commit。
- 禁止：`PLANNED → RISK_CLASSIFYING → BLOCKED`

`EXECUTING_SANDBOX` 可同时启动受控执行和深度检查；若执行先结束而检查未结束，进入 `SAFETY_CHECKING`。提交必须同时满足执行成功和检查通过。

状态合法性校验不替代调度前置条件：没有 RiskVerdict 不得执行，BLOCK 不得执行，中风险写操作没有 checkpoint 不得执行，没有检查通过不得 commit，同一 `request_id` 不得重复产生副作用。
