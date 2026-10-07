# Aegis Core v1 — PR1 public interface baseline

This file freezes the public names used by P1/P2/P3 during the first Core integration round. Internal helper names are not part of this contract.

## Public models owned by P1

### TaskContractV2

`contract_id`, `session_id`, `task_id`, `user_id`, `version`, `goals`, `completion_criteria`, `allowed`, `denied`, `limits`, `confirmation`, `policy_version`, `tool_manifest_digest`, `parent_digest`.

Reserved optional fields: `prepared_effect_id`, `effect_descriptor_digest`, `result_commitment`, `commit_epoch`, `execution_receipt_id`, `compliance_proof_ref`.

### ContractVersionRef

`contract_id: str`, `version: int`, `digest: str`, `status: DRAFT|CONFIRMED|SUPERSEDED`, `confirmed_by: str | null`, `confirmed_at: datetime | null`.

### PermissionGrant

`subject`, `skill`, `tool`, `action`, `resource`, `effect`, `scope`, `expires_at`, `source`, optional numeric `limits`, plus `scope_ref` for SESSION/TASK/ONE_SHOT binding.

`GrantScope = GLOBAL | SESSION | TASK | ONE_SHOT`.

For every numeric limit key, effective permission uses the smallest value contributed by the matching grants and `TaskContractV2.limits`. Missing keys mean that layer adds no additional numeric ceiling.

### EffectivePermission

Core fields: `allowed`, `denied`, `constraints`, `matched_sources`, `conflict_reason`.

PR1 also exposes `conflict_code` and `requires_confirmation` so ToolGateway can make deterministic decisions without parsing human-readable text.

### ToolCallEnvelope

`request_id`, `task_id`, `session_id`, `contract_ref`, `skill_ref`, `tool`, `action`, `canonical_args`, `resource`, `effect_class`.

### GatewayDecision

`decision`, `reason_code`, `evidence_refs`, `versions`, `confirmation_id`.

`decision = ALLOW | DENY | REQUIRE_CONFIRMATION | REQUIRE_REPLAN`.

Minimum reason codes from the Core plan are implemented: `OUT_OF_CONTRACT`, `SKILL_LIMIT`, `SYSTEM_DENY`, `RESOURCE_MISMATCH`, `VERSION_STALE`, `CONFIRMATION_REQUIRED`, `CHECK_UNAVAILABLE`, `ADAPTER_BYPASS`, `SIGNATURE_INVALID`. PR1 additionally uses `ALLOWED` and `USER_DENY`.

## Public services owned by P1

`ContractService.create_contract`, `update_contract`, `confirm_contract`, `get_active_contract`.

`PermissionResolver.resolve_effective_permission`, `explain_conflict`.

`ToolGateway.evaluate`, `execute`, `resume_after_confirmation`.

`resume_after_confirmation` is deliberately a PR1 substitute: it accepts a boolean confirmation result and then re-runs contract/version/permission checks. PR2 replaces this with the durable ConfirmationService flow.

`execute` re-runs the same checks immediately before calling the side-effect adapter, so expiry or in-memory permission changes after the first evaluation fail closed. PR2 replaces the stored permission snapshot with durable current-state lookup.

## PR1 substitute dependencies

`InMemoryEventStore` exposes `append_event`, `list_task_events`, `get_chain_head`.

`FakeSignatureProvider` exposes `sign_sm2`, `verify_sm2`, `list_public_keys`. It is a deterministic test double and **is not SM2 cryptography**. P3 replaces it in PR2 without changing method names.

## API baseline

- `GET /api/v1/health`
- `POST /api/v1/sessions`
- `POST /api/v1/sessions/{session_id}/tasks`
- `POST /api/v1/contracts`
- `POST /api/v1/contracts/{contract_id}/versions`
- `POST /api/v1/contracts/{contract_id}/confirm`
- `GET /api/v1/permissions/effective?request_id=...`
- `POST /api/v1/tool-calls/evaluate`
- `POST /api/v1/tool-calls/{request_id}/execute`

`POST /api/v1/contracts/{contract_id}/confirm` requires both the reviewed `version` and `confirmed_by`; the server never substitutes the newest version.

The PR1 execute path uses `DryRunToolExecutor`; real side-effect adapters remain PR2 work.

## PR2 integration status

PR2 keeps the PR1 public field names and service signatures, then replaces the temporary wiring:

- `SqliteEventStore` is the runtime EventStore in both fake and SM2 modes after the hardening update. The original `JsonlEventStore(..., chain_factory=HashChain)` remains supported for callers and fixtures. The standard chained legacy file is verified and imported once; it is retained unchanged. The three-method public EventStore protocol and event bytes are unchanged.
- `ConfirmationService.request_confirmation/resolve_confirmation` owns the real `WAITING_CONFIRMATION -> CONFIRMED|REJECTED` lifecycle. `resume_after_confirmation` remains only as a backwards-compatible delegate.
- `CORE_CRYPTO_MODE=sm2` wires P3 `OpenSSLSignatureProvider`, `EnvelopeService`, `FileEvidenceRecorder`, `AuditExportService` and `AuditVerifier`. Missing trusted keys fail startup closed.
- `CoreToolExecutor` provides real filesystem, Memory, simulated-egress and restricted-process adapters; every adapter call requires the opaque token held by `ToolGateway`.
- `ToolGateway.execute` re-runs active contract/version, permission context and confirmation binding immediately before the side effect. A newer confirmed contract returns `VERSION_STALE/REQUIRE_REPLAN`; a tightened permission layer returns DENY.
- `ToolGateway.replan` creates a new DRAFT contract version and blocks the old request. The new version must be explicitly confirmed before a new request can execute.

Additional PR2 API endpoints:

- `GET /api/v1/confirmations/{confirmation_id}`
- `POST /api/v1/confirmations/{confirmation_id}/resolve`
- `POST /api/v1/tool-calls/{request_id}/replan`
- `GET /api/v1/tasks/{task_id}/events`
- `POST /api/v1/audit/export`
- `POST /api/v1/audit/verify`

The contract SHA-256 stored in `ContractVersionRef.digest` is retained only as a contract/version reference. It is **not** used as P3 `object_digest`. In SM2 mode `object_digest/result_digest` are P3 signed-object digests; when signed evidence is unavailable those fields stay null rather than being given a misleading substitute digest.

## Hardening update (C1–C7, unchanged wire schemas)

`ToolEvaluationRequest.permissions` is now an optional ceiling on server-owned authority at the HTTP boundary, never a source of greater authority. Three empty grant arrays select the trusted server defaults; nonempty caller grants must independently pass the existing resolver. Trusted inputs are the current session SecurityProfile, `configs/core_skills.json`, system rules and confirmed contract. Unknown Skills fail closed. Actual tool/action/effect/resource must agree; filesystem aliases that rename the authorized resource through a symlink/junction are rejected.

Internal optional `store`, `request_validator`, and `permission_provider` constructor hooks retain the existing service methods. Runtime wiring uses `CoreStateStore` for contracts, confirmations, sessions and requests. All cooperating instances must share its configured file. A complete envelope fingerprint binds each request ID; a different payload under the same ID is refused. A durable execution claim prevents concurrent automatic re-execution. Successful retries reuse the recorded result; `UNKNOWN` outcomes require reconciliation and are not automatically retried. This is not arbitrary-fault exactly-once.

`core_event_log_path` continues to identify the old JSONL source. The live event DB is the same path with suffix `.sqlite3`; default state is `.state.sqlite3` unless `CORE_STATE_PATH` is specified. The optional internal `get_last_event_id` optimization is not a new required EventStore method. Append and head reads check the tail; listing/export verifies the complete requested task. An external trusted checkpoint is still needed to detect replacement of anchored history.

The health endpoint adds deployment limits and reports the actual EventStore class. No frozen model, signature input, schema or fixture is renamed. See [validation and migration notes](../status/CORE_HARDENING_VALIDATION.md) before reverting a deployed SQLite event store.

## September 26 compatible additions

- `GatewayDecision.versions.policy` is the actual trusted policy snapshot used to resolve permissions. When the trusted provider supplies provenance, `versions.contract_policy` preserves the version bound into the contract. The existing `versions` mapping remains unchanged in type.
- Reconfirming the same version by the same confirmer returns its original immutable confirmation reference. A different confirmer or superseded version is rejected. Retries reconcile interrupted confirmation audit recording without resetting completed task state.
- Concrete event stores optionally expose `get_task_snapshot(task_id) -> (events, head)` for coherent export. This is not a fourth required EventStore method. Business parent references may name any earlier event in the same task; they do not have to name the previous chain entry.
- `GET /api/v1/tasks/{task_id}/snapshot` returns `{task: SessionTaskDraftV1, latest_request: {envelope, evaluation, execution_state, execution_result, confirmation_id} | null}` inside `APIResponse.data`. It does not return permission credentials or execute a request. A request for an older/different contract is omitted. `CLAIMED`, `ADMITTED`, and `UNKNOWN` must never be presented as automatically retryable execution.
- `GET /api/v1/experiments/core` returns recorded file data with IDs, source paths, timestamps and SHA-256 file hashes. These are historical measurements, not live performance counters; missing files are reported in `unavailable`.
- Graph snapshots add optional `recovery_failures: Record<request_id, reason>`. `POST /api/task-graphs/{graph_id}/retry-recovery` explicitly retries failures after checking durable effects. It operates on a graph registered in the current process; restarting the server does not recreate the graph scheduler.
- `MAX_BACKUP_BYTES` optionally configures the per-file checkpoint budget; the default follows `MAX_READ_BYTES`. The Core health limits include its effective value. Existing larger backups require an adequate configured budget before recovery.
- Retrying export with an existing `checkpoint_id` returns that checkpoint's original anchored prefix, including after lawful history growth. Use a new ID to anchor newer events. A conflicting task/history still fails closed; the current stored task history is verified before selecting the prefix.
- Event listing and audit export return structured `detail: {code, message}` for event-store failures: invalid evidence is HTTP 409; storage/check unavailability is HTTP 503. A corrupt log is never returned as an empty successful history.
- Event/evidence/checkpoint writes drain their worker on cancellation. Manual, automatic and cancellation recovery share a per-graph lock and reconcile durable effect status before a second explicit recovery attempt.
