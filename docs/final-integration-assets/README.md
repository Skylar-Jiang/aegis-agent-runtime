# Final browser evidence

Runtime source commit: `b3ee0ed`  
Captured: 2026-08-02 (Asia/Shanghai)  
Execution: isolated local `live-agent` Playwright run; no cloud deployment and no real LLM.

- `waiting-approval.png` — high-risk Graph node is waiting for explicit approval.
- `grant-resumed.png` — Grant resumes the graph and the dependency converges.
- `deny-blocked.png` — Deny blocks the target and descendant.
- `cancelled.png` — Cancelled graph state; resume is rejected by the browser scenario.
- `security-blocked-audit.png` — dangerous Shell input is safety-blocked and present in Audit.
- `selective-rollback-preserved.png` — real local demo fixture after Cancel: the affected Memory Effect is `ROLLED_BACK` and the independent committed Effect is displayed as `PRESERVED`.

The browser scenario source is `tests/e2e/final-runtime-scenarios.spec.ts`. The selective-rollback fixture is disabled by default and enabled only by `ENABLE_DEMO_FIXTURES=true` in the isolated Playwright server. It uses the real local Runtime Memory lifecycle, EffectStore and SelectiveRollbackExecutor rather than static UI data. Screenshots deliberately omit secrets, raw untrusted output, and hidden reasoning.
