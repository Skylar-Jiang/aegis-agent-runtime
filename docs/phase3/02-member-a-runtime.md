# 成员 A（组长）：Runtime

分支：`feat/runtime-parallel-scheduler`。输入为 v0.3 Contract、B 的 Pre/Post checker、C 的 pending/rollback 实现；输出为 Scheduler 的受控并行编排、权限感知步骤调度、取消/异常 rollback、Container/Bootstrap 接线和集成测试。

允许修改：`runtime/`、`core/container.py`、`core/bootstrap.py`、`main.py`、Contract/状态机（仅组长）、integration tests。禁止修改 `security/`、`execution/`、`memory/`、`tools/` 的内部实现。必须保证 Scheduler 是唯一副作用入口，PreCheck/执行/PostCheck 全通过才 commit，权限等待不阻塞无依赖步骤；覆盖并行、取消、关联 ID、rollback 与合并集成测试。
