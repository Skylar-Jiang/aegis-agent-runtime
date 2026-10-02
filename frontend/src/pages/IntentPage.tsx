import { useEffect, useState } from 'react'
import {
  controlIntentRun,
  getIntentRun,
  listIntentCases,
  resetIntentDemo,
  startIntentCase,
} from '../api/intentDemo'
import type { IntentCase, IntentRun } from '../api/intentDemo'
import { IntentPanel } from '../features/intent/IntentPanel'

export function IntentPage() {
  const [cases, setCases] = useState<IntentCase[]>([])
  const [selected, setSelected] = useState('')
  const [run, setRun] = useState<IntentRun | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let active = true
    listIntentCases()
      .then((data) => {
        if (active) setCases(data)
      })
      .catch((e) => {
        if (active) setError(e instanceof Error ? e.message : String(e))
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [])
  const caseId = selected || cases[0]?.case_id || ''
  async function perform(operation: () => Promise<IntentRun | null>) {
    setBusy(true)
    setError(null)
    try {
      setRun(await operation())
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }
  const canStop =
    run &&
    [
      'BLOCKED',
      'WAITING_CLARIFICATION',
      'WAITING_REPLAN',
      'WAITING_GOAL_CONFIRMATION',
    ].includes(run.status)
  return (
    <div className="intent-page">
      <div className="intent-page-heading">
        <h1>通信运维 Intent 演示</h1>
        <p>合成数据 · fixture/mock 检测 · 真实本地执行 · 无外部投递</p>
      </div>
      <div className="intent-toolbar">
        <label htmlFor="intent-case">场景</label>
        <select
          id="intent-case"
          value={caseId}
          disabled={loading || busy}
          onChange={(e) => setSelected(e.target.value)}
        >
          {loading && <option value="">正在加载场景…</option>}
          {cases.map((c) => (
            <option key={c.case_id} value={c.case_id}>
              {c.case_id} · {c.title}
            </option>
          ))}
        </select>
        <button
          disabled={busy || loading || !caseId}
          onClick={() => perform(() => startIntentCase(caseId))}
        >
          运行场景
        </button>
        <button
          disabled={busy || loading || cases.length === 0}
          onClick={() =>
            perform(async () => {
              await resetIntentDemo()
              return null
            })
          }
        >
          重置演示
        </button>
      </div>
      {busy && <p role="status">后端正在执行并核验副作用…</p>}
      {error && (
        <p className="intent-error" role="alert">
          {error}
        </p>
      )}
      {run && (
        <div className="intent-controls">
          {run.status === 'WAITING_GOAL_CONFIRMATION' && (
            <button
              disabled={busy}
              onClick={() =>
                perform(() => controlIntentRun(run.run_id, 'confirm'))
              }
            >
              确认合法目标变更
            </button>
          )}
          {run.status === 'WAITING_REPLAN' && (
            <button
              disabled={busy}
              onClick={() =>
                perform(() => controlIntentRun(run.run_id, 'replan'))
              }
            >
              执行已复核纠偏
            </button>
          )}
          {canStop && (
            <button
              disabled={busy}
              onClick={() =>
                perform(() => controlIntentRun(run.run_id, 'stop'))
              }
            >
              安全终止
            </button>
          )}
          <button
            disabled={busy}
            onClick={() => perform(() => getIntentRun(run.run_id))}
          >
            重新核验实际状态
          </button>
        </div>
      )}
      {run ? (
        <IntentPanel key={run.run_id} run={run} />
      ) : (
        !loading && (
          <p className="intent-empty">
            选择场景并运行，查看后端事件与实际副作用。
          </p>
        )
      )}
    </div>
  )
}
