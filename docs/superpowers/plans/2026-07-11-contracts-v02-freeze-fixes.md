# Contracts v0.2 Freeze Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove every contracts-v0.2 freeze blocker and standardize the repository on Node.js 24.14.0 with pnpm 10.12.4.

**Architecture:** Normalize all public datetimes through one shared Contract type; coordinate request execution through a replaceable registry injected by `ServiceContainer`; keep `AuditRecorder.record` as the only public audit write API. Make environment checks cross-platform by resolving executables and routing trusted Windows batch commands through `COMSPEC` without general `shell=True`.

**Tech Stack:** Python 3.11, Pydantic v2, asyncio/concurrent futures, pytest, Node.js 24.14.0, pnpm 10.12.4, React/Vite.

**Repository rule:** Do not commit or push during this task.

---

### Task 1: Node 24 and Windows command preflight

**Files:**
- Modify: `.nvmrc`
- Create: `.npmrc`
- Modify: `frontend/package.json`
- Modify: `.github/workflows/ci.yml`
- Modify: `scripts/check.py`
- Create: `scripts/use-node.ps1`
- Modify: `tests/unit/test_ci_workflow.py`
- Create: `tests/unit/test_check_script.py`

- [x] Add tests requiring Node `24.14.0`, engine `>=24.14.0 <25`, pnpm `10.12.4`, engine-strict, Windows `.cmd/.bat` resolution, and version rejection.
- [x] Run the new tests and confirm they fail against Node 22 metadata and the direct `corepack` subprocess call.
- [x] Implement the smallest cross-platform command resolver and environment preflight; update repository version files and CI.
- [x] Re-run the focused tests until green.

### Task 2: UTC-normalized public Contracts and one audit API

**Files:**
- Modify: `backend/src/ra_agent/contracts/common.py`
- Modify: `backend/src/ra_agent/contracts/tools.py`
- Modify: `backend/src/ra_agent/contracts/tasks.py`
- Modify: `backend/src/ra_agent/contracts/execution.py`
- Modify: `backend/src/ra_agent/contracts/approvals.py`
- Modify: `backend/src/ra_agent/contracts/audit.py`
- Modify: `backend/src/ra_agent/audit/event_bus.py`
- Modify: `backend/src/ra_agent/audit/__init__.py`
- Modify: `tests/contract/test_contracts.py`
- Modify: `tests/unit/test_audit.py`

- [x] Add failing tests for UTC preservation, +08:00 normalization, naive rejection, full JSON serialization, concurrent audit sequences, and removal of public `emit` APIs.
- [x] Define one reusable `UTCDateTime` Contract type and use it for every public time field.
- [x] Remove `AuditEventSink`, `InMemoryAuditEventSink`, and `emit`; retain recorder locking and recursive redaction.
- [x] Re-run Contract and audit tests until green.

### Task 3: Replaceable request-id idempotency registry

**Files:**
- Create: `backend/src/ra_agent/runtime/idempotency.py`
- Modify: `backend/src/ra_agent/runtime/scheduler.py`
- Modify: `backend/src/ra_agent/runtime/__init__.py`
- Modify: `backend/src/ra_agent/core/container.py`
- Modify: `backend/src/ra_agent/core/bootstrap.py`
- Modify: `tests/integration/test_runtime_low_and_block.py`
- Modify: `tests/integration/test_mock_runtime.py`

- [x] Add failing tests for serial retry, concurrent retry, semantic conflict, independent IDs, cached failures, and requested-at-insensitive fingerprints.
- [x] Implement an atomic thread-safe registry using a lock plus shared Future results; exclude `requested_at` from the canonical semantic fingerprint.
- [x] Claim before producing execution audit events; waiters return the published result, conflicts return `REQUEST_ID_CONFLICT` and audit `STEP_FAILED`.
- [x] Re-run runtime integration tests until green.

### Task 4: Conservative permission consistency

**Files:**
- Modify: `backend/src/ra_agent/runtime/scheduler.py`
- Modify: `tests/integration/test_runtime_low_and_block.py`

- [x] Add a failing permission matrix covering GRANTED, NOT_REQUIRED, DENIED, PENDING, EXPIRED, missing, extra, duplicate, mismatched IDs, approval required, and contradictory aggregate flags.
- [x] Validate correlation and exact permission coverage before execution; treat every inconsistency as a structured conservative block with audit evidence.
- [x] Re-run the permission matrix and existing LOW/BLOCK tests until green.

### Task 5: Freeze documentation and verify

**Files:**
- Modify: `README.md`
- Modify: `docs/02-tech-stack.md`
- Modify: `docs/03-contracts.md`
- Modify: `docs/04-state-machine.md`
- Modify: `docs/07-execution-semantics.md`
- Modify: `docs/09-event-schema.md`
- Modify: `docs/11-development-guide.md`
- Modify: `docs/PHASE-0-FREEZE-REPORT.md`
- Modify: `docs/superpowers/specs/2026-07-10-phase-0-freeze-design.md`
- Modify: `docs/superpowers/plans/2026-07-10-phase-0-freeze.md`
- Modify: `backend/src/ra_agent/main.py`

- [x] Document Node 24.14.0/pnpm 10.12.4, Phase 1 capability boundaries, idempotency, UTC, the single audit API, and distinct LOW/MEDIUM COMMITTED semantics.
- [x] Run format, lint, type checking, all tests, frontend checks, and `python scripts/check.py`.
- [x] Search the full repository for stale Node 22 project requirements; retain only third-party lockfile engine metadata and explain it.
- [x] Inspect the final diff for scope, race safety, mock boundaries, and Git-history preservation.
