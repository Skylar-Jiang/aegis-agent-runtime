# Phase 3.5 Integration Report

Frozen on 2026-07-23 from branch `codex/phase3-5-real-integration`.

## Delivered runtime path

`Risk → Policy → Permission → PreCheck → Controlled Execution → PostCheck → DeepCheck → Commit/Rollback → Audit` is wired in `live-agent`. Pre/Post errors and rejections fail closed. Controlled file writes clean their pending record after a successful commit; failure, cancellation and rollback use the cleanup coordinator.

Live handlers are registered for controlled files, `download_url`, `memory_read`, `memory_write`, `run_shell`, and `send_email_dry_run`. Downloads remain quarantined; Memory is trusted only after checking; Shell remains allowlisted; dry-run egress returns `PENDING_EGRESS` and never sends mail.

`TaskContract` is required for Live Runtime requests. The IntentBoundaryGuard blocks missing contracts, unapproved actions/resources, bulk expansion, forbidden actions, unconfirmed mutations and disallowed egress. Data lineage records owner, sensitivity, source and permitted recipients; the egress guard checks the recipient again.

The minimal `TaskGraphRunner` supports dependency ordering, bounded parallel independent nodes, conditional routing, explicit request inputs/outputs, descendant blocking and independent progress while another branch waits for approval. It delegates every node to the Runtime Scheduler.

## Real E2E and experiments

`tests/e2e/test_live_runtime.py` exercises an Agent or complete Live container: safe file commit with empty pending directory, mocked-network download quarantine, dangerous shell block before process execution, Memory-poisoning rejection without trusted/pending residue, idempotent retry, approved-delete recovery, and `PENDING_EGRESS` dry run. Unit/integration coverage additionally proves fault rollback behavior.

`experiments/runners/run_experiment.py --mode all` creates a disposable runtime directory for every case and records status, tool execution, check count, temporary-artifact count, rollback count, approval count and elapsed time. It does not call an LLM or public network, so token data is explicitly `N/A`.

The executed comparison used six cases per mode (18 total): Baseline directly invoked real handlers in an isolated directory; Full Guard and Adaptive Runtime used real Live containers. The safe write committed in all modes; Full Guard/Adaptive recorded three checks for it and left zero pending/quarantine temporary artifacts. Sensitive reads and deletes requested approval; dangerous shell was blocked before execution. Baseline intentionally bypasses runtime checkers for comparison, but the real handler still rejects sensitive paths and non-allowlisted shell commands.

## Verification

Executed from the isolated worktree on 2026-07-23:

```powershell
backend/.venv/Scripts/python.exe scripts/check.py
```

Result: Ruff passed; Pyright reported `0 errors, 0 warnings`; pytest reported `734 passed, 7 skipped, 1 warning`; frontend ESLint, TypeScript, 2 Vitest tests and the Vite production build passed.

## Known limits

- `send_email_dry_run` is deliberately not an email transport; it never sends network traffic.
- The experiment runner is deterministic and reports no LLM/token metrics by design.
- TaskGraph is an in-process minimal scheduler, not durable distributed workflow orchestration.
