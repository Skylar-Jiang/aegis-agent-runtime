# Phase 3.5 Baseline Audit

Audited from `main` commit `a8a354b` on 2026-07-23 before Phase 3.5 changes.

## Verification baseline

`backend/.venv/Scripts/python.exe scripts/check.py` completed successfully after
installing the locked backend and frontend dependencies:

- Ruff: passed.
- Pyright: `0 errors, 0 warnings, 0 informations`.
- Pytest: `715 passed, 7 skipped, 2 warnings` in 30.97 seconds.
- Frontend: ESLint, TypeScript, 2 Vitest tests, and Vite production build passed.

The persistent-audit concurrency test previously hung because it used two
independent recorders against one `sqlite:///:memory:` connection.  The live
runtime uses a file SQLite database, where the same atomic-upsert test completes
with unique sequences.  The test now uses pytest's temporary file database and
a five-second bound so a future regression fails rather than hanging the gate.

## Implemented and wired

- The scheduler is the only runtime entry point to a `ToolExecutor`, and applies
  risk classification, policy and permission checks before selecting a flow.
- Sandbox execution has filesystem checkpoints, pending artifacts, deep safety
  checking, commit and rollback implementations.
- Live file tools (`list_dir`, `read_file`, `write_file`, `delete_file`) are
  registered in `core/bootstrap.py` and use a safe workspace resolver.
- Rule-based risk, policy, permission, deep-safety and persistent audit services
  are assembled outside offline mode.

## Baseline gaps closed in Phase 3.5

The Pre/Post checkers, live download/Memory/Shell handlers and controlled cleanup
are now wired. Fast flow applies Pre/Post checks; sandbox handles DeepCheck and
Commit/Rollback. `TaskGraphRunner` replaces the prior no-task-graph baseline for
the required minimal dependency and bounded-parallel execution scope.

## Mock versus real boundary

- `OFFLINE` mode deliberately composes mock handlers, executor, checkpoint,
  checker and approval services.
- `RULES_ONLY` composes persistent/rule services but does not register real live
  tool handlers.
- `LIVE_AGENT` uses filesystem, download quarantine, Memory lifecycle, restricted
  Shell and dry-run egress handlers.
- The experiment runner now creates isolated real containers; only Baseline
  intentionally bypasses runtime checkers for comparison.

## Documentation and test gaps

- README, architecture and Phase 3 reports describe pre/post, live tools and
  experiments as planned or partial rather than an end-to-end capability.
- Existing browser E2E covers UI only; it does not prove the full live runtime
  tool chain, fault handling, approval resume or idempotent effects.
- There are no task-contract/intent-boundary, data-lineage/egress or task-graph
  contracts and no tests for their required fail-closed behavior.
