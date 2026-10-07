# Core v1：成员三密码与审计接口（供 P1/P2 核对）

范围：C6。保留开发计划中的模型字段和服务方法名称，以及 P1 已发布的
`ra_agent.gateway.interfaces.SignatureProvider` 签名。下文细化的编码、证据包和错误码是
P3 提交的 v1.0 联调基线，需要非作者评审后合入 `aegis-core`；不能据此声称三人已确认。

## 导入与方法签名

```python
from ra_agent.crypto import (
    Canonicalizer, DigestProvider, OpenSSLSignatureProvider,
    SignedEnvelope, EnvelopeService, generate_sm2_key, signed_object_digest,
)
from ra_agent.audit import (
    AuditCheckpoint, HashChain, FileCheckpointStore, AuditVerifier, create_checkpoint,
)

Canonicalizer.canonicalize(value: JSONValue) -> bytes
DigestProvider.sm3(payload: bytes) -> str

OpenSSLSignatureProvider(
    public_keys: dict[str, str], *, private_keys: dict[str, pathlib.Path] | None = None
)
SignatureProvider.sign_sm2(payload: bytes, *, key_id: str) -> str
SignatureProvider.verify_sm2(payload: bytes, signature: str, *, key_id: str) -> bool
SignatureProvider.list_public_keys() -> dict[str, str]

EnvelopeService.sign(payload, *, object_type: str, key_id: str) -> SignedEnvelope
EnvelopeService.verify(payload, envelope, *, object_type: str) -> bool
signed_object_digest(payload, envelope: SignedEnvelope | dict) -> str

HashChain(task_id: str)
HashChain.append_event(event: dict) -> dict  # 返回 ChainEntry；不替代 P2 EventStore
HashChain.get_chain_head() -> str
HashChain.export_bundle(record: CheckpointRecord, *, objects: list[dict] | None = None) -> dict

create_checkpoint(chain, signer: EnvelopeService, *, key_id: str,
                  checkpoint_id: str) -> CheckpointRecord
FileCheckpointStore(directory: Path, envelopes: EnvelopeService)
CheckpointStore.save_checkpoint(record: CheckpointRecord | dict) -> None
CheckpointStore.get_trusted_checkpoint(checkpoint_id: str) -> CheckpointRecord

AuditVerifier(envelopes: EnvelopeService, checkpoints: CheckpointStore)
AuditVerifier.verify_bundle(bundle: dict, *, trusted_checkpoint_id: str,
                            task_id: str) -> VerificationResult
```

这些方法均为同步方法。SM2 适配器启动外部进程；异步网关/导出路由应通过
`asyncio.to_thread(...)` 调用。不要在事件循环中进行阻塞签名，也不要让 Agent 输入选择
私钥路径、OpenSSL 路径或可信检查点目录。`AEGIS_OPENSSL` 由部署方设置。

## SignedEnvelope：保持六个字段

| 字段 | 类型 / 值 |
|---|---|
| object_type | 非空字符串，调用方必须明确指定；验证时必须检查期望对象类型 |
| schema_version | 固定字符串 `"1.0"` |
| algorithm | 固定字符串 `"SM2-SM3"` |
| key_id | 非空字符串，最长 128；由可信公钥表查找，不是文件路径 |
| payload_digest | `SM3(JCS(payload))`，64 位小写十六进制，无前缀 |
| signature | 标准 Base64（含 padding）编码的 ASN.1 DER `SEQUENCE(INTEGER r, INTEGER s)` |

签名输入精确定义为：

```text
M = ASCII("AEGIS-CORE:SIGNED-ENVELOPE:v1") || 0x00 ||
    JCS({object_type, schema_version, algorithm, key_id, payload_digest})
signature = Base64(SM2-SM3-Sign(M, SM2_ID="1234567812345678"))
```

SM2 使用标准 sm2 曲线，OpenSSL 计算 `ZA` 和 `SM3(ZA || M)`。不要自行再次预哈希 M，
不要把 ASCII hex 当摘要原始字节，不要使用 ECDSA 替代 SM2。公钥列表的值为
SubjectPublicKeyInfo PEM（`BEGIN PUBLIC KEY`）；私钥不进入公钥表、JSON、日志或样例。
签名包含所有元数据，改 key_id（即使对应同一把公钥）、类型、版本或算法均不能复用签名。

## JCS 输入规则

采用 `rfc8785==0.1.4`。输入为明确选定的 JSON 对象，不接受自动猜字段、`default=str`、
本地化日期或直接传 Pydantic 模型。模型先用 `model_dump(mode="json")`。
字符串按 UTF-16 code unit 排序，输出 UTF-8；不做 Unicode NFC 等额外归一化。
拒绝 NaN/Infinity、孤立代理字符、非字符串对象键和超出安全整数范围的 Python int。
JSON 文件用 `load_json` 读取，拒绝重复键；Python dict 已丢失的重复键无法追溯。
日期由业务模型明确序列化；`null` 与字段缺失不同，所有扩展字段都进入摘要。

## BehaviorEvent 与链：P2 接入约定

不另建一套 BehaviorEvent 模型。接收计划中的 13 个必需字段：
`event_id sequence task_id parent_event_id type actor source_ref object_digest state decision
result_digest occurred_at`。扩展字段原样保留并全部进入 JCS 摘要。

每个 task 独立维护一条链，sequence 必须从 1 开始连续增长，event_id 不重复，
parent_event_id 为 null 或同任务已出现事件的 ID。P2 必须在同一个存储事务中分配序号、
写入事件及更新链头；HashChain 是确定性计算工具，不提供数据库并发或持久化保证。

```text
H0 = 32 个零字节
Ei = SM3(JCS(完整 BehaviorEvent))
Hi = SM3(ASCII("AEGIS-CORE:AUDIT-CHAIN:v1") || 0x00 || H(i-1) || Ei)
ChainEntry = {event, prev_hash, event_digest, chain_hash}
```

拼接时 H 和 E 用各 32 字节的原始摘要，JSON 中才使用 64 位小写 hex。
空链 `get_chain_head()` 返回 64 个 `0`，不生成空检查点。

## AuditCheckpoint：保持六个字段

| 字段 | 类型 / 值 |
|---|---|
| checkpoint_id | 非空字符串，最长 128，由可信调用方固定选择 |
| from_seq | int；Core v1 固定 1，仅支持完整前缀 |
| to_seq | int >= 1，锚定前缀最后一条事件序号 |
| chain_head | to_seq 对应 SM3 链头，小写 hex |
| created_at | UTC ISO 8601 字符串，以 Z 结尾；不是可信时间戳服务 |
| signer | 签检查点的 key_id，必须等于 signed_checkpoint.key_id |

检查点签名载荷为 `{"task_id": task_id, "checkpoint": AuditCheckpoint}`，
SignedEnvelope.object_type 固定 `AuditCheckpoint`。把 task_id 放在外层是为了保持计划中
六个检查点字段不变，同时让签名绑定任务。

`CheckpointRecord = {task_id, checkpoint, signed_checkpoint}`。存储文件名为
`SM3(UTF8(checkpoint_id)).json`；同 ID 同内容保存幂等，冲突拒绝；写入用 fsync +
同文件系统原子硬链接，文件系统必须支持硬链接。目录必须由独立可信组件维护、Agent
无修改权限；路径不同本身不构成隔离。此版本不声称提供硬件密钥隔离或抗任意断电保证。

## 证据包与验证返回

```text
AuditBundle = {
  schema_version: "1.0",
  task_id: str,
  entries: ChainEntry[],
  objects: [{payload: object, envelope: SignedEnvelope}],
  checkpoint: AuditCheckpoint,
  signed_checkpoint: SignedEnvelope
}
VerificationResult = {
  valid: bool,
  verified_events: int,
  anchored_from_seq: int|null,
  anchored_to_seq: int|null,
  tail_complete: false,
  errors: [{code: str, message: str}]
}
```

验证器不自动信任包内公钥或包内检查点。调用方通过独立渠道指定 task_id、
trusted_checkpoint_id、可信公钥表与 CheckpointStore。包内检查点必须与外部锚点完全一致。
必须重新计算全部事件摘要及链，检查任务、序号、重复 ID、父事件、对象摘要和对象签名。

objects 支持 `TaskContractV2`、`ToolCallEnvelope`、`GatewayDecision`、`ToolResult`。
每个非 null 的 object_digest/result_digest 必须在 objects 中提供对应签名载荷，
引用值统一调用 `signed_object_digest(payload, envelope)`，其值为
`SM3(JCS({"payload": payload, "envelope": SignedEnvelope完整六字段}))`。
**这里不能填 envelope.payload_digest**：后者只绑定载荷，无法阻止替换同一载荷的另一份
合法签名并改变 object_type/key_id。完整对象摘要绑定类型、版本、key_id、载荷和签名；
同一载荷重新签名可能产生新的引用摘要，旧事件保留原始信封。
`FileEvidenceRecorder` 在同一任务、对象类型、版本、算法、key_id 与 JCS 载荷均相同时，
通过证据目录中的 SQLite 索引复用首次保存的信封，避免评估、复查和执行重复签名。
复用前重新读取文件并检查对象摘要、任务与签名；索引不是信任来源。事务协调同机并发
写入；已有对象文件及事件引用不改写，索引不是抗任意崩溃或断电的承诺。
不接受缺失、重复或未引用对象。若 P2 只导出日志、不导出关联对象，应将未提供的引用
标明为 null，且不能把此模式宣称为“已验证对象关联”；真实审计数据不可为了通过验证
临时删改这些引用。其他类型须通过可信端配置 allowed_object_types，再统一 schema。
验证不代替 P1 对契约是否当前有效、权限是否足够、版本是否过期的业务判断。

成功只表示指定检查点覆盖的前缀一致。它不能证明未锚定尾部未删除、日志来源诚实、
更晚检查点不存在、签名时间可信，也不能证明已经泄露的密钥在某历史时刻仍安全。
普通轮换保留历史公钥；泄露后从可信表移除该 key_id，历史信任需额外证据裁定。

## 错误约定

底层操作抛出 `CryptoError(code, message)`；验签不匹配返回 False。
verify_bundle 返回失败结果，不把普通坏输入变成未捕获异常。

- 输入/对象：CANONICALIZATION_INVALID、INPUT_INVALID、BUNDLE_INVALID、OBJECT_TYPE_INVALID。
- 密钥/签名：KEY_UNKNOWN、KEY_UNAVAILABLE、KEY_INVALID、KEY_MISMATCH、SIGNATURE_INVALID。
- 链：EVENT_INVALID、SEQUENCE_INVALID、TASK_MISMATCH、EVENT_ID_INVALID、PARENT_EVENT_MISSING、
  CHAIN_INVALID、CHAIN_HEAD_MISMATCH、CHECKPOINT_RANGE_INVALID。
- 锚点：ANCHOR_NOT_FOUND、ANCHOR_MISMATCH、ANCHOR_CONFLICT、CHECKPOINT_INVALID。
- 引用：OBJECT_MISSING、OBJECT_DUPLICATE、OBJECT_UNREFERENCED。
- 环境：CHECK_UNAVAILABLE、KEY_STORAGE_ERROR、INPUT_UNAVAILABLE、INPUT_TOO_LARGE。

网关映射到公共 `SIGNATURE_INVALID` 或 `CHECK_UNAVAILABLE` 并拒绝执行。
健康检查应确认 OpenSSL 可用，不能静默退回 FakeSignatureProvider。

## P1/P2 接入点与已知限制

1. P1 通过已有 signature_provider 构造参数注入真实提供方。原始字节的 sign_sm2 用于
   底层适配器合同；业务对象应通过 EnvelopeService，避免自己拼接签名输入。
2. P1 当前 ContractService 的 SHA-256/普通 JSON 摘要及 P1 InMemoryEventStore 的
   fake_chain_head 不能当作这里的 SM3 摘要。切换时统一新增 Core 对象的摘要语义，
   不把既有旧摘要重新标为 SM3；迁移由 P1/P2 联调完成。本 PR 不修改他们的路由和服务。
3. P2 append_event 后用同样的 JCS/SM3 规则保存链；导出结构采用 AuditBundle Schema，
   路由返回下载内容由 P2 实现。检查点应由可信组件签发、保存并给验证者分发 ID。
4. CLI 使用 OpenSSL 子进程，优先可移植正确性。高吞吐部署可以用同一接口接入原生绑定
   或隔离签名服务；不能把当前实现称为已通过商用密码产品认证。

## 验证与演示

前置：Python 3.11、uv、OpenSSL 3.x（Linux 安装 openssl；Windows 可使用 Git 附带版本，
或设置 AEGIS_OPENSSL 为绝对路径）。

```powershell
uv sync --project backend --group dev --frozen
uv run --project backend pytest tests/unit/crypto tests/contract/test_core_crypto_interfaces.py -q
uv run --project backend python -m ra_agent.audit --bundle tests/fixtures/core/crypto/bundle.json --keys tests/fixtures/core/crypto/public_keys.json --checkpoints tests/fixtures/core/crypto/trusted_checkpoints --checkpoint-id fixture-cp-1 --task-id fixture-task-1
uv run --project backend python scripts/demo_core_crypto.py --output .runtime/p3-demo
uv run --project backend python scripts/export_core_crypto_schemas.py
```

CLI：成功退出 0，无效或超量证据退出 1，信任配置/输入文件/环境不可用退出 2；
标准输出为 JSON，验证过程返回的 CHECK_UNAVAILABLE、KEY_UNAVAILABLE 等环境错误也退出 2。
导出、直接验证和 CLI 共用 `audit.integrity.MAX_AUDIT_BUNDLE_BYTES`：证据包上限 64 MiB，
按紧凑 UTF-8 JSON 计量；保存时可用 `audit.integrity.encode_bundle(bundle)`。
CLI 同时限制原始文件字节数，额外缩进或转义也计入该上限；公钥配置文件上限 1 MiB。
单个存储证据仍限 16 MiB，包内最多 100000 条事件及 100000 个对象。
超量导出在保存新检查点前失败；此版本只支持有界完整前缀包，尚无分卷协议。
固定 fixture 只包含公钥，无私钥。
demo 临时生成密钥并在结束时删除，不覆盖已有输出目录；demo 中的同机目录不是生产隔离。

Windows 中文路径的 Python 3.11 editable `.pth` 可能遇到 GBK/UTF-8 冲突。
可改用 `uv sync --project backend --group dev --frozen --no-editable` 安装，保持项目原目录。
后续 `uv run` 也加 `--no-editable`，避免 uv 再装回 editable；例如
`uv run --project backend --no-editable python -m ra_agent.audit --help`。
此方式已在新的 Python 3.11 虚拟环境中安装并完成固定样例离线验签。

## 参考

- [RFC 8785](https://www.rfc-editor.org/rfc/rfc8785.html)：规范化规则。
- [rfc8785.py](https://github.com/trailofbits/rfc8785.py)：实际 JCS 依赖。
- [OpenSSL SM2 pkeyutl](https://docs.openssl.org/3.0/man1/openssl-pkeyutl/)：rawin、SM3、distid。
- [OpenSSL 标准向量](https://github.com/openssl/openssl/blob/master/test/sm2_internal_test.c)：
  GM/T 0003.5-2012 / GB/T 32918.5-2016 附录 A 固定签名。测试同时覆盖 OpenSSL dgst 路径，
  后者是同一密码库的不同入口，不声称是独立密码库交叉验证。

## 第二轮真实联调

联调分支保持第一轮公开字段不变，并补上真实数据流：

```text
ToolGateway
  -> FileEvidenceRecorder: SM2 签 ToolCallEnvelope / GatewayDecision / ToolResult
  -> SqliteEventStore: 在同次事务中保存完整 BehaviorEvent、序号和 P3 ChainEntry
  -> AuditExportService: 重建并比对 SM3 链头，选取事件实际引用的签名对象
  -> FileCheckpointStore: 保存完整前缀的签名检查点
  -> AuditVerifier: 仅用可信公钥、外部检查点和证据包离线验证
```

Gateway 的评估事件使用 `object_digest` 引用签名的 `ToolCallEnvelope`，使用
`result_digest` 引用签名的 `GatewayDecision`。执行事件分别引用
`ToolCallEnvelope` 和 `ToolResult`。决定签名载荷为
`{task_id, request_id, decision, reason_code, evidence_refs, versions, confirmation_id}`；
工具结果载荷为 `{task_id, request_id, result}`。这些载荷都先走 JCS，再由现有六字段
`SignedEnvelope` 保护；没有修改冻结模型。

`AuditExportService.export_task(task_id=..., checkpoint_id=...)` 是 P2 EventStore 到
P3 AuditVerifier 的真实桥。它拒绝空日志、无密码链的存储、重建链头不一致、缺失签名对象
和无效签名。导出只包含事件实际引用的对象，证据目录中遗留但未引用的历史对象不会进入包。

应用默认保持 `CORE_CRYPTO_MODE=fake`，兼容第一轮测试。真实模式必须显式设置：

```text
CORE_CRYPTO_MODE=sm2
CORE_SM2_KEY_ID=<受信 key_id>
CORE_SM2_PUBLIC_KEYS_PATH=<key_id 到 SPKI PEM 的 JSON 文件>
CORE_SM2_PRIVATE_KEY_PATH=<部署方控制的 SM2 私钥文件>
CORE_EVENT_LOG_PATH=<旧 P2 JSONL 路径；当前数据库采用同路径 .sqlite3 后缀>
CORE_EVIDENCE_ROOT=<不可由 Agent 选择的签名对象目录>
CORE_AUDIT_CHECKPOINT_ROOT=<独立检查点目录>
```

任何密钥文件缺失、key_id 不在可信公钥表、私钥与公钥不匹配或 OpenSSL 3 不可用都会让
SM2 模式启动失败，不会回退到 FakeSignatureProvider。当前单进程版本仍把签名器放在后端
进程内；决赛部署应把同一 `SignatureProvider` 接口接到独立账户、签名服务或 HSM。当前
执行器在产生结果后才能签 ToolResult，若外部工具已经产生不可逆副作用而证据存储随后失败，
Core 只能报告失败；待提交/提交协议属于后续 Aegis Cap，不能把本版本描述为任意故障下的
恰好一次执行。
