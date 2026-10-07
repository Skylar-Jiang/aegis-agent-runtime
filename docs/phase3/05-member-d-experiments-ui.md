# 成员 D：实验与展示

分支：`feat/experiments-dashboard`。实现 `BASELINE`、`FULL_GUARD`、`ADAPTIVE_RUNTIME` 固定测试集，产出 JSON/CSV/Markdown；统计安全性、可恢复性、效率、审计指标；展示审计时间线、并行时间重叠和三模式图，并补充 E2E 和最终界面。

允许：`experiments/`、`audit/`、`api/`、`frontend/`、e2e tests；只能消费稳定 Contract/审计/API 边界，禁止修改 Scheduler、security/execution/memory 内部和公共 Contract。三模式必须使用相同任务、工具、workspace 初始状态、Planner、超时配置。完成标准：可复现实验产物、UI/E2E 证据和三模式公平性检查。
