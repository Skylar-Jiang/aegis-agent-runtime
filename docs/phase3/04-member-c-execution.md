# 成员 C：Memory、Download 与执行

分支：`feat/memory-download-execution`。负责 pending/trusted Memory、Memory commit/rollback、download quarantine（大小、重定向、哈希、路径、SSRF）、pending/checkpoint/commit/rollback、取消/异常清理，最后才实现受限 Shell。

允许：`execution/`、`memory/`、`tools/`、相应配置与 tests；禁止：`shell=True`、任意系统命令、绕过 Scheduler、下载直接进入 trusted workspace、让 rejected memory 被 Agent 读取、修改 Scheduler/security 内部。输出必须使用 v0.3 状态和关联 ID；完成标准为文件、下载、memory、取消和 rollback 测试全部通过。
