# 审计事件 Schema

`AuditEvent` 字段为 `event_id/task_id/step_id/request_id/sequence_number/event_type/timestamp/actor/status/risk_level/decision/summary/details`。同一任务 sequence_number 严格单调增加；timestamp 是 UTC aware datetime。

事件类型冻结为任务/计划/工具请求、风险与权限、审批、checkpoint、执行、深检、commit、rollback、阻断、失败、完成和取消生命周期，具体枚举以 `contracts/enums.py` 为唯一来源。

事件在进入 sink 前必须递归脱敏认证头、token、password、secret、API key 和敏感文件内容。Phase 0 的 `redact` 只覆盖顶层常见键，真实递归脱敏是后续安全工作，不得将当前实现用于生产日志。
