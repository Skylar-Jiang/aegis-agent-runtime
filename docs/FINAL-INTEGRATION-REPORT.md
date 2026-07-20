# Final Integration Validation Report

## Scope

Integration branch `integration/real-agent-validation` combines the security engine,
controlled filesystem execution, durable audit/approval/idempotency state, explicit
runtime modes, DeepSeek-compatible planning, AgentRunner, task API, SSE replay, and
the minimal task/approval/audit frontend.

## Runtime safety boundary

- `offline` is the default and is all-mock/no-side-effect.
- `rules-only` uses the rule engine and durable SQLite state, while retaining mock tool
  execution.
- `live-agent` is required for real workspace file handlers. It is restricted to the
  configured `.runtime/workspace`; Shell, network download, and Memory have no live
  handlers.
- Agent plans are parsed from a strict JSON schema, receive Runtime-owned task/step/
  request IDs, and reach tools only through `RuntimeScheduler`. DeepSeek planning is
  bounded to `MAX_AGENT_TURNS` (default 8); each turn may emit one tool call or stop.

## Verified scenarios

| Scenario | Evidence |
| --- | --- |
| Dangerous shell command | Rule-based classifier produces CRITICAL/BLOCK; disabled shell has no handler. |
| Pending file write | live-agent integration test performs checkpoint → pending → deep check → commit inside a temporary `.runtime/workspace`. |
| Durable audit ordering | concurrent persistent-recorder tests and migration enforce unique, contiguous `(task_id, sequence_number)`. |
| Approval race | concurrent grant/expiry tests produce one decision and one domain conflict, never a raw database exception. |
| Model failure | AgentRuntime test verifies a failing planner schedules zero tools. |
| SSE reconnect | replay test subscribes before history and de-duplicates an event arriving during replay. |

## Performance smoke

On 2026-07-20, the disposable SQLite smoke recorded and verified 100 durable audit
events in **363.5 ms** (**275.1 events/s**). This is a local regression datapoint,
not a production capacity guarantee.

## Final automated verification

On 2026-07-20, backend pytest completed with **486 passed, 7 skipped**. Ruff and
Pyright reported no findings. Frontend ESLint, TypeScript typecheck, Vitest (2 tests),
and the Vite production build also completed successfully.

## Remaining deployment boundary

Approval identity is accepted at the HTTP boundary as `decided_by`. A production
deployment must replace this with authenticated, authorized caller identity before
exposing the approval API outside a trusted operator environment.
