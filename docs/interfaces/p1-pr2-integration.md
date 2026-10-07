# P1 PR2 cross-module integration review

This document records the interface audit performed after P1/P2/P3 PR1 were merged.

## Interface consistency verdict

### P2 BehaviorEvent / JsonlEventStore

The merged implementation matches the P2 handoff:

```python
JsonlEventStore(path: pathlib.Path, *, chain_factory=None)
await store.append_event(event: dict) -> dict
await store.list_task_events(task_id: str) -> list[dict]
await store.get_chain_head(task_id: str) -> str | None
```

`ToolGateway` now supplies every required event key before append: `event_id`, `task_id`, `parent_event_id`, `type`, `actor`, `source_ref`, `object_digest`, `state`, `decision`, `result_digest`, `occurred_at`. It also supplies P1 correlation fields `request_id`, `reason_code`, `evidence_refs`. `sequence` is intentionally omitted so P2 assigns it atomically per task. `parent_event_id` points to the immediately preceding stored event for the task.

P2's structural `chain_factory` protocol is compatible with P3 `HashChain`; runtime construction uses `JsonlEventStore(..., chain_factory=HashChain)`.

### P3 crypto/audit

The merged implementation matches the P3 handoff:

```python
SignatureProvider.sign_sm2(payload: bytes, *, key_id: str) -> str
SignatureProvider.verify_sm2(payload: bytes, signature: str, *, key_id: str) -> bool
SignatureProvider.list_public_keys() -> dict[str, str]

AuditVerifier.verify_bundle(
    bundle: dict,
    *,
    trusted_checkpoint_id: str,
    task_id: str,
) -> VerificationResult
```

`SignedEnvelope` remains the six-field v1 object and `AuditCheckpoint` remains the six-field v1 object. P1 does not build a second signature format. Gateway signed evidence is delegated through P3 `FileEvidenceRecorder -> EnvelopeService -> SignatureProvider`.

### One semantic mismatch corrected in PR2

PR1 could use `ContractVersionRef.digest` (the existing contract SHA-256) as an event object reference in fake wiring. P3 explicitly defines `object_digest/result_digest` as the SM3 digest of the complete signed `{payload,envelope}` object. PR2 therefore:

- retains `contract_ref.digest` only in `evidence_refs`;
- writes real P3 signed-object digests to `object_digest/result_digest` in SM2 mode;
- writes null when signed evidence is unavailable, rather than inventing a cryptographic binding.

No public model field was renamed to make this correction.

## P2/P3 completion audit at merge time

### P2 PR1

Complete for its declared first-round scope: strict `BehaviorEvent`, `JsonlEventStore`, OpenAPI/schema artifacts, fixtures, shared EventStore contract tests and `/core` mock UI exist. P2's own verification record reports its PR1 validation as passing.

Not complete for the *second-round integration* before this PR: P2 documentation explicitly states that the production event-query route, evidence export and real Gateway UI were PR2 targets. This PR implements the real task-events route and connects `/core` to the real Gateway while retaining the fixed mock tab.

### P3 crypto/audit scope

The merged tree contains the required JCS canonicalizer, SM3 digest provider, real OpenSSL SM2 provider/key-id handling, six-field `SignedEnvelope`, `HashChain`, checkpoint store, signed evidence recorder/exporter and independent `AuditVerifier`. P3's handoff records dedicated real integration tests and offline verification. The remaining work at merge time was cross-module application wiring, which this PR performs rather than duplicating P3 primitives.

## PR2 ownership boundary

P1 owns the wiring and safety state machine: Gateway, confirmation/replan, TOCTOU recheck, adapter boundary and API routes. P2 remains owner of BehaviorEvent/EventStore semantics and UI/evidence presentation. P3 remains owner of canonicalization, SM2/SM3, signed evidence, checkpoint and verifier semantics.
