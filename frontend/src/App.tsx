const phases = [
  'Planner 产生 ToolCallRequest',
  'Runtime Scheduler 统一调度',
  'Risk / Policy / Permission 决策',
  '受控执行与 Commit / Rollback',
  'AuditEvent 记录事实',
]

export default function App() {
  return (
    <main className="shell">
      <header>
        <p className="eyebrow">Phase 0 工程骨架</p>
        <h1>RA-Agent Runtime</h1>
        <p className="summary">
          当前仅冻结工程边界、公共 Contract
          与开发工具链；真实安全能力将在后续阶段实现。
        </p>
      </header>

      <section aria-labelledby="execution-chain">
        <h2 id="execution-chain">冻结执行链</h2>
        <ol>
          {phases.map((phase) => (
            <li key={phase}>{phase}</li>
          ))}
        </ol>
      </section>
    </main>
  )
}
