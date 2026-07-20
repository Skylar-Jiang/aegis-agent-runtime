# Phase 3 Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Freeze Phase 3 collaboration boundaries without implementing the members' Phase 3 business modules.

**Architecture:** Preserve the Phase 2 scheduler as the only side-effect entry point. Add versioned, data-only public contracts and Protocols for the future pre/post checks, document ownership and integration order under `docs/phase3/`, and retain historical documents with explicit status rather than deleting uncertain material.

**Tech Stack:** Python 3.11, Pydantic v2, pytest, Ruff, Pyright, React/Vite documentation tooling.

---

### Task 1: Audit and normalize project documentation

**Files:**
- Create: `docs/phase3/07-cleanup-report.md`
- Modify: `README.md`, `docs/00-scope.md`, `docs/01-architecture.md`, `docs/12-module-ownership.md`, `docs/FINAL-INTEGRATION-REPORT.md`, `docs/SHARED-BASELINE-REPORT.md`

- [ ] Record every legacy-document candidate and its KEEP/UPDATE/MERGE_DELETE/DELETE evidence.
- [ ] Mark historical Phase 1 reports as historical and point to the Phase 3 source of truth.
- [ ] Replace obsolete claims about missing persistence with the current Phase 2 boundary.

### Task 2: Freeze Contract v0.3

**Files:**
- Modify: `backend/src/ra_agent/contracts/enums.py`, `tools.py`, `execution.py`, `tasks.py`, `__init__.py`
- Create: `backend/src/ra_agent/security/pre_post_check.py`
- Modify: `backend/src/ra_agent/security/__init__.py`, `tests/contract/test_contracts.py`, `tests/contract/test_enums.py`

- [ ] Extend the contract tests with `PreCheckResult`, `PostCheckResult`, memory/experiment enums and dependency-aware `TaskStep` expectations; run them to establish the v0.2 failure.
- [ ] Add the minimal additive v0.3 models, enums, exports, checker Protocols and deterministic mocks.
- [ ] Run contract tests, Ruff and Pyright; do not call handlers or alter scheduler control flow.

### Task 3: Publish parallel handoffs

**Files:**
- Create: `docs/phase3/README.md`, `01-contract-v0.3.md`, `02-member-a-runtime.md`, `03-member-b-security.md`, `04-member-c-execution.md`, `05-member-d-experiments-ui.md`, `06-integration-checklist.md`

- [ ] Define Phase 3 scope, six demos, branches, ownership and merge order.
- [ ] Record contract semantics, state flows, cancellation, idempotency and group-lead-only files.
- [ ] Give each member exact allowed/prohibited paths, interfaces, tests and completion criteria.

### Task 4: Validate and integrate

**Files:** all changed files

- [ ] Search for stale phase claims and broken Markdown links.
- [ ] Run backend pytest, Ruff, Pyright, frontend lint/typecheck/tests/build and `scripts/check.py`.
- [ ] Commit with `chore(phase3): freeze interfaces and clean legacy handovers`; merge and push only after all required checks pass.
