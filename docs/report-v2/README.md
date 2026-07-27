# 比赛作品报告书骨架

本目录只收录可核验的报告文字、图表说明和数据索引；图片放 `assets/`，真实实验原始数据放仓库 `experiments/results/raw/`，本目录只链接其 run ID。

| 章节 | 文件 | 主责 | 必需证据 |
| --- | --- | --- |
| 摘要与问题 | `01-summary.md` | 组长 | 威胁、贡献、范围与限制 |
| 架构与运行时 | `02-architecture-runtime.md` | 组长 | 架构图、Graph 时序、Scheduler 不变量 |
| 风险与人工审核 | `03-security-approval.md` | 成员 2 | 策略表、审批截图、安全/人工数据 |
| 状态提交与回滚 | `04-effects-rollback.md` | 成员 3 | effect 时序、回滚证据和数据 |
| 实验与演示 | `05-experiments-demo.md` | 成员 4 汇总 | raw run ID、CSV 图表、UI/Demo 截图 |
| 结论与局限 | `06-conclusion-limitations.md` | 组长 | 可证明结论与未实现能力 |

所有数字必须能追溯到 `02-experiment-data.md` 的 raw result；所有截图须标注场景、commit、生成日期，且不得包含密钥、敏感文件内容或隐藏推理。
