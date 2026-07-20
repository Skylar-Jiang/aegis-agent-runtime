# 成员 B：Security Pre/Post Check

分支：`feat/security-pre-post-check`。B 是 `PreExecutionChecker` 和 `PostExecutionChecker` 两个真实实现的唯一负责人：目标一致性、敏感路径/路径穿越、危险 Shell、URL/网络风险、外部文档与工具输出提示注入、Memory 投毒、secret scanning 和 fail-closed 配置。默认测试不得依赖真实 LLM。

允许：`security/`、安全配置、`tests/security/`；禁止：Scheduler、execution/memory/tool 内部、公共 Contract/状态机。C 只提供可检查的 pending、quarantine、memory 和 execution artifacts，绝不实现 PostCheck。输入输出严格遵守 [v0.3](01-contract-v0.3.md)，所有拒绝应含稳定 reason/signals 与 request_id。完成标准：规则测试覆盖每类拒绝与配置失效 fail-closed，Ruff/Pyright/pytest 通过。
