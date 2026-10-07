import { useState } from 'react'
import { RealCoreGatewayClient, type RecoveryPlan } from './gateway'
const client = new RealCoreGatewayClient()
export function RecoveryPanel({
  taskId,
  onComplete,
}: {
  taskId: string
  onComplete: () => Promise<void>
}) {
  const [plan, setPlan] = useState<RecoveryPlan | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function act(approve: boolean) {
    setBusy(true)
    setError('')
    try {
      const next =
        approve && plan
          ? await client.approveRecovery(plan.plan_id)
          : await client.proposeRecovery(taskId)
      setPlan(next)
      if (approve) await onComplete()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(false)
    }
  }
  return (
    <section aria-label="有界报告恢复">
      <h3>有界报告恢复</h3>
      <p>
        仅按原契约生成报告：重新读取指定证据，再写入原报告路径。最多两次提案、四次调用。证据变化或权限变化会安全终止。
      </p>
      <button disabled={busy} onClick={() => void act(false)}>
        生成恢复提案
      </button>
      {error && <p role="alert">{error}</p>}
      {plan && (
        <>
          <p>
            处置：{plan.status} · 待确认契约版本 {plan.draft_contract_version} ·
            剩余提案预算 {plan.retry_budget}
          </p>
          <p>
            新增授权：
            {plan.added_scope.length ? plan.added_scope.join('、') : '无'}
          </p>
          <p>隔离引用：{plan.contaminated_refs.join('、')}</p>
          <pre>{JSON.stringify(plan.proposed_actions, null, 2)}</pre>
          {plan.error && <p role="alert">{plan.error}</p>}
          {plan.status === 'PROPOSED' && (
            <button disabled={busy} onClick={() => void act(true)}>
              确认原授权并执行恢复
            </button>
          )}
        </>
      )}
    </section>
  )
}
