# Final browser evidence

Runtime source commit: `63718ae`  
Captured: 2026-08-02 (Asia/Shanghai)  
Execution: isolated local `live-agent` Playwright run; no cloud deployment and no real LLM.

- `waiting-approval.png` — high-risk Graph node is waiting for explicit approval.
- `grant-resumed.png` — Grant resumes the graph and the dependency converges.
- `deny-blocked.png` — Deny blocks the target and descendant.
- `cancelled.png` — Cancelled graph state; resume is rejected by the browser scenario.
- `security-blocked-audit.png` — dangerous Shell input is safety-blocked and present in Audit.

The browser scenario source is `tests/e2e/final-runtime-scenarios.spec.ts`. It records fresh artifacts in this directory; transient runner metadata and per-test folders are ignored. Screenshots deliberately omit secrets, raw untrusted output, and hidden reasoning.
