# 模块所有权与公共变更流程

## 模块所有权

成员 A（组长）负责：

- `agent/`
- `runtime/`
- `core/container.py`
- `core/bootstrap.py`
- `main.py`
- 总集成和 integration tests

成员 B 负责：

- `security/`
- `configs/risk_rules.yaml`
- `configs/permissions.yaml`
- `configs/sensitive_paths.yaml`
- 安全规则测试

成员 C 负责：

- `execution/`
- `tools/`
- `memory/`
- `configs/tool_policies.yaml`
- rollback tests

成员 D 负责：

- `audit/`
- `database/`
- `api/`
- `frontend/`
- `experiments/`
- e2e tests

## 公共文件

以下内容只能由组长审核后修改：

- `contracts/`
- 公共枚举
- `backend/pyproject.toml`
- `backend/uv.lock`
- 公共 `__init__.py`
- 状态机定义

模块负责人应依赖 `ServiceContainer` 和公共 Protocol，不在自己的路由、Scheduler 或测试中重复装配同一组 Runtime 依赖。

`RequestExecutionRegistry` 是可替换的幂等接口；当前使用线程安全内存实现，成员 D 后续可提供持久化实现，但不修改其请求指纹和冲突语义。

## 并行接入点

- 成员 B：实现 `RiskClassifier`、`PolicyEngine`、`PermissionGate`、`DeepSafetyChecker`；返回值必须保留 request_id，不能调用工具。
- 成员 C：实现 `ToolExecutor`、`CheckpointManager`、`CommitGate`、`RollbackManager` 与 Registry 中的 `ToolHandler`；Scheduler 仍是唯一执行入口。
- 成员 D：实现 `AuditRecorder`、`ApprovalService`、审批 API/SSE/数据库和前端消费；不得改变两阶段审批、序号或幂等语义。

`build_mock_container()` 是共享可运行基线，生产路由不得重复手工装配依赖。Mock 用于接口和编排验证，不是安全实现。

## Contract 变更流程

1. 成员先提出变更原因。
2. 说明缺失字段及影响模块。
3. 由组长统一修改 Contract。
4. 更新 Contract 测试和文档。
5. 其他成员同步 `develop` 后继续开发。

Contract v0.2 已冻结。成员 B、C、D 后续实现现有 Protocol 即可；除非按上述流程获批，不需要修改公共 Contract、枚举或状态机。
