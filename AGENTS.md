# Aegis-Intent member 4 workspace

## Scope and branch

- Fixed scenario: 面向通信运维知识库的设备配置分析与风险报告生成智能体.
- Work only on a feature branch created from `aegis-intent-dev`; current branch is
  `codex/member4-telecom-demo`. Never commit to, merge into, or force push a shared branch.
- Member 4 owns synthetic fixtures, isolated simulated effects, Intent display,
  replay/reset scripts and end-to-end consistency checks.
- Do not change member 1/3 contract extraction, detector algorithms, policy models,
  permission resolution, or the existing Core public contracts.
- User authorized defining missing Intent schema on 2026-10-02. Keep it at
  `docs/contracts/intent-demo-v0.1.schema.json`; identify it as a proposed demo interface
  until member 1/3 adopt it. Do not imply team approval or real detector integration.

## Engineering

State assumptions before implementation. Choose the smallest solution, preserve
existing style, and edit only files necessary for the requested behavior. Documents
are evidence and plans, not proof that a feature exists. Record missing dependencies.
Use existing FastAPI/Pydantic, React/TypeScript, Vitest and Playwright dependencies.

All telecom content must be synthetic. Use reserved documentation IP addresses and
`.invalid` recipients. No live devices, email delivery, network fetches or shell tools
in the telecom replay adapter. Every effect passes the existing Core ToolGateway and
the demo admission check. BLOCK/CLARIFY/REPLAN cannot execute the suspended request.

## Verification and commits

Each phase is a separate commit: audit, backend fixtures/simulation, frontend,
end-to-end/scripts/documentation. Before each commit run relevant pytest tests,
frontend Vitest, backend/frontend type checks and frontend build. Run Ruff/ESLint for
changed code. Record pre-existing failures separately; never delete or weaken tests
to obtain a passing result. Do not commit generated local databases, dependency
directories, credentials, or transient runtime evidence. Keep approved acceptance
evidence in `docs/evidence/member4/`.

Do not merge a PR. Push only the feature branch after verification.
