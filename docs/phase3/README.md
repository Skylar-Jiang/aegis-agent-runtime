# Phase 3 开发基线

**Aegis Runtime：面向工具增强型智能体的风险自适应安全运行时** 的 Phase 3 目标是补全原始策划书的受控并行检测、Pre/Post Check、可信 Memory、下载隔离、权限感知调度、三模式实验和审计展示。非目标：重写 Agent、绕过 Scheduler、提前实现成员负责的业务模块，或开放任意 Shell。

已完成：Phase 0 Contract/目录冻结，Phase 1 Mock 编排，Phase 2 文件受控执行、SQLite 审计审批、Agent/API/SSE/前端。缺口是上述 Phase 3 能力的真实实现与实验。

| 成员 | 分支 | 交接文件 |
| --- | --- | --- |
| 组长 A | `feat/runtime-parallel-scheduler` | [02](02-member-a-runtime.md) |
| B 安全 | `feat/security-pre-post-check` | [03](03-member-b-security.md) |
| C 执行 | `feat/memory-download-execution` | [04](04-member-c-execution.md) |
| D 实验/UI | `feat/experiments-dashboard` | [05](05-member-d-experiments-ui.md) |

合并顺序为 C → B → A → D；公共 Contract、枚举、状态机、`core/container.py`、`core/bootstrap.py` 只由组长修改。全部 PR 须通过 backend pytest、Ruff、Pyright、frontend lint/typecheck/tests/build、`scripts/check.py`。

六个演示：低风险 README；隔离下载并行检测；危险 Shell 阻断；外部文档提示注入阻断；Memory 投毒回滚；下载权限等待期间继续报告准备。
