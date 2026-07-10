# Phase 0 范围冻结

## 本阶段交付

冻结单体仓库结构、Python/Node 技术栈、公共枚举和 Contract、模块调用边界、运行状态机、八个工具规范、REST/SSE 接口、审计事件、YAML 配置、测试与 CI。交付可启动的 FastAPI 健康检查、可构建的 React 占位入口、Mock Runtime 调度链和内存 AuditEvent sink。

## 本阶段不交付

不实现真实 LLM Planner、完整风险算法、真实文件/网络/Shell 工具、完整页面、生产数据库、分布式任务、多 Agent、RAG 或模型训练。

## 安全能力边界

Mock 分类器固定返回 LOW，Mock 权限门固定返回 NOT_REQUIRED，Mock 执行器不触发真实工具。它们只能验证 Contract 与模块接线，不能保护生产环境。规则关键词只是第一层信号，后续必须结合参数、任务上下文、历史轨迹、结果内容和深度检查。

临时目录与 pending 目录不是系统级 Sandbox。文件备份只覆盖可控工作区内的普通文件；任意 Shell 命令、外部系统副作用和不可逆操作不能承诺回滚。Phase 0 的 `run_shell` 没有真实实现。
