# 工具规范冻结

| 工具         | 权限             | 基础风险 | 副作用  | 可逆性         | 受控模式                    |
| ------------ | ---------------- | -------- | ------- | -------------- | --------------------------- |
| list_dir     | FILE_LIST        | LOW      | NONE    | ATOMIC         | NONE                        |
| read_file    | FILE_READ        | LOW      | READ    | ATOMIC         | NONE；敏感路径动态升到 HIGH |
| write_file   | FILE_WRITE       | MEDIUM   | WRITE   | BACKUP_RESTORE | PENDING                     |
| delete_file  | FILE_DELETE      | HIGH     | DELETE  | BACKUP_RESTORE | PENDING + approval          |
| run_shell    | SHELL_EXEC       | HIGH     | PROCESS | NON_REVERSIBLE | Phase 1 禁用真实执行        |
| download_url | NETWORK_DOWNLOAD | MEDIUM   | WRITE   | ATOMIC         | QUARANTINE                  |
| memory_read  | MEMORY_READ      | LOW      | READ    | ATOMIC         | NONE                        |
| memory_write | MEMORY_WRITE     | MEDIUM   | WRITE   | ATOMIC         | PENDING                     |

所有工具必须注册到 `ToolRegistry`，Planner 不能 import 实现，调用必须形成 `ToolCallRequest` 并经 Scheduler。`allowed_paths`、timeout、network_required 和 dry-run 能力是每个 `ToolSpec` 的必填元数据。
