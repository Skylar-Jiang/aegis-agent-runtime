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

## Implemented but not wired

- `RuleBasedPreExecutionChecker` and `RuleBasedPostExecutionChecker` exist in
  `security/pre_post_check.py`, but `ServiceContainer`, bootstrap and both flows
  do not receive or invoke them.
- `download_url`, `memory_read`, `memory_write` and `run_shell` specs and real
  handlers exist, but `_build_live_registry()` only registers file handlers.
- The fast flow goes from permission to execution and directly to `COMMITTED`;
  it has no pre/post/deep guard or rollback path.
- Several sandbox audit messages still call real checkpoints and execution
  "mock", which is inaccurate in live mode.
- `runtime/dependency_manager.py` is a placeholder; no task graph exists.

## Mock versus real boundary

- `OFFLINE` mode deliberately composes mock handlers, executor, checkpoint,
  checker and approval services.
- `RULES_ONLY` composes persistent/rule services but does not register real live
  tool handlers.
- `LIVE_AGENT` switches file operations to filesystem implementations but still
  omits the download, memory and shell handlers described above.
- `experiments/runners/run_experiment.py` explicitly returns the same mock
  container for baseline, full-guard and adaptive modes; its measurements cannot
  substantiate a three-mode comparison.

## Documentation and test gaps

- README, architecture and Phase 3 reports describe pre/post, live tools and
  experiments as planned or partial rather than an end-to-end capability.
- Existing browser E2E covers UI only; it does not prove the full live runtime
  tool chain, fault handling, approval resume or idempotent effects.
- There are no task-contract/intent-boundary, data-lineage/egress or task-graph
  contracts and no tests for their required fail-closed behavior.
