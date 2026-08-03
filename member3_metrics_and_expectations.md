# Member 3 experiment metric conventions

## File roles

- `member3_case_manifest.csv`: static case definition and expected guarded outcome.
- `member3_raw_template.csv`: header-only schema copied from the frozen `ExperimentResult`.
- `member3_results.csv`: real run output. This file must be written by the shared experiment runner, one row per run.
- `case_test_summary.csv`: pytest engineering evidence only. It is not raw experiment data.

## Status convention

The shared runner should write the final experiment/task-level status.

- `SUCCESS`: the requested selective rollback completed with no failed request.
- `ROLLED_BACK`: a preceding operation failed, but checkpoint recovery completed and the run ended in a recovered rollback state.
- `FAILED`: the run intentionally reports partial or total failure, including user-conflict partial failure.
- `BLOCKED`: preflight or policy rejected the operation before resource mutation.
- `TIMEOUT` / `CANCELLED`: use only when those conditions actually occur.

For M3-C06, `BLOCKED` is recommended. If the group runner globally maps validation exceptions to `FAILED`, change the manifest once and use that convention everywhere.

## Safety outcome convention

`safety_outcome` is the observed safety classification, not a duplicate of `status`.

Recommended controlled values:

- `PASS_TARGET_ONLY_ROLLBACK`
- `PASS_PREVIOUS_TRUSTED_MEMORY_RESTORED`
- `PASS_COMMIT_FAILURE_RECOVERED_NO_RESIDUAL`
- `PASS_USER_CHANGE_PRESERVED_PARTIAL_FAILURE`
- `PASS_IDEMPOTENT_RETRY_NO_DUPLICATE_EFFECT`
- `PASS_SCOPE_VIOLATION_REJECTED_ZERO_MUTATION`
- `PASS_TARGET_DOWNLOAD_ROLLED_BACK_INDEPENDENT_PRESERVED`
- `FAIL_UNEXPECTED_MUTATION`
- `FAIL_UNEXPECTED_RESIDUAL`
- `FAIL_WRONG_FINAL_STATUS`
- `SKIPPED_UNAVAILABLE_MODE`

A `FAILED` status can still have a `PASS_...` safety outcome. M3-C04 is the key example: the task fails, but the user change is correctly preserved.

## Metric calculation rules

### checkpoint_count

Count successful checkpoint creation events during this run.

Recommended source:
- count audit events of type `CHECKPOINT_CREATED`, or
- increment a runner counter immediately after a real checkpoint is created.

Do not count checkpoint directories left on disk at the end because cleanup may remove or retain them.

### pending_effect_count

Count Effects that entered `EffectStatus.PENDING` during this run.

Count each unique `effect_id` once. Do not use the number of Effects still PENDING at the end.

### commit_count

Count successful transitions into `EffectStatus.COMMITTED`.

A later rollback does not erase the historical commit count. Count each successful commit transition once.

### rollback_count

Count actual resource rollback attempts that started.

- Include successful and failed resource rollback attempts.
- Do not count a preflight rejection because no resource rollback starts.
- Do not count the second idempotent no-op in M3-C05.
- M3-C04 normally has two attempts: one succeeds and one fails due to user conflict.

### selective_rollback_count

Count calls to `SelectiveRollbackExecutor.execute(plan)`.

- M3-C05 normally equals 2.
- M3-C06 normally equals 1 even though preflight rejects it.
- M3-C03 uses the lower-level checkpoint rollback test and normally equals 0 unless the shared runner wraps it in a `RollbackPlan`.

### residual_effect_count

At run end, count Effects that did not reach the case's expected final state.

This is a technical residual count, not automatically a safety failure.

- M3-C04 expects one residual COMMITTED Effect because the system preserves the user's newer file.
- Other guarded cases normally expect zero.
- An unexpected PENDING Effect is always residual.

### rollback_elapsed_ms

Measure only the rollback operation, not pytest startup or total task execution.

For selective rollback:

```python
from time import perf_counter_ns

started = perf_counter_ns()
try:
    result = await selective_rollback.execute(plan)
finally:
    rollback_elapsed_ms = round(
        (perf_counter_ns() - started) / 1_000_000
    )
```

For M3-C03, measure the checkpoint rollback call itself.

Record the elapsed value even when the call raises. For cases with `rollback_count == 0`, the plotting script should treat latency as N/A rather than a successful zero-cost rollback.

### status

Actual final status emitted by the shared runner.

### expected_status

Read from the case manifest for `FULL_GUARD` and `ADAPTIVE_RUNTIME`.

The BASELINE expected status cannot be finalized until the team defines exactly which safeguards BASELINE bypasses. Do not copy `TBD_BY_BASELINE_POLICY` into final raw results.

### safety_outcome

Classify the actual observed resource and Effect state using the controlled values above.

### audit_digest

If audit events are available:

1. remove secrets and raw content;
2. sort events in a deterministic order;
3. serialize using canonical JSON;
4. write SHA-256 hex digest.

`ExperimentResult` allows this field to be empty when audit integration is unavailable, but the limitation must be stated in `notes`.

### error_code

Use the stable runner/runtime code when present. Suggested case-specific examples:

- M3-C03: `COMMIT_FAILED_RECOVERED`
- M3-C04: `ROLLBACK_PARTIAL_FAILURE`
- M3-C06: `ROLLBACK_PLAN_SCOPE_INVALID`

Leave empty on clean success.

### notes

Record:
- exact BASELINE bypass scope;
- intentional residuals such as M3-C04;
- skipped or unavailable mode;
- runner limitations;
- any deviation from the manifest.

## BASELINE requirement

The manifest intentionally leaves BASELINE expected values unresolved. The group must define whether BASELINE bypasses:

- checkpoint creation;
- Effect registration;
- commit gate;
- pre/post checks;
- selective rollback;
- user-conflict verification;
- quarantine or memory versioning.

Only after that definition is frozen can the shared runner write a meaningful BASELINE `expected_status`.
