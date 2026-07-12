# Runtime Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Freeze Contract v0.2 and shared runtime dependencies, then implement safe FAST_EXECUTE and BLOCK scheduling paths.

**Architecture:** Keep contracts as the only shared model source. Make the scheduler depend on trusted tool metadata and a recorder that owns audit identity and ordering; unsupported policies return structured failures and never reach the executor.

**Tech Stack:** Python 3.11, Pydantic v2, asyncio, pytest, Ruff, Pyright.

---

### Task 1: Contract v0.2

**Files:**
- Modify: `backend/src/ra_agent/contracts/tools.py`
- Modify: `backend/src/ra_agent/contracts/risk.py`
- Modify: `backend/src/ra_agent/contracts/execution.py`
- Modify: `backend/src/ra_agent/contracts/__init__.py`
- Test: `tests/contract/test_contracts.py`

- [ ] Add failing tests for required request context, safe collection defaults, multi-permission results, risk metadata, deep-check signals, and execution lifecycle fields.
- [ ] Run `pytest tests/contract/test_contracts.py -v` and confirm failures describe missing v0.2 fields.
- [ ] Add the minimum Pydantic fields and exports needed by those tests.
- [ ] Re-run the contract tests and confirm they pass.

### Task 2: AuditRecorder

**Files:**
- Modify: `backend/src/ra_agent/audit/logger.py`
- Modify: `backend/src/ra_agent/audit/event_bus.py`
- Modify: `backend/src/ra_agent/audit/__init__.py`
- Test: `tests/unit/test_audit.py`

- [ ] Add failing tests for recorder-owned IDs/timestamps, per-task monotonic sequences across requests, independent task sequences, and recursive redaction.
- [ ] Run `pytest tests/unit/test_audit.py -v` and confirm the recorder API is missing.
- [ ] Implement one in-memory recorder with a single `record` method and recursive redaction.
- [ ] Re-run audit tests and confirm they pass.

### Task 3: Shared bootstrap and fixtures

**Files:**
- Create: `backend/src/ra_agent/core/container.py`
- Create: `backend/src/ra_agent/core/bootstrap.py`
- Modify: `backend/src/ra_agent/tools/specs.py`
- Modify: `backend/src/ra_agent/main.py`
- Create: `tests/fixtures/*.json`
- Test: `tests/contract/test_contracts.py`

- [ ] Add failing tests that parse all four request fixtures and build a mock container without external credentials.
- [ ] Define trusted tool specs, `ServiceContainer`, and one safe mock bootstrap path.
- [ ] Attach the shared container to the FastAPI application and re-run health/contract tests.

### Task 4: Runtime FAST_EXECUTE and BLOCK

**Files:**
- Modify: `backend/src/ra_agent/runtime/scheduler.py`
- Modify: `backend/src/ra_agent/runtime/state_machine.py`
- Create: `tests/integration/test_runtime_low_and_block.py`

- [ ] Add failing integration tests for executor call counts, final states, audit event sets/order, multi-request sequences, unsupported policy safety, failures, and invalid transitions.
- [ ] Implement trusted tool validation, state-machine transitions, FAST_EXECUTE, BLOCK, and structured unsupported-policy handling.
- [ ] Re-run runtime integration tests, then all backend tests.

### Task 5: Freeze public documentation and verify

**Files:**
- Modify: `docs/03-contracts.md`
- Modify: `docs/04-state-machine.md`
- Modify: `docs/09-event-schema.md`
- Create: `docs/12-module-ownership.md`

- [ ] Document Contract v0.2, recorder ownership, valid direct blocking, module ownership, and the contract change process.
- [ ] Run `python scripts/check.py`; if frontend tooling is unavailable, separately run backend Ruff, Pyright, and pytest and report the environment limitation exactly.
- [ ] Inspect `git diff --check` and `git status --short` before reporting completion.
