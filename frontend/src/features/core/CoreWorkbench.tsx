import { RealWorkbench } from './RealWorkbench'
import { useI18n } from '../../i18n'
import { useEffect, useRef, useState } from 'react'
import {
  HttpMockToolGateway,
  scenarios,
  type CoreGatewayClient,
  type CoreSnapshot,
  type Scenario,
} from './gateway'

const defaultClient = new HttpMockToolGateway()
const labels: Record<Scenario, string> = {
  confirm: '等待确认',
  allow: '允许执行',
  deny: '策略拒绝',
  replan: '需要重新规划',
  empty: '暂无事件',
  error: '服务异常',
}

function MockCoreWorkbench({
  client = defaultClient,
}: {
  client?: CoreGatewayClient
}) {
  const [scenario, setScenario] = useState<Scenario>('confirm')
  const [snapshot, setSnapshot] = useState<CoreSnapshot | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [reload, setReload] = useState(0)
  const requestVersion = useRef<symbol>(Symbol())

  useEffect(() => {
    const version = Symbol()
    requestVersion.current = version
    setBusy(true)
    setSnapshot(null)
    setError('')
    client
      .load(scenario)
      .then((next) => {
        if (requestVersion.current === version) setSnapshot(next)
      })
      .catch((cause: unknown) => {
        if (requestVersion.current === version)
          setError(cause instanceof Error ? cause.message : '载入失败')
      })
      .finally(() => {
        if (requestVersion.current === version) setBusy(false)
      })
    return () => {
      requestVersion.current = Symbol()
    }
  }, [client, scenario, reload])

  async function confirm(confirmed: boolean) {
    const id = snapshot?.decision?.confirmation_id
    if (!id || busy) return
    const version = Symbol()
    requestVersion.current = version
    setBusy(true)
    setError('')
    try {
      const next = await client.resolveConfirmation(id, confirmed)
      if (version === requestVersion.current) setSnapshot(next)
    } catch (cause) {
      if (version === requestVersion.current)
        setError(cause instanceof Error ? cause.message : '确认失败')
    } finally {
      if (version === requestVersion.current) setBusy(false)
    }
  }

  const events = [...(snapshot?.events ?? [])]
    .filter((event) => event.task_id === snapshot?.task_id)
    .sort((a, b) => a.sequence - b.sequence)

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <header>
        <p className="text-sm text-indigo-600">AEGIS CORE · P2 / PR1</p>
        <h1 className="!text-3xl">任务与行为工作台</h1>
        <p className="text-amber-700" role="note">
          模拟数据演示 · 不执行真实工具 · 未接入密码验证
        </p>
        <p className="mt-2 text-sm text-slate-500">
          查看契约边界、网关决定与有序事件。确认操作演示重新检查后的固定返回结果。
        </p>
      </header>
      <div className="flex flex-wrap items-center gap-3">
        <label htmlFor="core-scenario">演示场景</label>
        <select
          id="core-scenario"
          className="rounded border border-slate-200 bg-white p-2"
          value={scenario}
          onChange={(event) => setScenario(event.target.value as Scenario)}
        >
          {scenarios.map((value) => (
            <option value={value} key={value}>
              {labels[value]}
            </option>
          ))}
        </select>
        <button
          className="rounded border border-slate-200 px-3 py-2 disabled:opacity-40"
          disabled={busy}
          onClick={() => setReload((value) => value + 1)}
        >
          重新载入
        </button>
        {busy && <span role="status">正在载入模拟结果…</span>}
      </div>
      {error && (
        <p
          role="alert"
          className="rounded border border-red-800 p-3 text-rose-700"
        >
          {error}
        </p>
      )}
      {snapshot && (
        <>
          <div className="grid gap-4 md:grid-cols-2">
            <article
              className="rounded-lg border border-slate-200 p-5"
              aria-label="任务契约"
            >
              <h2 className="font-semibold">
                任务契约 · v{snapshot.contract.version}
              </h2>
              <p className="mt-2 break-all text-xs text-slate-500">
                {snapshot.task_id} / {snapshot.contract.session_id}
              </p>
              <p className="mt-3">{snapshot.contract.goals.join('；')}</p>
              <p className="mt-2 text-sm text-slate-500">
                完成条件：
                {snapshot.contract.completion_criteria?.join('；') || '未提供'}
              </p>
              <dl className="mt-4 space-y-2 text-sm">
                <dt className="text-slate-500">允许范围</dt>
                <dd>
                  {snapshot.contract.allowed?.map((rule, i) => (
                    <div className="break-all" key={i}>
                      {rule.action} · {rule.resource}
                    </div>
                  ))}
                </dd>
                <dt className="text-slate-500">禁止操作</dt>
                <dd>
                  {snapshot.contract.denied?.map((rule, i) => (
                    <div key={i}>{rule.action}</div>
                  ))}
                </dd>
                <dt className="text-slate-500">策略版本</dt>
                <dd>{snapshot.contract.policy_version}</dd>
              </dl>
            </article>
            <article
              className="rounded-lg border border-slate-200 p-5"
              aria-label="有效权限"
            >
              <h2 className="font-semibold">有效权限</h2>
              <p className="mt-2 text-sm text-slate-500">
                来源：
                {snapshot.effective_permission.matched_sources?.join('、') ||
                  '未提供'}
              </p>
              <p className="mt-3">
                允许 {snapshot.effective_permission.allowed?.length ?? 0} 项 /
                禁止 {snapshot.effective_permission.denied?.length ?? 0} 项
              </p>
              {snapshot.effective_permission.conflict_reason && (
                <p className="mt-2 text-rose-700">
                  {snapshot.effective_permission.conflict_reason}
                </p>
              )}
              <p className="mt-3 text-sm">
                约束：
                {JSON.stringify(
                  snapshot.effective_permission.constraints ?? {},
                )}
              </p>
              <p className="mt-3 text-sm text-slate-500">
                此视图直接展示模拟网关返回的结果。
              </p>
            </article>
          </div>
          <article
            className="rounded-lg border border-slate-200 p-5"
            aria-label="网关决定"
          >
            <h2 className="font-semibold">网关决定</h2>
            <p
              className="mt-3 font-mono text-lg text-indigo-600"
              data-testid="gateway-decision"
            >
              {snapshot.decision?.decision ?? '尚无决定'}
            </p>
            {snapshot.decision && (
              <p className="mt-2 text-sm">
                原因码：{snapshot.decision.reason_code}
              </p>
            )}
            {snapshot.confirmation_status && (
              <p className="mt-2" data-testid="confirmation-status">
                {snapshot.confirmation_status}
              </p>
            )}
            {snapshot.confirmation_status === 'WAITING_CONFIRMATION' && (
              <div className="mt-4 flex flex-wrap gap-3">
                <button
                  disabled={busy}
                  onClick={() => void confirm(true)}
                  className="rounded bg-blue-600 px-4 py-2 disabled:opacity-40"
                >
                  模拟批准并重新检查
                </button>
                <button
                  disabled={busy}
                  onClick={() => void confirm(false)}
                  className="rounded border border-red-600 px-4 py-2 disabled:opacity-40"
                >
                  模拟拒绝
                </button>
              </div>
            )}
          </article>
          <article
            className="rounded-lg border border-slate-200 p-5"
            aria-label="行为时间线"
          >
            <h2 className="font-semibold">行为时间线 · {events.length} 条</h2>
            {!events.length && (
              <p className="mt-4 text-slate-500">此任务暂无行为事件。</p>
            )}
            <ol
              className="!my-4 !list-none !p-0 space-y-3"
              aria-label="有序行为事件"
            >
              {events.map((event) => (
                <li
                  key={event.event_id}
                  className="rounded border border-slate-200 p-4"
                  data-sequence={event.sequence}
                >
                  <div className="flex flex-wrap justify-between gap-2">
                    <span className="font-mono text-sm">
                      #{event.sequence} {event.type}
                    </span>
                    <span className="text-sm text-indigo-600">
                      {event.state}
                    </span>
                  </div>
                  <p className="mt-2 break-all text-xs text-slate-500">
                    {event.occurred_at} · {event.actor} · {event.source_ref}
                  </p>
                  <details className="mt-2 text-xs text-slate-500">
                    <summary className="cursor-pointer">查看关联信息</summary>
                    <dl className="mt-2 space-y-1 break-all">
                      <dt>事件 ID</dt>
                      <dd>{event.event_id}</dd>
                      <dt>请求 ID</dt>
                      <dd>{event.request_id ?? '未提供'}</dd>
                      <dt>父事件</dt>
                      <dd>{event.parent_event_id ?? '无'}</dd>
                      <dt>对象摘要</dt>
                      <dd>{event.object_digest ?? '未提供（模拟样例）'}</dd>
                      <dt>结果摘要</dt>
                      <dd>{event.result_digest ?? '未提供（模拟样例）'}</dd>
                    </dl>
                  </details>
                </li>
              ))}
            </ol>
          </article>
        </>
      )}
    </div>
  )
}

export function CoreWorkbench({ client }: { client?: CoreGatewayClient }) {
  const [mode, setMode] = useState<'real' | 'mock'>('real')
  const { text } = useI18n()
  if (client) return <MockCoreWorkbench client={client} />
  return (
    <div className="core-mode-container">
      <div className="mode-tabs" aria-label={text('Data source', '数据来源')}>
        <button
          className={mode === 'real' ? 'active' : ''}
          onClick={() => setMode('real')}
        >
          {text('Core workbench', 'Core 工作台')}
        </button>
        <button
          className={mode === 'mock' ? 'active' : ''}
          onClick={() => setMode('mock')}
        >
          {text('Mock lab · simulated', 'Mock 实验室 · 模拟数据')}
        </button>
      </div>
      {mode === 'real' ? (
        <RealWorkbench />
      ) : (
        <MockCoreWorkbench client={defaultClient} />
      )}
    </div>
  )
}
