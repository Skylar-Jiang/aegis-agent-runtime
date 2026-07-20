# Contract v0.3

v0.3 是对 v0.2 的加法冻结：`PreCheckResult`、`PostCheckResult`、`MemoryStatus(PENDING/TRUSTED/REJECTED/ROLLED_BACK)`、`ExperimentMode(BASELINE/FULL_GUARD/ADAPTIVE_RUNTIME)`，以及带 `tool_name`、`arguments`、`dependencies`、`required_permissions` 的 `TaskStep`。既有字段和枚举语义不变。

`PreExecutionChecker.check(request, verdict)` 校验工具、参数、目标、路径、URL、权限和上下文；`PostExecutionChecker.check(request, execution)` 校验输出、pending 文件、下载内容与 pending memory。两者均必须回传同一 `request_id`。PreCheck 可与 pending/sandbox execution 并行，但仅当 PreCheck、执行和 PostCheck 全通过时 Commit；否则 Rollback。当前仅提供 Protocol/Mock，真实实现分别归 B/C。

时序：`PLANNED → PreCheck + pending execution → PostCheck → COMMIT|ROLLBACK`；取消或任一异常必须禁止 Commit 并清理 pending。依赖未完成或等待权限的步骤不可运行；无依赖且权限满足的步骤可继续。所有结果必须保留 task/step/request 关联 ID，`request_id` 保持幂等键。

禁止 Agent、UI、检查器直接调用 Handler；只有 Scheduler 可进入 Executor。破坏兼容性需在 PR 中说明迁移路径、更新 Contract tests，并由组长先修改公共文件。
