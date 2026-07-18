# 确定性安全规则说明

成员 B 的生产实现位于 `backend/src/ra_agent/security/`，保留已有 Protocol 和 Mock，新增以下可替换实现：

- `RuleBasedRiskClassifier`：结合可信 `ToolSpec`、请求参数、路径、网络目标、上下文来源和可逆性产生 `RiskVerdict`。
- `RuleBasedPolicyEngine`：执行 LOW/MEDIUM/HIGH/CRITICAL/FORBIDDEN 到四类 Runtime 策略的保守映射。
- `RuleBasedPermissionGate`：逐项核对可信 `ToolSpec.required_permissions`，未知、缺失或配置漂移时拒绝。
- `RuleBasedDeepSafetyChecker`：在 Commit 前核对 Pending 状态、关联 ID、变更路径、操作类型、变更数量和输出泄密。

## 配置

- `configs/risk_rules.yaml`：风险映射、危险命令、提示注入、网络 allowlist、受保护路径与深检限制。
- `configs/permissions.yaml`：可信工具权限、确定性权限状态和需要 Runtime 审批的权限。
- `configs/sensitive_paths.yaml`：大小写策略及敏感文件/目录模式。

`RuleEngine.from_directory(Path("configs"))` 会一次性解析并校验三份配置。默认模式在文件缺失、YAML 损坏、未知枚举、遗漏风险映射、权限不一致或非法正则时生成 fail-closed 引擎：风险分类返回 FORBIDDEN/BLOCK，权限全部 DENIED，DeepCheck 不通过。`strict=True` 可用于启动期诊断并抛出 `SecurityConfigurationError`。

## 规则优先级

风险只会升级，不会被较低风险信号覆盖：

`LOW < MEDIUM < HIGH < CRITICAL < FORBIDDEN`

关键规则包括：

- `.env`、私钥、凭据目录等敏感读取至少为 HIGH/REQUEST_APPROVAL。
- `..`（包括 URL 编码形式）路径穿越为 CRITICAL/BLOCK。
- 修改安全配置、Contract、Runtime、容器装配或锁文件为 FORBIDDEN/BLOCK。
- 危险 Shell 命令、私有/回环/元数据网络目标为 CRITICAL/BLOCK。
- 非 allowlist 网络目标为 HIGH/REQUEST_APPROVAL。
- 外部文档、工具输出或 Memory 中的间接提示注入会升级；写入恶意 Memory 为 CRITICAL/BLOCK。
- 未知工具使用 HIGH/REQUEST_APPROVAL；Runtime 的可信 Registry 仍会更早拒绝未注册工具。

## Pending 交接约定

成员 C 的文件 Handler 先在 `pending_root` 下创建 PendingRecord v1；此时
`checkpoint_id` 可以为 `null`。ToolExecutor 必须在返回 `ToolExecutionResult` 前把它绑定为
本次 checkpoint，并将记录放入 `pending_changes`。DeepCheck 位于 ToolExecutor 之后，
因此只接受已经绑定且与 `ToolExecutionResult.checkpoint_id` 相同的记录。

`write_file` 的记录必须严格包含以下字段，不允许缺失或增加未约定字段：

```json
{
  "version": 1,
  "request_id": "request-001",
  "checkpoint_id": "checkpoint-request-001",
  "tool_name": "write_file",
  "operation": "WRITE",
  "target_path": "reports/result.md",
  "pending_path": "request-001/payload.bin",
  "content_sha256": "<64 位十六进制 SHA-256>",
  "size_bytes": 11,
  "created_at": "2026-07-17T22:30:00.123456+00:00",
  "status": "PENDING"
}
```

`delete_file` 使用相同字段集合，但 `operation` 必须为 `DELETE`，且
`pending_path`、`content_sha256`、`size_bytes` 必须全部为 `null`。

DeepCheck 还执行以下确定性检查：

- `version` 必须为整数 `1`，`request_id` 和 `tool_name` 必须与请求一致。
- `target_path` 必须是 workspace 内标准化的 POSIX 相对路径，并与请求目标一致。
- `created_at` 必须是带时区的 ISO 8601 字符串，Commit 前 `status` 必须为 `PENDING`；
  已变为 `COMMITTED` 的记录不得再次进入 DeepCheck。
- `WRITE` 的 `pending_path` 必须位于本次 `request_id` 命名空间；manifest 的
  SHA-256 和字节数必须同时匹配请求的 UTF-8 正文与磁盘上的真实 `payload.bin`。
- `RuleBasedDeepSafetyChecker` 必须同时注入可信的 `workspace_root` 和
  `pending_root`；未配置 `pending_root` 时 `WRITE` 按 fail-closed 拒绝。
- 敏感/受保护路径、路径穿越、异常变更数量、关联 ID 不一致和输出泄密均拒绝提交。

`download_url` 和 `memory_write` 暂时保留旧兼容检查，等成员 C 给出对应的正式
PendingRecord 后再升级为严格 v1 校验。

## 组长接入

公共 `__init__.py`、`core/container.py` 和 `core/bootstrap.py` 由组长审核。生产装配时应创建同一个 `RuleEngine`，把可信 ToolSpec 映射注入分类器，并用 `Settings.workspace_root`、`Settings.pending_root` 构造 DeepSafetyChecker，仅替换 `ServiceContainer` 的四个 Protocol 实现。不得修改或复制 `RuntimeScheduler` 流程。

## 验证

```powershell
python -m pytest tests/security -q --cov=ra_agent.security --cov-report=term-missing
python scripts/check.py
```
