# Aegis Core interface mapping — PR1

The existing Runtime Base remains runnable while Core v1 is introduced. PR1 avoids deleting or renaming existing v0.4 Runtime interfaces; it adds a versioned `/api/v1` Core boundary and documents the transition.

| Core plan interface | Existing Runtime Base | PR1 implementation / mapping |
| --- | --- | --- |
| `TaskContractV2` | `ra_agent.contracts.TaskContract` | New `ra_agent.contracts.core_v1.TaskContractV2`. Legacy `TaskContract` stays only for the existing Runtime main chain during PR1. |
| `ContractService` | Task contract stored inside `TaskStore` snapshots | New storage-agnostic in-memory version service. PR2 may move storage behind a Repository. |
| `PermissionResolver` | `RuleBasedPermissionGate` + Runtime permission checks | New four-layer Core intersection resolver. Existing gate is not removed in PR1. |
| `ToolGateway` | RuntimeScheduler + IntentBoundaryGuard + PermissionGate | New pre-side-effect Core gateway foundation. PR2 wires real adapters into it. |
| `ConfirmationService` | `ApprovalService` | Not replaced in PR1. `ToolGateway.resume_after_confirmation` is a temporary re-check substitute; durable integration is PR2. |
| `EventStore` | AuditRecorder / AuditRepository | P2-owned final interface. PR1 uses `InMemoryEventStore` with the frozen method names. |
| `SignatureProvider` | none | P3-owned final implementation. PR1 uses `FakeSignatureProvider`, explicitly non-cryptographic. |
| `session_id` | `Conversation.conversation_id` | `/api/v1/sessions` reuses the existing ConversationStore and uses a `session-*` identifier. |

## Integration rule

### P3 crypto/audit implementation

The plan's `aegis/crypto/` maps to `backend/src/ra_agent/crypto/`; `aegis/audit/`
maps to the existing `backend/src/ra_agent/audit/` package. Runtime Base's audit recorders
remain available. P3 adds `HashChain`, `FileCheckpointStore`, `AuditVerifier`, and
`AuditCheckpoint` without introducing a second BehaviorEvent or EventStore model.
`SignatureProvider` retains its unique Protocol definition in `gateway/interfaces.py`;
the real implementation is `crypto.OpenSSLSignatureProvider`. The complete encoding,
method parameters, schema and fixture contract is in [crypto_audit_v1.md](../interfaces/crypto_audit_v1.md).
Frozen JSON Schemas are in `docs/interfaces/schemas/`; public-only fixtures are in
`tests/fixtures/core/crypto/`. P1/P2 must review this wire-format detail before integration.

New Core work should depend on the public objects in `contracts/core_v1.py`, not on internal helper variables. Existing v0.4 runtime code is left intact until the second PR replaces fake dependencies and performs real cross-module wiring.

### P1 Gateway to P2 EventStore contract

The integration branch keeps the three-method `EventStore` Protocol unchanged. `ToolGateway`
now emits every required `BehaviorEvent` field (`parent_event_id`, `actor`, `source_ref`,
`object_digest`, and `result_digest` included), so P2 `JsonlEventStore` can validate and
persist Gateway events directly. The store still assigns `sequence`; P3 `HashChain` hashes
the normalized complete event returned by P2. The legacy in-memory store remains usable for
PR1 tests, but its `fake_chain_head` is never exported as P3 evidence.
