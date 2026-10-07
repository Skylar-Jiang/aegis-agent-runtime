# 成员三整周接口交接

本文件供组长核对成员三从 9 月 14 日至 9 月 20 日的全部 Core 工作。第一轮密码原语 PR
保持独立；第二轮联调分支同时依赖 P1 和 P2 第一轮代码，待两个基础 PR 合入 aegis-core
后再整理为只含联调提交的正式 PR。

## 冻结公开接口

```python
Canonicalizer.canonicalize(value) -> bytes
DigestProvider.sm3(payload: bytes) -> str

SignatureProvider.sign_sm2(payload: bytes, *, key_id: str) -> str
SignatureProvider.verify_sm2(payload: bytes, signature: str, *, key_id: str) -> bool
SignatureProvider.list_public_keys() -> dict[str, str]

EnvelopeService.sign(payload, *, object_type: str, key_id: str) -> SignedEnvelope
EnvelopeService.verify(payload, envelope, *, object_type: str) -> bool
signed_object_digest(payload, envelope) -> str

HashChain(task_id: str)
HashChain.append_event(event: dict) -> dict
HashChain.get_chain_head() -> str

CheckpointStore.save_checkpoint(record) -> None
CheckpointStore.get_trusted_checkpoint(checkpoint_id: str) -> CheckpointRecord

AuditVerifier.verify_bundle(
    bundle: dict,
    *,
    trusted_checkpoint_id: str,
    task_id: str,
) -> VerificationResult
```

`SignedEnvelope` 仍为：
`object_type schema_version algorithm key_id payload_digest signature`。

`AuditCheckpoint` 仍为：
`checkpoint_id from_seq to_seq chain_head created_at signer`。

签名输入、SM2 ID、Base64/DER 编码、SM3/JCS 规则和错误码以
`docs/interfaces/crypto_audit_v1.md` 为准。

## 第二轮新增的内部联调接口

```python
EvidenceRecorder.record_object(
    *,
    task_id: str,
    object_type: str,
    payload: dict,
) -> str

EvidenceRecorder.list_task_objects(
    task_id: str,
    references: set[str],
) -> list[dict]

AuditExportService.export_task(
    *,
    task_id: str,
    checkpoint_id: str,
) -> dict
```

P1 Gateway 不需要增加公共 API 字段。注入 EvidenceRecorder 后，它把
ToolCallEnvelope、GatewayDecision 和 ToolResult 交给 P3 签名，并把完整签名对象摘要放入
P2 BehaviorEvent 的 object_digest/result_digest。未注入时仍兼容 P1 替身，但这类事件不能
作为完整对象绑定证据导出。

P2 JsonlEventStore 构造参数保持：
`JsonlEventStore(path, chain_factory=HashChain)`。Gateway 现在会提供 P2 要求的全部必填
事件字段；P2 继续负责 sequence 分配和同快照写入，P3 负责确定性链计算与离线复算。

## 需要组长确认的边界

- `GatewayDecision` 签名载荷增加 task_id 和 request_id 作为任务绑定字段，但不修改
  GatewayDecision 的公开响应模型。
- `ToolResult` 签名载荷为 `{task_id, request_id, result}`。
- P1 的 contract_ref.digest 仍是现有契约版本摘要，只放在 evidence_refs 中；不能称为
  P3 的 SM3 签名对象摘要。
- 真正的 object_digest/result_digest 一律使用
  `SM3(JCS({payload, envelope完整六字段}))`。
- 应用默认 fake；设置 `CORE_CRYPTO_MODE=sm2` 后，缺钥匙或 OpenSSL 不可用即启动失败。
- UI 与 POST /api/v1/audit/export 仍由 2 号负责；成员三交付的是可注入的真实导出服务、
  CLI 验证器和端到端演示。

## 演示和验收

```powershell
uv sync --project backend --group dev --frozen --no-editable --reinstall-package ra-agent
uv run --project backend --no-editable python scripts/demo_core_crypto.py --output .runtime/p3-full-week-demo
uv run --project backend --no-editable python -m ra_agent.audit --bundle .runtime/p3-full-week-demo/bundle.json --keys .runtime/p3-full-week-demo/public_keys.json --checkpoints .runtime/p3-full-week-demo/trusted_checkpoints --checkpoint-id fixture-cp-1 --task-id fixture-task-1
```

第二条演示命令真实经过 Gateway、P2 JsonlEventStore、P3 SM2 签名对象、SM3 哈希链、签名检查点
和 AuditVerifier；输出同时包含正常验证成功与篡改失败。临时私钥在演示结束时删除。


## 本地最终验证结果

- 最新受影响模块集合：87 passed；真实 Gateway/EventStore/SM2/导出联调文件：9 passed。
- `tests` 共收集 1041 项。为隔离 Windows 下全量顺序运行的等待问题，按五组覆盖全部
  测试并在最后一次修改后复跑受影响集合，合计 1029 passed、8 skipped、4 failed。
  其中 `test_demo_scheduling_waits_only_for_the_high_risk_branch` 是并行负载下的时序波动，
  单独立即复跑为 1 passed；其余 3 项均位于
  `tests/rollback/test_v2_rollback_benchmark.py`，是已在未修改的 `origin/aegis-core`
  上复现的 Windows 回滚/隔离区路径问题，不涉及本分支密码、事件或导出代码。
- Ruff 标准范围 `backend/src tests scripts`：通过。
- Pyright：0 errors、0 warnings。
- P2 OpenAPI、TypeScript 与 fixture 漂移检查：通过。
- 前端：11 个测试文件、39 tests passed；ESLint、TypeScript、生产构建通过。
- 真实演示：3 条 Gateway 事件、6 个 SM2 签名对象；正常证据包通过，篡改包返回
  `CHAIN_INVALID`；随后使用独立 CLI 和公钥再次验证通过。
