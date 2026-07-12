# Phase 1：Runtime 基础闭环范围

## 本阶段交付

在既有工程骨架上冻结 Contract v0.2、Node.js 24.14.0/pnpm 10.12.4、模块边界和统一依赖装配；交付四策略调度骨架、请求幂等、权限一致性、UTC Contract、内存 AuditRecorder，以及 SANDBOX_CHECK/REQUEST_APPROVAL 的无副作用 Mock 闭环。

## 本阶段不交付

不实现真实 LLM Planner、完整风险算法、真实文件/网络/Shell 工具、真实 Sandbox/Checkpoint/Commit/Rollback、正式审批 API、完整页面、生产数据库、分布式任务、多 Agent、RAG 或模型训练。

## 安全能力边界

Mock 分类器只实现共享 Fixture 的确定性语义基线，Mock 权限门固定返回 NOT_REQUIRED，Mock 执行器不触发真实工具。它们只能验证 Contract 与模块接线，不能保护生产环境。规则关键词只是第一层信号，后续必须结合参数、任务上下文、历史轨迹、结果内容和深度检查。

临时目录与 pending 目录不是系统级 Sandbox。文件备份只覆盖可控工作区内的普通文件；任意 Shell 命令、外部系统副作用和不可逆操作不能承诺回滚。Phase 1 的 `run_shell` 没有真实实现。
