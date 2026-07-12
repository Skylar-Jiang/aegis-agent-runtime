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
        <p className="eyebrow">Phase 1 Runtime 基础闭环</p>
        <h1>RA-Agent Runtime</h1>
        <p className="summary">
          当前已实现 LOW 风险直通与阻断路径；MEDIUM 风险的 Pending、
          SafetyCheck 与 CommitGate 将在后续阶段实现。
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
