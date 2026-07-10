# 测试计划

## Phase 0 自动化

健康检查、Contract 创建/JSON 序列化、naive datetime 拒绝、风险与策略枚举、状态机合法/非法流转、Mock ToolCallRequest 调度、AuditEvent 顺序、YAML 配置解析和前端占位渲染。

## 后续安全验收

- 正常任务：低风险不触发深检，审计完整。
- 危险命令：CRITICAL/BLOCK，不产生真实副作用。
- 敏感文件：`.env` 读取进入审批或阻断，日志不泄密。
- 间接提示注入：外部内容诱导的后续工具请求仍被分类。
- Memory 投毒：只写 pending，失败后不进入 trusted。
- 状态污染：检查失败后文件、memory 和结果不可见。
- 超时/取消：终止执行并清理/回滚。
- Rollback 失败：进入 FAILED、告警且不继续任务。

所有 CI 测试使用 Mock，不调用真实外部 API 或 LLM。
