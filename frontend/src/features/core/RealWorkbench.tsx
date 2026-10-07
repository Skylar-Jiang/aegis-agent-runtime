import { useEffect, useMemo, useRef, useState } from 'react'
import { useI18n } from '../../i18n'
import type { BehaviorEvent } from './generated'
import { IntentPanel } from './IntentPanel'
import { RecoveryPanel } from './RecoveryPanel'
import {
  RealCoreGatewayClient,
  CoreApiError,
  type CoreHealth,
  type CoreTaskDraft,
  type RealToolEvaluationRequest,
  type RealEvaluationResult,
  type RealExecutionResult,
  type VerificationResult,
} from './gateway'

const client = new RealCoreGatewayClient()
const eventPageSize = 1000
const maxPagesPerRefresh = 10
const uid = (prefix: string) => `${prefix}-${crypto.randomUUID()}`
const tools = [
  'create_file',
  'read_file',
  'write_file',
  'list_dir',
  'delete_file',
]
const effects: Record<string, string> = {
  create_file: 'WRITE',
  write_file: 'WRITE',
  delete_file: 'DELETE',
  read_file: 'READ',
  list_dir: 'READ',
}
const starterRequest = JSON.stringify(
  {
    envelope: {
      request_id: '',
      task_id: '',
      session_id: '',
      contract_ref: {
        contract_id: '',
        version: 1,
        digest: '',
        status: 'CONFIRMED',
      },
      skill_ref: 'core-ui',
      tool: 'create_file',
      action: 'create_file',
      canonical_args: { path: 'core-note.txt', content: '' },
      resource: 'core-note.txt',
      effect_class: 'WRITE',
    },
    permissions: { user_grants: [], skill_grants: [], system_grants: [] },
  },
  null,
  2,
)

function Property({
  label,
  children,
}: {
  label: string
  children: React.ReactNode
}) {
  return (
    <div className="core-property">
      <dt>{label}</dt>
      <dd>{children || '—'}</dd>
    </div>
  )
}

function Activity({ events }: { events: BehaviorEvent[] }) {
  const { text, language } = useI18n()
  const buckets = new Map<number, number>()
  for (const event of events) {
    const timestamp = Date.parse(event.occurred_at)
    if (Number.isFinite(timestamp)) {
      const bucket = Math.floor(timestamp / 10000) * 10000
      buckets.set(bucket, (buckets.get(bucket) ?? 0) + 1)
    }
  }
  const values = [...buckets].sort((a, b) => a[0] - b[0]).slice(-12)
  const max = Math.max(1, ...values.map(([, count]) => count))
  const latest = new Map<string, BehaviorEvent>()
  events.forEach((event) => {
    if (event.request_id && event.decision) latest.set(event.request_id, event)
  })
  return (
    <div className="core-activity">
      <div className="panel-heading">
        <h3>{text('Live activity', '实时活动')}</h3>
        <span>{text('Refresh · 2s', '每 2 秒刷新')}</span>
      </div>
      <div className="activity-stats">
        <span>
          <strong>
            {events.filter((event) => event.decision === 'ALLOW').length}
          </strong>{' '}
          {text('ALLOW events', 'ALLOW 事件')}
        </span>
        <span>
          <strong>
            {events.filter((event) => event.decision === 'DENY').length}
          </strong>{' '}
          {text('DENY events', 'DENY 事件')}
        </span>
        <span>
          <strong>
            {
              [...latest.values()].filter(
                (event) => event.decision === 'REQUIRE_CONFIRMATION',
              ).length
            }
          </strong>{' '}
          {text('Pending requests', '待确认请求')}
        </span>
      </div>
      {values.length ? (
        <>
          <svg
            role="img"
            aria-label={text('Event activity', '事件活动')}
            viewBox="0 0 280 84"
            className="activity-chart"
          >
            <line
              x1="0"
              x2="280"
              y1="76"
              y2="76"
              stroke="currentColor"
              opacity=".15"
            />
            {values.map(([timestamp, count], index) => (
              <rect
                key={timestamp}
                x={index * 23 + 3}
                y={76 - (count / max) * 62}
                width="14"
                height={(count / max) * 62}
                rx="2"
                data-count={count}
              >
                <title>
                  {new Date(timestamp).toLocaleTimeString(language)} · {count}
                </title>
              </rect>
            ))}
          </svg>
          <p className="micro-copy">
            {text(
              'Events per 10s bucket · latest 12 active buckets',
              '每 10 秒新增事件 · 最近 12 个活动时间桶',
            )}
          </p>
        </>
      ) : (
        <p className="empty-copy">
          {text(
            'Activity appears when this task records events.',
            '任务产生真实事件后显示活动图。',
          )}
        </p>
      )}
    </div>
  )
}

export function RealWorkbench() {
  const { text, language } = useI18n()
  const [health, setHealth] = useState<CoreHealth | null>(null)
  const [healthError, setHealthError] = useState('')
  const [objective, setObjective] = useState('')
  const [intentEnabled, setIntentEnabled] = useState(false)
  const [reportPath, setReportPath] = useState('reports/report.txt')
  const [requiredText, setRequiredText] = useState('Evidence:')
  const [reopenId, setReopenId] = useState(
    () => new URLSearchParams(window.location.search).get('task_id') ?? '',
  )
  const [task, setTask] = useState<CoreTaskDraft | null>(null)
  const [tool, setTool] = useState('create_file')
  const [resource, setResource] = useState('core-note.txt')
  const [content, setContent] = useState(
    'Aegis Core · controlled execution evidence',
  )
  const [raw, setRaw] = useState(starterRequest)
  const [request, setRequest] = useState<RealToolEvaluationRequest | null>(null)
  const [result, setResult] = useState<RealEvaluationResult | null>(null)
  const [execution, setExecution] = useState<RealExecutionResult | null>(null)
  const [executionState, setExecutionState] = useState<string | null>(null)
  const [events, setEvents] = useState<BehaviorEvent[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [eventError, setEventError] = useState('')
  const [checkpoint, setCheckpoint] = useState(() => uid('checkpoint'))
  const [checkpointIsManual, setCheckpointIsManual] = useState(false)
  const [bundle, setBundle] = useState<Record<string, unknown> | null>(null)
  const [bundleBinding, setBundleBinding] = useState<{
    taskId: string
    checkpointId: string
    toSequence: number
  } | null>(null)
  const [copy, setCopy] = useState('')
  const [verification, setVerification] = useState<{
    target: 'original' | 'copy'
    result: VerificationResult
  } | null>(null)
  const taskId = task?.task_id ?? request?.envelope.task_id ?? ''
  const isSigned = health?.crypto_mode === 'sm2'
  const eventFeed = useRef<{
    taskId: string
    cursor: number
    refresh: () => Promise<void>
  } | null>(null)
  const operationGeneration = useRef(0)
  const runtimeReceipt =
    execution?.result.runtime && typeof execution.result.runtime === 'object'
      ? (execution.result.runtime as Record<string, unknown>)
      : null
  const requestMatchesCurrentContract = Boolean(
    task &&
    request &&
    request.envelope.contract_ref.contract_id ===
      task.contract.ref.contract_id &&
    request.envelope.contract_ref.version === task.contract.ref.version &&
    request.envelope.contract_ref.digest === task.contract.ref.digest &&
    task.contract.ref.status === 'CONFIRMED',
  )

  useEffect(() => {
    let active = true
    client
      .health()
      .then((data) => {
        if (active) setHealth(data)
      })
      .catch((cause) => {
        if (active) setHealthError(String(cause.message))
      })
    return () => {
      active = false
    }
  }, [])

  useEffect(() => {
    const linkedId = new URLSearchParams(window.location.search)
      .get('task_id')
      ?.trim()
    if (linkedId) void reopen(linkedId)
    const onHistory = () => {
      const id = new URLSearchParams(window.location.search)
        .get('task_id')
        ?.trim()
      if (id) {
        setReopenId(id)
        void reopen(id)
      }
    }
    window.addEventListener('popstate', onHistory)
    return () => {
      operationGeneration.current += 1
      window.removeEventListener('popstate', onHistory)
    }
    // The initial deep link is loaded once; explicit reopen handles later ID changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    let active = true
    let loading: Promise<void> | null = null
    const controller = new AbortController()
    const history = new Map<string, BehaviorEvent>()
    setEvents([])
    setSelected(null)
    setEventError('')
    if (!taskId) return
    const feed = { taskId, cursor: 0, refresh: () => Promise.resolve() }
    const refresh = (): Promise<void> => {
      if (!active) return Promise.resolve()
      if (loading) return loading
      loading = (async () => {
        try {
          for (let page = 0; page < maxPagesPerRefresh; page++) {
            const afterSequence = feed.cursor
            const next = await client.events(taskId, {
              afterSequence,
              limit: eventPageSize,
              signal: controller.signal,
            })
            if (!active || eventFeed.current !== feed) return
            let changed = false
            for (const event of next) {
              if (
                event.task_id !== taskId ||
                !Number.isSafeInteger(event.sequence) ||
                event.sequence < 1
              )
                continue
              history.set(event.event_id, event)
              changed = true
              feed.cursor = Math.max(feed.cursor, event.sequence)
            }
            if (changed)
              setEvents(
                [...history.values()].sort((a, b) => a.sequence - b.sequence),
              )
            setEventError('')
            if (next.length < eventPageSize || feed.cursor === afterSequence)
              break
          }
        } catch (cause) {
          if (active)
            setEventError(
              cause instanceof Error ? cause.message : 'Event load failed',
            )
        } finally {
          loading = null
        }
      })()
      return loading
    }
    feed.refresh = refresh
    eventFeed.current = feed
    void refresh()
    const timer = window.setInterval(() => void refresh(), 2000)
    return () => {
      active = false
      controller.abort()
      if (eventFeed.current === feed) eventFeed.current = null
      window.clearInterval(timer)
    }
  }, [taskId])

  const selectedEvent =
    events.find((event) => event.event_id === selected) ?? events.at(-1)
  const orderedEvents = useMemo(() => [...events].reverse(), [events])

  function invalidate() {
    setRequest(null)
    setResult(null)
    setExecution(null)
    setExecutionState(null)
    setError('')
  }
  async function perform(action: () => Promise<void>) {
    if (busy) return
    const generation = operationGeneration.current
    setBusy(true)
    setError('')
    try {
      await action()
    } catch (cause) {
      if (generation === operationGeneration.current)
        setError(
          cause instanceof Error
            ? cause.message
            : text('Core request failed', 'Core 请求失败'),
        )
    } finally {
      if (generation === operationGeneration.current) setBusy(false)
    }
  }
  async function refreshEvents(id = taskId) {
    const feed = eventFeed.current
    if (feed?.taskId === id) await feed.refresh()
  }
  function bindTaskUrl(id: string) {
    const url = new URL(window.location.href)
    url.pathname = '/core'
    url.searchParams.set('task_id', id)
    window.history.replaceState(
      window.history.state,
      '',
      `${url.pathname}${url.search}`,
    )
    setReopenId(id)
  }
  async function reopen(id: string) {
    const generation = ++operationGeneration.current
    setBusy(true)
    setError('')
    try {
      const snapshot = await client.taskSnapshot(id.trim())
      if (generation !== operationGeneration.current) return
      const restoredRequest = snapshot.latest_request
        ? ({
            envelope: snapshot.latest_request.envelope,
            permissions: {
              user_grants: [],
              skill_grants: [],
              system_grants: [],
            },
          } satisfies RealToolEvaluationRequest)
        : null
      setTask(snapshot.task)
      setObjective(snapshot.task.contract.contract.goals[0] ?? '')
      setRequest(restoredRequest)
      if (restoredRequest && tools.includes(restoredRequest.envelope.tool)) {
        setTool(restoredRequest.envelope.tool)
        setResource(restoredRequest.envelope.resource)
        setContent(
          typeof restoredRequest.envelope.canonical_args.content === 'string'
            ? restoredRequest.envelope.canonical_args.content
            : '',
        )
      }
      setRaw(
        restoredRequest
          ? JSON.stringify(restoredRequest, null, 2)
          : starterRequest,
      )
      setResult(snapshot.latest_request?.evaluation ?? null)
      setExecution(snapshot.latest_request?.execution_result ?? null)
      setExecutionState(snapshot.latest_request?.execution_state ?? null)
      setBundle(null)
      setBundleBinding(null)
      setCopy('')
      setVerification(null)
      setCheckpoint(uid('checkpoint'))
      setCheckpointIsManual(false)
      bindTaskUrl(snapshot.task.task_id)
    } catch (cause) {
      if (generation === operationGeneration.current)
        setError(
          cause instanceof Error
            ? cause.message
            : text('Core request failed', 'Core 请求失败'),
        )
    } finally {
      if (generation === operationGeneration.current) setBusy(false)
    }
  }
  const create = () => {
    if (busy) return
    const generation = ++operationGeneration.current
    return perform(async () => {
      const session = await client.createSession(objective.trim().slice(0, 200))
      if (generation !== operationGeneration.current) return
      const created = await client.createTask(
        session.session_id,
        objective.trim(),
        intentEnabled
          ? [
              `intent:report=${reportPath.trim()}`,
              ...requiredText
                .split('\n')
                .map((value) => value.trim())
                .filter(Boolean)
                .map((value) => `intent:contains=${value}`),
            ]
          : [],
      )
      if (generation !== operationGeneration.current) return
      invalidate()
      setTask(created)
      setEvents([])
      setBundle(null)
      setBundleBinding(null)
      setCopy('')
      setVerification(null)
      setCheckpoint(uid('checkpoint'))
      setCheckpointIsManual(false)
      bindTaskUrl(created.task_id)
    })
  }
  const confirmContract = () =>
    perform(async () => {
      if (!task) return
      const record = await client.confirmContract(
        task.contract.ref.contract_id,
        task.contract.ref.version,
      )
      setTask({ ...task, contract: record })
      invalidate()
      await refreshEvents(task.task_id)
    })
  function buildRequest(): RealToolEvaluationRequest {
    if (!task || task.contract.ref.status !== 'CONFIRMED')
      throw new Error(
        text('Confirm this task contract first.', '请先确认当前任务契约。'),
      )
    return {
      envelope: {
        request_id: uid('request'),
        task_id: task.task_id,
        session_id: task.session_id,
        contract_ref: task.contract.ref,
        skill_ref: 'core-ui',
        tool,
        action: tool,
        canonical_args:
          tool === 'create_file' || tool === 'write_file'
            ? { path: resource.trim(), content }
            : { path: resource.trim() },
        resource: resource.trim(),
        effect_class: effects[tool],
      },
      permissions: { user_grants: [], skill_grants: [], system_grants: [] },
    }
  }
  const evaluate = (advanced = false) =>
    perform(async () => {
      invalidate()
      const next = advanced
        ? (JSON.parse(raw) as RealToolEvaluationRequest)
        : buildRequest()
      const evaluation = await client.evaluate(next)
      setRequest(next)
      setResult(evaluation)
      if (evaluation.intent?.disposition === 'SAFE_STOP')
        setTask((previous) =>
          previous ? { ...previous, status: 'CANCELLED' } : previous,
        )
      setExecutionState('READY')
      setRaw(JSON.stringify(next, null, 2))
      await refreshEvents(next.envelope.task_id)
    })
  const resolve = (confirmed: boolean) =>
    perform(async () => {
      if (!requestMatchesCurrentContract || !result?.decision.confirmation_id)
        return
      setResult(
        await client.resolveConfirmation(
          result.decision.confirmation_id,
          confirmed,
        ),
      )
      await refreshEvents()
    })
  const execute = () =>
    perform(async () => {
      if (
        !request ||
        !requestMatchesCurrentContract ||
        executionState !== 'READY' ||
        result?.decision.decision !== 'ALLOW' ||
        execution
      )
        return
      const completed = await client.execute(request.envelope.request_id)
      setExecution(completed)
      setExecutionState(completed.status)
      await refreshEvents()
    })
  const exportEvidence = () =>
    perform(async () => {
      let checkpointId = checkpoint.trim()
      const manualConflict = () =>
        new Error(
          text(
            'This checkpoint already anchors earlier history. Enter a new Checkpoint ID to export new events; the previous evidence keeps its original binding.',
            '此检查点已锚定先前历史。请填写新的检查点 ID 导出新增事件；旧证据仍绑定原检查点。',
          ),
        )
      if (bundleBinding?.checkpointId === checkpointId) {
        const later =
          bundleBinding.taskId === taskId
            ? await client.events(taskId, {
                afterSequence: bundleBinding.toSequence,
                limit: 1,
              })
            : null
        const historyGrew =
          later === null ||
          later.some(
            (event) =>
              event.task_id === taskId &&
              event.sequence > bundleBinding.toSequence,
          )
        if (historyGrew) {
          if (checkpointIsManual) throw manualConflict()
          checkpointId = uid('checkpoint')
        }
      }
      let next: Record<string, unknown>
      try {
        next = await client.exportAudit(taskId, checkpointId)
      } catch (cause) {
        if (!(cause instanceof CoreApiError) || cause.status !== 409)
          throw cause
        if (checkpointIsManual) throw manualConflict()
        // History can grow between the tail probe and export. A fresh automatic
        // anchor preserves the immutable checkpoint that caused the conflict.
        checkpointId = uid('checkpoint')
        next = await client.exportAudit(taskId, checkpointId)
      }
      const anchor = next.checkpoint as { to_seq?: unknown } | undefined
      const toSequence =
        typeof anchor?.to_seq === 'number'
          ? anchor.to_seq
          : (eventFeed.current?.cursor ?? 0)
      setBundle(next)
      setBundleBinding({ taskId, checkpointId, toSequence })
      setCheckpoint(checkpointId)
      setVerification(null)
      setCopy('')
    })
  const verify = (target: 'original' | 'copy') =>
    perform(async () => {
      setVerification(null)
      if (!bundle || !bundleBinding) return
      const candidate =
        target === 'original'
          ? bundle
          : (JSON.parse(copy) as Record<string, unknown>)
      const next = await client.verifyAudit(
        candidate,
        bundleBinding.taskId,
        bundleBinding.checkpointId,
      )
      setVerification({ target, result: next })
    })
  function tamperCopy() {
    if (!bundle) return
    const next = structuredClone(bundle)
    const entries = next.entries as
      Array<{ event?: Record<string, unknown> }> | undefined
    if (entries?.[0]?.event)
      entries[0].event.source_ref = `${String(entries[0].event.source_ref)}-tampered`
    else next.task_id = `${String(next.task_id)}-tampered`
    setCopy(JSON.stringify(next, null, 2))
    setVerification(null)
  }
  function download() {
    if (!bundle) return
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(bundle)], { type: 'application/json' }),
    )
    const link = document.createElement('a')
    link.href = url
    link.download = `${bundleBinding?.taskId ?? 'core'}-evidence.json`
    link.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="core-workbench">
      <header className="workbench-heading">
        <div>
          <p className="eyebrow">AEGIS CORE / V1</p>
          <h1>{text('Task workbench', '任务工作台')}</h1>
          <p>
            {text(
              'Define a boundary. Inspect every call. Verify the evidence.',
              '定义任务边界，检查每次调用，核验执行证据。',
            )}
          </p>
        </div>
        <div className="connection-state">
          <span className={`state-dot ${health ? 'available' : ''}`} />
          {health
            ? text('Core connected', 'Core 已连接')
            : text('Connecting to Core', '正在连接 Core')}
          <small>{health?.event_store ?? '/api/v1'}</small>
        </div>
      </header>
      {healthError && (
        <p className="notice danger" role="alert">
          {healthError}
        </p>
      )}
      <div className="task-create-row">
        <label htmlFor="core-objective">
          {text('Task objective', '任务目标')}
        </label>
        <input
          id="core-objective"
          value={objective}
          disabled={busy}
          placeholder={text(
            'e.g. Create a scoped experiment summary',
            '例如：在授权范围内创建实验摘要',
          )}
          onChange={(event) => setObjective(event.target.value)}
          onKeyDown={(event) => {
            if (
              event.key === 'Enter' &&
              !event.nativeEvent.isComposing &&
              event.keyCode !== 229 &&
              objective.trim()
            )
              void create()
          }}
        />
        <button
          className="primary-button"
          disabled={busy || !objective.trim()}
          onClick={() => void create()}
        >
          {task
            ? text('Create new task', '新建任务')
            : text('Create task', '创建任务')}
        </button>
      </div>
      <div className="task-create-row">
        <label htmlFor="core-reopen-id">
          {text('Reopen task ID', '重新打开任务 ID')}
        </label>
        <input
          id="core-reopen-id"
          value={reopenId}
          disabled={busy}
          onChange={(event) => setReopenId(event.target.value)}
        />
        <button
          className="secondary-button"
          disabled={busy || !reopenId.trim()}
          onClick={() => void reopen(reopenId)}
        >
          {text('Reopen task', '重新打开任务')}
        </button>
      </div>
      <details className="intent-setup">
        <summary>
          {text('Report completion conditions', '报告完成条件')}
        </summary>
        <label>
          <input
            type="checkbox"
            checked={intentEnabled}
            onChange={(event) => setIntentEnabled(event.target.checked)}
          />
          {text(
            'Check report consistency before execution',
            '执行前检查报告是否符合目标',
          )}
        </label>
        {intentEnabled && (
          <>
            <label htmlFor="intent-report-path">
              {text('Report path', '报告路径')}
            </label>
            <input
              id="intent-report-path"
              value={reportPath}
              onChange={(event) => setReportPath(event.target.value)}
            />
            <label htmlFor="intent-required-text">
              {text(
                'Required report text (one item per line)',
                '报告必须包含的内容（每行一项）',
              )}
            </label>
            <textarea
              id="intent-required-text"
              value={requiredText}
              onChange={(event) => setRequiredText(event.target.value)}
            />
            <p>
              {text(
                'Review these conditions in the contract before confirming. Mismatch stops the task.',
                '创建任务后请审阅契约中的这些条件。报告不符合条件时将终止任务。',
              )}
            </p>
          </>
        )}
      </details>
      <div className="workflow-steps" aria-label={text('Workflow', '操作流程')}>
        {[
          text('01  Task & contract', '01  任务与契约'),
          text('02  Evaluate & execute', '02  评估与执行'),
          text('03  Export & verify', '03  导出与核验'),
        ].map((label, index) => (
          <span
            key={label}
            className={
              (index === 0 && !task) ||
              (index === 1 && task && !execution) ||
              (index === 2 && execution)
                ? 'current'
                : ''
            }
          >
            {label}
          </span>
        ))}
        {task && <code title={task.task_id}>{task.task_id}</code>}
      </div>
      {error && (
        <p className="notice danger" role="alert">
          {error}
        </p>
      )}
      {busy && (
        <p className="operation-status" role="status">
          {text('Calling Core API…', '正在调用 Core API…')}
        </p>
      )}
      <div className="core-columns">
        <aside className="core-events-panel">
          <div className="panel-heading">
            <h2>{text('Behavior timeline', '行为时间线')}</h2>
            <span>{events.length}</span>
          </div>
          {!events.length && (
            <div className="event-empty">
              <span className="empty-symbol">≡</span>
              <p>{text('No recorded events', '暂无行为事件')}</p>
              <small>
                {text(
                  'Create a task to start an auditable workflow.',
                  '创建任务后，这里显示真实的契约与调用记录。',
                )}
              </small>
            </div>
          )}
          <ol
            className="core-events"
            aria-label={text('Behavior events', '行为事件')}
          >
            {orderedEvents.map((event) => (
              <li key={event.event_id}>
                <button
                  className={
                    selectedEvent?.event_id === event.event_id ? 'selected' : ''
                  }
                  onClick={() => setSelected(event.event_id)}
                >
                  <span className="event-number">
                    {String(event.sequence).padStart(2, '0')}
                  </span>
                  <span>
                    <strong>
                      #{event.sequence} {event.type}
                    </strong>
                    <small>{event.state}</small>
                    <time>
                      {new Date(event.occurred_at).toLocaleTimeString(language)}
                    </time>
                  </span>
                </button>
              </li>
            ))}
          </ol>
          <div className="event-panel-footer">
            {eventError ? (
              <span className="error-text">{eventError}</span>
            ) : (
              text(
                'Server events · refresh every 2s',
                '服务端事件 · 每 2 秒刷新',
              )
            )}
          </div>
        </aside>

        <div className="core-call-panel">
          <div className="panel-heading">
            <h2>{text('Task contract', '任务契约')}</h2>
            <span className="quiet-badge">
              {task?.contract.ref.status ?? text('Not created', '未创建')}
            </span>
          </div>
          {task ? (
            <div className="contract-summary">
              <p>{task.contract.contract.goals.join(' · ')}</p>
              <dl>
                <Property label={text('Version / policy', '版本 / 策略')}>
                  v{task.contract.ref.version} ·{' '}
                  {task.contract.contract.policy_version}
                </Property>
                <Property label={text('Allowed scope', '允许范围')}>
                  {task.contract.contract.allowed
                    ?.slice(0, 3)
                    ?.map((rule) => `${rule.action} · ${rule.resource}`)
                    .join('\n')}
                  {(task.contract.contract.allowed?.length ?? 0) > 3 && (
                    <span className="micro-copy">
                      {text(
                        'More rules in contract details',
                        '其余规则见契约详情',
                      )}
                    </span>
                  )}
                </Property>
              </dl>
              <details>
                <summary>
                  {text('Contract boundaries & digest', '契约边界与摘要')}
                </summary>
                <dl>
                  <Property label={text('All allowed rules', '完整允许规则')}>
                    {task.contract.contract.allowed
                      ?.map((rule) => `${rule.action} · ${rule.resource}`)
                      .join('\n')}
                  </Property>
                  <Property label="Digest">
                    <code>{task.contract.ref.digest}</code>
                  </Property>
                  <Property label={text('Denied', '禁止')}>
                    {task.contract.contract.denied
                      ?.map((rule) => rule.action)
                      .join(', ') || text('None declared', '无声明')}
                  </Property>
                  <Property label={text('Limits', '限制')}>
                    {JSON.stringify(task.contract.contract.limits)}
                  </Property>
                  <Property label={text('Completion conditions', '完成条件')}>
                    {task.contract.contract.completion_criteria
                      ?.map((item) =>
                        item
                          .replace('intent:report=', '报告路径：')
                          .replace('intent:contains=', '必须包含：'),
                      )
                      .join('\n') || text('None declared', '未声明')}
                  </Property>
                </dl>
              </details>
              {task.contract.ref.status !== 'CONFIRMED' && (
                <button
                  className="primary-button"
                  disabled={busy}
                  onClick={() => void confirmContract()}
                >
                  {text('Confirm contract', '确认契约')}
                </button>
              )}
            </div>
          ) : (
            <p className="empty-copy">
              {text(
                'Your task inherits the current security profile. Review its scope before confirming.',
                '任务将继承当前安全配置。创建后，请审阅范围并确认契约。',
              )}
            </p>
          )}

          <div className="panel-heading section-divider">
            <h2>{text('Tool call', '工具调用')}</h2>
            <span>
              {request
                ? request.envelope.request_id.slice(-12)
                : text('Draft', '草稿')}
            </span>
          </div>
          <div className="tool-form">
            <div className="form-two-columns">
              <label>
                {text('Tool', '工具')}
                <select
                  value={tool}
                  disabled={busy}
                  onChange={(event) => {
                    setTool(event.target.value)
                    invalidate()
                  }}
                >
                  {tools.map((name) => (
                    <option key={name}>{name}</option>
                  ))}
                </select>
              </label>
              <label>
                {text('Resource path', '资源路径')}
                <input
                  value={resource}
                  disabled={busy}
                  onChange={(event) => {
                    setResource(event.target.value)
                    invalidate()
                  }}
                />
              </label>
            </div>
            {(tool === 'create_file' || tool === 'write_file') && (
              <label>
                {text('Content', '内容')}
                <textarea
                  rows={4}
                  disabled={busy}
                  value={content}
                  onChange={(event) => {
                    setContent(event.target.value)
                    invalidate()
                  }}
                />
              </label>
            )}
            <p className="micro-copy">
              {text(
                'Scope and permissions are checked by the server against trusted configuration.',
                '服务端根据可信配置检查任务范围和权限。',
              )}
            </p>
            <div className="action-row">
              <button
                className="primary-button"
                disabled={
                  busy ||
                  !resource.trim() ||
                  task?.contract.ref.status !== 'CONFIRMED'
                }
                onClick={() => void evaluate()}
              >
                {text('Evaluate request', '评估请求')}
              </button>
              <span className="quiet-badge">{effects[tool]}</span>
            </div>
          </div>
          {result && (
            <div
              className={`gateway-result ${result.decision.decision === 'DENY' ? 'denied' : ''}`}
              aria-label={text('Gateway decision', '网关决定')}
            >
              <strong data-testid="real-gateway-decision">
                {result.decision.decision}
              </strong>
              <p>{result.decision.reason_code}</p>
              {result.decision.decision === 'REQUIRE_CONFIRMATION' &&
                requestMatchesCurrentContract && (
                  <div className="action-row">
                    <button
                      className="primary-button"
                      disabled={busy}
                      onClick={() => void resolve(true)}
                    >
                      {text('Approve and recheck', '批准并重新检查')}
                    </button>
                    <button
                      className="secondary-button"
                      disabled={busy}
                      onClick={() => void resolve(false)}
                    >
                      {text('Reject', '拒绝')}
                    </button>
                  </div>
                )}
              {result.decision.decision === 'ALLOW' &&
                !execution &&
                executionState === 'READY' &&
                requestMatchesCurrentContract && (
                  <button
                    className="primary-button"
                    disabled={busy}
                    onClick={() => void execute()}
                  >
                    {text('Execute tool', '执行工具')}
                  </button>
                )}
              <details>
                <summary>{text('Effective permissions', '有效权限')}</summary>
                <pre>
                  {JSON.stringify(result.effective_permission, null, 2)}
                </pre>
              </details>
            </div>
          )}
          {execution && (
            <div className="execution-result">
              <div className="panel-heading">
                <h3>{text('Execution result', '执行结果')}</h3>
                <span className="quiet-badge">{execution.status}</span>
              </div>
              {runtimeReceipt && (
                <dl>
                  <Property label={text('Runtime commit', 'Runtime 提交状态')}>
                    {String(runtimeReceipt.commit_status ?? '—')}
                  </Property>
                  <Property label={text('Recovery checkpoint', '恢复检查点')}>
                    <code>{String(runtimeReceipt.checkpoint_id ?? '—')}</code>
                  </Property>
                  <Property label={text('Persisted effect', '持久化 Effect')}>
                    <code>{String(runtimeReceipt.effect_id ?? '—')}</code>
                  </Property>
                </dl>
              )}
              <pre>{JSON.stringify(execution.result, null, 2)}</pre>
            </div>
          )}
          <details className="advanced-request">
            <summary>
              {text('Advanced · raw request JSON', '高级 · 原始请求 JSON')}
            </summary>
            <label htmlFor="core-real-request">
              ToolEvaluationRequest JSON
            </label>
            <textarea
              id="core-real-request"
              disabled={busy}
              rows={12}
              value={raw}
              onChange={(event) => {
                setRaw(event.target.value)
                invalidate()
              }}
            />
            <div className="action-row">
              <button
                className="secondary-button"
                disabled={busy}
                onClick={() => void evaluate(true)}
              >
                真实 Evaluate
              </button>
              {result?.decision.decision === 'ALLOW' && !execution && (
                <button
                  className="secondary-button"
                  disabled={busy}
                  onClick={() => void execute()}
                >
                  真实 Execute
                </button>
              )}
            </div>
          </details>
        </div>

        <aside className="core-evidence-panel">
          <div className="panel-heading">
            <h2>{text('Evidence details', '证据详情')}</h2>
            <span className="quiet-badge">{isSigned ? 'SM2' : '—'}</span>
          </div>
          <dl className="evidence-properties">
            <Property label={text('Crypto mode', '密码模式')}>
              <span className={isSigned ? '' : 'warning-text'}>
                {health
                  ? isSigned
                    ? 'SM2 / SM3'
                    : text('Fake crypto · unsigned', 'Fake 密码模式 · 未签名')
                  : text('Checking…', '检测中…')}
              </span>
            </Property>
            <Property label={text('Key IDs', '公钥标识')}>
              {health?.public_keys?.join(', ') || '—'}
            </Property>
            <Property label={text('Provider', '签名提供方')}>
              {health?.signature_provider}
            </Property>
            <Property label={text('Contract', '任务契约')}>
              {task ? `v${task.contract.ref.version}` : '—'}
            </Property>
          </dl>
          <div className="evidence-selection">
            <h3>{text('Selected event', '选中事件')}</h3>
            {selectedEvent ? (
              <dl>
                <Property label="source_ref">
                  <code>{selectedEvent.source_ref}</code>
                </Property>
                <Property label="object_digest">
                  <code>
                    {selectedEvent.object_digest ??
                      text('Not recorded', '未记录')}
                  </code>
                </Property>
                <Property label="result_digest">
                  <code>
                    {selectedEvent.result_digest ??
                      text('Not recorded', '未记录')}
                  </code>
                </Property>
                <Property label={text('Sequence', '事件序列')}>
                  #{selectedEvent.sequence} · {selectedEvent.state}
                </Property>
              </dl>
            ) : (
              <p className="empty-copy">
                {text(
                  'Select a recorded event to inspect its evidence references.',
                  '选择一条真实事件，查看来源与关联证据摘要。',
                )}
              </p>
            )}
            {selectedEvent && (
              <details>
                <summary>
                  {text(
                    'Raw event & evidence references',
                    '原始事件与证据引用',
                  )}
                </summary>
                <pre>{JSON.stringify(selectedEvent, null, 2)}</pre>
              </details>
            )}
          </div>
          <Activity events={events} />
          <IntentPanel events={events} onSelect={setSelected} />
          {task && task.status === 'CANCELLED' && (
            <RecoveryPanel
              key={task.task_id}
              taskId={task.task_id}
              onComplete={() => reopen(task.task_id)}
            />
          )}
          <div className="audit-tools">
            <h3>{text('Independent verification', '独立核验')}</h3>
            <p className="micro-copy">
              {text(
                'Verifies evidence integrity against a trusted checkpoint. It does not prove task correctness.',
                '基于可信检查点核验证据完整性，不证明任务结果正确。',
              )}
            </p>
            {!isSigned && (
              <p className="notice subtle">
                {text(
                  'Signed export requires SM2 mode.',
                  '签名证据导出需要 SM2 模式。',
                )}
              </p>
            )}
            <label>
              {text('Checkpoint ID', '检查点 ID')}
              <input
                value={checkpoint}
                disabled={busy}
                onChange={(event) => {
                  setCheckpoint(event.target.value)
                  setCheckpointIsManual(true)
                  setVerification(null)
                }}
              />
            </label>
            {bundleBinding && (
              <p className="micro-copy">
                {text('Original verification checkpoint', '原件核验检查点')} ·{' '}
                <code>{bundleBinding.checkpointId}</code>
              </p>
            )}
            <button
              className="secondary-button full-width"
              disabled={busy || !taskId || !checkpoint.trim() || !isSigned}
              onClick={() => void exportEvidence()}
            >
              {text('Export evidence', '导出证据')}
            </button>
            {bundle && (
              <>
                <div className="action-row">
                  <button className="secondary-button" onClick={download}>
                    {text('Download', '下载证据包')}
                  </button>
                  <button
                    className="primary-button"
                    disabled={busy}
                    onClick={() => void verify('original')}
                  >
                    {text('Verify original', '核验原件')}
                  </button>
                </div>
                <button
                  className="text-button"
                  disabled={busy}
                  onClick={tamperCopy}
                >
                  {text('Create tampered copy', '制作篡改副本')}
                </button>
                <details>
                  <summary>
                    {text(
                      'Exported checkpoint & bundle',
                      '导出的检查点与证据包',
                    )}
                  </summary>
                  <pre>{JSON.stringify(bundle.checkpoint, null, 2)}</pre>
                </details>
              </>
            )}
            {copy && (
              <div className="tamper-copy">
                <label>
                  {text('Verification copy JSON', '核验副本 JSON')}
                  <textarea
                    rows={6}
                    disabled={busy}
                    value={copy}
                    onChange={(event) => {
                      setCopy(event.target.value)
                      setVerification(null)
                    }}
                  />
                </label>
                <button
                  className="secondary-button"
                  disabled={busy}
                  onClick={() => void verify('copy')}
                >
                  {text('Verify copy', '核验副本')}
                </button>
              </div>
            )}
            {verification && (
              <div
                className={`verification-result ${verification.result.valid ? 'valid' : 'invalid'}`}
              >
                <strong>
                  {verification.result.valid
                    ? text('Verification passed', '核验通过')
                    : text('Verification failed', '核验失败')}
                </strong>
                <p>
                  {verification.target === 'copy'
                    ? text('Tampered copy', '篡改副本')
                    : text('Original evidence', '原始证据')}{' '}
                  · {verification.result.verified_events}{' '}
                  {text('events verified', '条事件已核验')}
                </p>
                <p>
                  {text('Checkpoint range', '检查点范围')}{' '}
                  {verification.result.anchored_from_seq ?? '—'}—
                  {verification.result.anchored_to_seq ?? '—'} ·{' '}
                  {text('Complete tail', '尾部完整')}{' '}
                  {String(verification.result.tail_complete)}
                </p>
                {verification.result.errors.map((issue, index) => (
                  <p key={index}>
                    {issue.code} · {issue.message}
                  </p>
                ))}
              </div>
            )}
            {health?.limits && (
              <details>
                <summary>{text('Service limits', '服务限制')}</summary>
                <dl>
                  {Object.entries(health.limits).map(([key, value]) => (
                    <Property key={key} label={key}>
                      {String(value)}
                    </Property>
                  ))}
                </dl>
              </details>
            )}
          </div>
        </aside>
      </div>
    </div>
  )
}
