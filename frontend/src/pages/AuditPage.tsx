import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import { createTaskStreamUrl, getTaskEvents } from '../api/tasks'
import { StatusBadge } from '../components/StatusBadge'
import { describeAuditEvent, mergeAuditEvents } from '../lib/taskEvents'
import { useUIStore } from '../stores/ui'
import type { AuditEvent } from '../types/contracts'
import { useI18n } from '../i18n'

export function AuditPage() {
  const { language, text } = useI18n()
  const [searchParams, setSearchParams] = useSearchParams()
  const [taskId, setTaskId] = useState(searchParams.get('task_id') ?? '')
  const [events, setEvents] = useState<AuditEvent[]>([])
  const [connected, setConnected] = useState(false)
  const [polling, setPolling] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const filterType = useUIStore((state) => state.auditFilterType)

  const activeTaskId = searchParams.get('task_id')?.trim() ?? ''
  const [reload, setReload] = useState(0)

  function submitLoad() {
    const selectedTaskId = taskId.trim()
    if (!selectedTaskId) return
    if (selectedTaskId === activeTaskId) setReload((value) => value + 1)
    else setSearchParams({ task_id: selectedTaskId })
  }

  useEffect(() => {
    setTaskId(activeTaskId)
  }, [activeTaskId])

  useEffect(() => {
    let disposed = false
    let timer: number | undefined
    setEvents([])
    setError(null)
    setConnected(false)
    setPolling(false)
    if (!activeTaskId) return
    async function loadEvents() {
      try {
        const data = await getTaskEvents(activeTaskId)
        if (!disposed) {
          setEvents((current) =>
            mergeAuditEvents(
              current,
              (data.events as AuditEvent[]).filter(
                (event) => event.task_id === activeTaskId,
              ),
            ),
          )
          setError(null)
        }
      } catch (cause) {
        if (!disposed) setError((cause as Error).message)
      }
    }
    void loadEvents()
    const source = new EventSource(createTaskStreamUrl(activeTaskId))
    source.addEventListener('audit', (message) => {
      if (disposed) return
      try {
        const event = JSON.parse(
          (message as MessageEvent<string>).data,
        ) as AuditEvent
        if (event.event_id && event.task_id === activeTaskId) {
          setEvents((current) => mergeAuditEvents(current, [event]))
        }
      } catch {
        // Malformed data is not an audit fact.
      }
    })
    source.onopen = () => {
      if (disposed) return
      setConnected(true)
      setPolling(false)
      window.clearInterval(timer)
      timer = undefined
    }
    source.onerror = () => {
      if (disposed) return
      setConnected(false)
      setPolling(true)
      if (timer === undefined)
        timer = window.setInterval(() => void loadEvents(), 3000)
    }
    return () => {
      disposed = true
      source.close()
      window.clearInterval(timer)
    }
  }, [activeTaskId, reload])
  const filtered =
    filterType === 'ALL'
      ? events
      : events.filter((event) => event.event_type === filterType)

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold">
          {text('Audit timeline', '审计时间线')}
        </h1>
        <span
          className={`text-xs ${connected ? 'text-emerald-700' : polling ? 'text-amber-700' : 'text-gray-600'}`}
        >
          {connected
            ? text('● Live', '● 实时连接')
            : polling
              ? text('◐ Polling fallback', '◐ 轮询模式')
              : text('○ Offline', '○ 未连接')}
        </span>
      </div>
      <div className="mb-4 flex flex-wrap gap-2">
        <input
          className="min-w-56 flex-1 rounded border border-slate-200 bg-white px-3 py-2 text-sm text-slate-800"
          placeholder={text('Task ID...', '任务 ID…')}
          value={taskId}
          onChange={(event) => setTaskId(event.target.value)}
        />
        <button
          className="rounded bg-blue-700 px-4 py-2 text-sm text-white disabled:opacity-50"
          disabled={!taskId.trim()}
          onClick={submitLoad}
        >
          {text('Load audit timeline', '加载审计时间线')}
        </button>
      </div>
      {error && (
        <p className="mb-4 rounded border border-red-900 bg-red-950/30 p-3 text-sm text-rose-700">
          {error}
        </p>
      )}
      {taskId && filtered.length === 0 && !error && (
        <p className="rounded border border-slate-200 bg-white p-4 text-sm text-gray-500">
          {text(
            'No audit events have been recorded for this task.',
            '该任务尚未记录审计事件。',
          )}
        </p>
      )}
      {filtered.length > 0 && (
        <ol className="m-0 max-h-[36rem] overflow-y-auto rounded border border-slate-200 p-0">
          {filtered.map((event) => {
            const view = describeAuditEvent(event, language)
            return (
              <li
                key={event.event_id}
                className="m-0 border-b border-slate-200 border-l-0 px-4 py-3 last:border-b-0"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium text-slate-800">
                    {view.stage}
                  </span>
                  <StatusBadge status={event.status} />
                  <time className="ml-auto text-xs text-gray-500">
                    {new Date(event.timestamp).toLocaleString(language)}
                  </time>
                </div>
                <p className="mt-1 text-sm text-slate-500">{view.detail}</p>
                <p className="mt-1 font-mono text-[11px] text-gray-600">
                  #{event.sequence_number} · {event.event_type}
                </p>
              </li>
            )
          })}
        </ol>
      )}
    </div>
  )
}
