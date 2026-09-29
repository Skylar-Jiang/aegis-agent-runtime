import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { denyApproval, grantApproval } from '../api/approvals'
import {
  applyDemoUserModification,
  getDemoRecovery,
  previewDemoRecovery,
  recoverDemoScenario,
  resetDemoWorkspace,
  startDemoScenario,
  type DemoScenario,
  type RecoveryView,
} from '../api/demo'
import {
  cancelTaskGraph,
  getTaskApprovals,
  getTaskEffects,
  getTaskGraph,
  retryTaskGraphRecovery,
  type ApprovalView,
  type EffectView,
  type TaskGraphSnapshot,
} from '../api/taskGraphs'
import { getTaskEvents } from '../api/tasks'
import { StatusBadge } from '../components/StatusBadge'
import { localizeStatus, localizeTool, type Language, useI18n } from '../i18n'
import type { AuditEvent } from '../types/contracts'
import { layoutGraphNodes } from './taskGraphLayout'

type GraphNode = TaskGraphSnapshot['nodes'][number]

function effectReason(effect: EffectView, language: Language) {
  if (effect.status === 'ROLLED_BACK')
    return language === 'zh-CN'
      ? '该 Effect 位于运行时恢复闭包中，已执行回滚。'
      : 'Selected by the Runtime recovery closure.'
  if (effect.status === 'PRESERVED')
    return language === 'zh-CN'
      ? '该 Effect 已独立提交且位于恢复闭包之外，因此被保留。'
      : 'Independent committed effect outside the closure.'
  if (effect.status === 'CONFLICT')
    return language === 'zh-CN'
      ? '用户内容与检查点不再一致，运行时为保护用户修改而保留它。'
      : 'User content no longer matched the checkpoint; preservation protected it.'
  return language === 'zh-CN'
    ? '运行时 Effect 生命周期状态。'
    : 'Runtime effect lifecycle state.'
}

function targetName(node: GraphNode, language: Language) {
  const target = node.effect_targets?.[0]
  if (!target) return language === 'zh-CN' ? '运行时请求' : 'runtime request'
  const segments = target.split('/')
  return segments[segments.length - 1]
}

function effectTargetName(effect: EffectView) {
  const segments = effect.target_ref.split('/')
  return segments[segments.length - 1]
}

function nodeName(node: GraphNode, language: Language) {
  const target = targetName(node, language)
    .replace(/\.[^.]+$/, '')
    .replace(/_/g, ' ')
  const verb =
    language === 'zh-CN'
      ? node.tool_name === 'read_file'
        ? '读取'
        : node.tool_name === 'write_file'
          ? '写入'
          : node.tool_name === 'delete_file'
            ? '删除'
            : '执行'
      : node.tool_name === 'read_file'
        ? 'Read'
        : node.tool_name === 'write_file'
          ? 'Write'
          : node.tool_name === 'delete_file'
            ? 'Delete'
            : 'Run'
  return `${verb} ${target}`
}

function semantics(targets: string[] | undefined, language: Language) {
  return (
    targets
      ?.map((target) => target.split(':').slice(-2).join('/'))
      .join(', ') ||
    (language === 'zh-CN' ? '无 Effect 目标' : 'No effect target')
  )
}

function GraphCanvas({
  graph,
  selected,
  onSelect,
  risks,
}: {
  graph: TaskGraphSnapshot
  selected: string | null
  onSelect: (nodeId: string) => void
  risks: Map<string, string>
}) {
  const { language, text } = useI18n()
  const positioned = useMemo(() => layoutGraphNodes(graph.nodes), [graph.nodes])

  return (
    <section className="rounded border border-slate-200 bg-white p-4">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="text-sm font-medium text-slate-800">
            {text('Runtime TaskGraph', '运行时任务图')}
          </h2>
          <p className="mt-1 font-mono text-[11px] text-indigo-600">
            {graph.graph_id}
          </p>
        </div>
        <p className="text-xs text-slate-500">
          {text(
            'solid arrow = dependency · dashed arrow = effect conflict',
            '实线箭头＝依赖关系 · 虚线箭头＝Effect 冲突',
          )}
        </p>
      </div>
      <div className="overflow-x-auto rounded border border-slate-200 bg-slate-100 p-3">
        <div
          className="relative"
          style={{ width: positioned.width, height: positioned.height }}
        >
          <svg
            aria-hidden="true"
            className="absolute inset-0 h-full w-full overflow-visible"
          >
            <defs>
              <marker
                id="dependency-arrow"
                markerWidth="7"
                markerHeight="7"
                refX="6"
                refY="3.5"
                orient="auto"
              >
                <path d="M0,0 L7,3.5 L0,7 z" fill="#64748b" />
              </marker>
              <marker
                id="conflict-arrow"
                markerWidth="7"
                markerHeight="7"
                refX="6"
                refY="3.5"
                orient="auto"
              >
                <path d="M0,0 L7,3.5 L0,7 z" fill="#d97706" />
              </marker>
            </defs>
            {graph.nodes.flatMap((node) =>
              node.dependencies.map((dependency) => {
                const from = positioned.positions.get(dependency)
                const to = positioned.positions.get(node.node_id)
                return from && to ? (
                  <path
                    key={`${dependency}-${node.node_id}`}
                    d={`M ${from.x + 108} ${from.y} C ${from.x + 170} ${from.y}, ${to.x - 170} ${to.y}, ${to.x - 108} ${to.y}`}
                    fill="none"
                    stroke="#64748b"
                    strokeWidth="1.5"
                    markerEnd="url(#dependency-arrow)"
                  />
                ) : null
              }),
            )}
            {graph.nodes.map((node) => {
              const from = positioned.positions.get(
                node.conflict?.conflicting_node_id ?? '',
              )
              const to = positioned.positions.get(node.node_id)
              return from && to ? (
                <path
                  key={`conflict-${node.node_id}`}
                  d={`M ${from.x} ${from.y + 42} C ${from.x + 42} ${from.y + 84}, ${to.x - 42} ${to.y + 84}, ${to.x} ${to.y + 42}`}
                  fill="none"
                  stroke="#d97706"
                  strokeDasharray="5 5"
                  strokeWidth="1.5"
                  markerEnd="url(#conflict-arrow)"
                />
              ) : null
            })}
          </svg>
          {[...positioned.positions.values()].map(({ node, x, y }) => (
            <button
              key={node.node_id}
              type="button"
              onClick={() => onSelect(node.node_id)}
              style={{ left: x - 108, top: y - 43 }}
              className={`absolute w-[216px] rounded border p-3 text-left shadow-sm transition-colors focus:outline-none focus:ring-2 focus:ring-sky-300 ${selected === node.node_id ? 'border-sky-400 bg-sky-400/10' : 'border-slate-200 bg-white hover:border-slate-500'}`}
            >
              <div className="flex items-start justify-between gap-2">
                <div>
                  <p className="text-xs font-medium text-slate-800">
                    {nodeName(node, language)}
                  </p>
                  <p className="mt-0.5 font-mono text-[10px] text-slate-500">
                    {node.node_id}
                  </p>
                </div>
                <StatusBadge status={node.status} />
              </div>
              <div className="mt-2 flex items-center justify-between gap-2 border-t border-slate-200 pt-2 text-[10px]">
                <span className="text-slate-500">
                  {localizeTool(node.tool_name, language)}
                </span>
                <span className="text-slate-500">
                  {text('risk', '风险')}{' '}
                  {risks.get(node.request_id ?? '') ??
                    text('evaluating', '评估中')}
                </span>
              </div>
            </button>
          ))}
        </div>
      </div>
    </section>
  )
}

export function TaskGraphPage() {
  const { language, text } = useI18n()
  const [searchParams, setSearchParams] = useSearchParams()
  const [taskId, setTaskId] = useState(searchParams.get('task_id') ?? '')
  const [graph, setGraph] = useState<TaskGraphSnapshot | null>(null)
  const [effects, setEffects] = useState<EffectView[]>([])
  const [approvals, setApprovals] = useState<ApprovalView[]>([])
  const [events, setEvents] = useState<AuditEvent[]>([])
  const [recovery, setRecovery] = useState<RecoveryView | null>(null)
  const [recoveryPreview, setRecoveryPreview] = useState<RecoveryView | null>(
    null,
  )
  const [selectedRollbackEffectIds, setSelectedRollbackEffectIds] = useState<
    string[]
  >([])
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [decidedBy, setDecidedBy] = useState('demo-reviewer')
  const [activeDemoControl, setActiveDemoControl] = useState<
    'reset' | DemoScenario | null
  >(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [lastRefreshedAt, setLastRefreshedAt] = useState<Date | null>(null)
  const [refreshError, setRefreshError] = useState<string | null>(null)
  const backgroundRefreshInFlight = useRef<number | null>(null)
  const loadGeneration = useRef(0)
  const queryTaskId = searchParams.get('task_id')?.trim() ?? ''

  const load = useCallback(async (value: string, background = false) => {
    const selectedTaskId = value.trim()
    if (!selectedTaskId) return
    const generation = background
      ? loadGeneration.current
      : ++loadGeneration.current
    if (background) {
      if (backgroundRefreshInFlight.current === generation) return
      backgroundRefreshInFlight.current = generation
    } else {
      setLoading(true)
      setError(null)
    }
    try {
      const nextGraph = await getTaskGraph(selectedTaskId)
      const [nextEffects, nextApprovals, audit] = await Promise.all([
        getTaskEffects(selectedTaskId),
        getTaskApprovals(selectedTaskId),
        getTaskEvents(selectedTaskId),
      ])
      const nextRecovery = selectedTaskId.includes('demo-recovery-task')
        ? await getDemoRecovery(selectedTaskId)
        : null
      if (generation !== loadGeneration.current) return
      setGraph(nextGraph)
      setEffects(nextEffects)
      setApprovals(nextApprovals)
      setEvents((audit.events as AuditEvent[]) ?? [])
      setSelectedNodeId((current) =>
        current && nextGraph.nodes.some((node) => node.node_id === current)
          ? current
          : (nextGraph.nodes[0]?.node_id ?? null),
      )
      setRecovery(nextRecovery)
      setRefreshError(null)
      setLastRefreshedAt(new Date())
    } catch (cause) {
      if (generation !== loadGeneration.current) return
      if (!background) {
        setGraph(null)
        setEffects([])
        setApprovals([])
        setEvents([])
        setRecovery(null)
        setError((cause as Error).message)
      } else setRefreshError((cause as Error).message)
    } finally {
      if (background && backgroundRefreshInFlight.current === generation)
        backgroundRefreshInFlight.current = null
      if (!background && generation === loadGeneration.current)
        setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadGeneration.current += 1
    setTaskId(queryTaskId)
    setGraph(null)
    setEffects([])
    setApprovals([])
    setEvents([])
    setRecovery(null)
    setRecoveryPreview(null)
    setSelectedRollbackEffectIds([])
    setSelectedNodeId(null)
    setLoading(false)
    setError(null)
    setRefreshError(null)
    setLastRefreshedAt(null)
    if (queryTaskId) void load(queryTaskId)
  }, [load, queryTaskId])
  useEffect(() => {
    if (
      !graph ||
      graph.task_id !== queryTaskId ||
      refreshError ||
      loading ||
      !['RUNNING', 'WAITING_APPROVAL'].includes(graph.status)
    )
      return
    const timer = window.setInterval(() => void load(graph.task_id, true), 1200)
    return () => window.clearInterval(timer)
  }, [graph, load, loading, queryTaskId, refreshError])

  const selected =
    graph?.nodes.find((node) => node.node_id === selectedNodeId) ?? null
  const nodeEvents = selected?.request_id
    ? events.filter((event) => event.request_id === selected.request_id)
    : []
  const risk = nodeEvents.find((event) => event.risk_level)?.risk_level ?? '—'
  const decision = nodeEvents.find((event) => event.decision)?.decision ?? '—'
  const approval = approvals.find(
    (item) => item.request_id === selected?.request_id,
  )
  const risks = useMemo(
    () =>
      new Map(
        events
          .filter((event) => event.request_id && event.risk_level)
          .map((event) => [event.request_id!, event.risk_level!]),
      ),
    [events],
  )

  const resetWorkspace = async () => {
    loadGeneration.current += 1
    setLoading(true)
    setActiveDemoControl('reset')
    setError(null)
    setNotice(null)
    try {
      await resetDemoWorkspace()
      setTaskId('')
      setSearchParams({})
      setGraph(null)
      setEffects([])
      setApprovals([])
      setEvents([])
      setRecovery(null)
      setRecoveryPreview(null)
      setSelectedRollbackEffectIds([])
      setSelectedNodeId(null)
      setNotice(
        text(
          'Workspace reset · demo files restored and ready to run.',
          '工作区已重置，示例文件已恢复，可以开始运行。',
        ),
      )
    } catch (cause) {
      setError((cause as Error).message)
    } finally {
      setLoading(false)
      setActiveDemoControl(null)
    }
  }
  const runScenario = async (scenario: DemoScenario) => {
    loadGeneration.current += 1
    setLoading(true)
    setActiveDemoControl(scenario)
    setError(null)
    setNotice(null)
    setRecovery(null)
    setRecoveryPreview(null)
    setSelectedRollbackEffectIds([])
    try {
      const created = await startDemoScenario(scenario)
      setTaskId(created.task_id)
      setNotice(
        text(
          `${scenario.toUpperCase()} scenario submitted · waiting for Runtime facts.`,
          `${scenario.toUpperCase()} 场景已提交，正在等待运行时事实。`,
        ),
      )
      setSearchParams({ task_id: created.task_id })
    } catch (cause) {
      setError((cause as Error).message)
    } finally {
      setLoading(false)
      setActiveDemoControl(null)
    }
  }
  const toggleRollbackEffect = (effectId: string) => {
    setRecoveryPreview(null)
    setSelectedRollbackEffectIds((current) =>
      current.includes(effectId)
        ? current.filter((id) => id !== effectId)
        : [...current, effectId],
    )
  }
  const previewRecovery = async () => {
    if (!graph || !selectedRollbackEffectIds.length) return
    setLoading(true)
    setError(null)
    try {
      setRecoveryPreview(
        await previewDemoRecovery(graph.task_id, selectedRollbackEffectIds),
      )
    } catch (cause) {
      setError((cause as Error).message)
    } finally {
      setLoading(false)
    }
  }
  const recover = async (userModify = false) => {
    if (!graph || !recoveryPreview) return
    setLoading(true)
    setError(null)
    try {
      if (userModify) await applyDemoUserModification(graph.task_id)
      const result = await recoverDemoScenario(
        graph.task_id,
        recoveryPreview.selected_effect_ids,
      )
      setRecovery(result)
      setRecoveryPreview(null)
      setSelectedRollbackEffectIds([])
      await load(graph.task_id)
    } catch (cause) {
      setError((cause as Error).message)
    } finally {
      setLoading(false)
    }
  }
  const decide = async (action: 'grant' | 'deny') => {
    if (!approval || !graph || !decidedBy.trim()) return
    setLoading(true)
    setError(null)
    try {
      await (action === 'grant' ? grantApproval : denyApproval)(
        approval.approval_id,
        decidedBy.trim(),
      )
      await load(graph.task_id)
    } catch (cause) {
      setError((cause as Error).message)
    } finally {
      setLoading(false)
    }
  }
  const retryRecovery = async () => {
    if (!graph || !Object.keys(graph.recovery_failures ?? {}).length) return
    const generation = loadGeneration.current
    setLoading(true)
    setError(null)
    try {
      const updated = await retryTaskGraphRecovery(graph.graph_id)
      if (
        generation !== loadGeneration.current ||
        queryTaskId !== updated.task_id
      )
        return
      setGraph(updated)
      await load(updated.task_id, true)
    } catch (cause) {
      if (generation === loadGeneration.current)
        setError((cause as Error).message)
    } finally {
      if (generation === loadGeneration.current) setLoading(false)
    }
  }

  return (
    <div>
      <header className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-800">
            {text('Runtime execution view', '运行时执行视图')}
          </h1>
          <p className="mt-1 text-sm text-slate-500">
            {text(
              'Inspect Agent steps or TaskGraph nodes, approvals, effects, and audit facts.',
              '集中查看 Agent 步骤或任务图节点、审批、Effect 和审计事实。',
            )}
          </p>
        </div>
        {graph && (
          <div className="flex gap-2">
            <StatusBadge status={graph.status} />
            {graph.kind !== 'agent' &&
              ['RUNNING', 'WAITING_APPROVAL'].includes(graph.status) && (
                <button
                  className="rounded border border-rose-800 px-3 py-1.5 text-sm text-rose-700 disabled:opacity-50"
                  disabled={loading}
                  onClick={() =>
                    void cancelTaskGraph(graph.graph_id).then(() =>
                      load(graph.task_id),
                    )
                  }
                >
                  {text('Cancel graph', '取消任务图')}
                </button>
              )}
          </div>
        )}
      </header>
      {graph &&
        graph.kind !== 'agent' &&
        Object.keys(graph.recovery_failures ?? {}).length > 0 && (
          <section
            role="alert"
            className="mb-4 rounded border border-rose-300 bg-rose-50 p-3 text-sm text-rose-800"
          >
            <p className="font-medium">{text('Recovery failed', '恢复失败')}</p>
            <ul className="mt-2 list-inside list-disc">
              {Object.entries(graph.recovery_failures ?? {}).map(
                ([requestId, reason]) => (
                  <li key={requestId}>
                    <code>{requestId}</code>: {reason}
                  </li>
                ),
              )}
            </ul>
            <button
              className="mt-3 rounded bg-rose-700 px-3 py-1.5 text-xs text-white disabled:opacity-50"
              disabled={loading}
              onClick={() => void retryRecovery()}
            >
              {text('Retry recovery', '重试恢复')}
            </button>
          </section>
        )}
      <section className="mb-4 rounded border border-slate-200 bg-white p-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-sm font-medium text-slate-800">
              {text('Demo controls', '场景控制')}
            </h2>
            <p className="mt-1 text-xs text-slate-500">
              {text(
                'Each scenario resets the fixture and submits a real Runtime TaskGraph.',
                '每个场景都会重置测试夹具并提交真实的运行时任务图。',
              )}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              className="rounded border border-slate-200 px-3 py-1.5 text-xs text-slate-800 transition-transform duration-150 hover:border-slate-500 active:scale-[0.98] motion-reduce:transition-none disabled:opacity-50"
              disabled={activeDemoControl !== null}
              onClick={() => void resetWorkspace()}
            >
              {activeDemoControl === 'reset'
                ? text('Resetting…', '重置中…')
                : text('Reset workspace', '重置工作区')}
            </button>
            {(
              [
                ['boundary', text('A · Boundary', 'A · 安全边界')],
                ['scheduling', text('B · Scheduling', 'B · 调度')],
                ['recovery', text('C · Recovery', 'C · 恢复')],
                ['conflict', text('Conflict', '冲突')],
              ] as Array<[DemoScenario, string]>
            ).map(([scenario, label]) => (
              <button
                key={scenario}
                className="rounded bg-sky-800 px-3 py-1.5 text-xs text-white transition-transform duration-150 hover:bg-sky-700 active:scale-[0.98] motion-reduce:transition-none disabled:opacity-50"
                disabled={loading}
                onClick={() => void runScenario(scenario)}
              >
                {activeDemoControl === scenario
                  ? text('Starting…', '启动中…')
                  : label}
              </button>
            ))}
          </div>
        </div>
        {notice && (
          <p
            role="status"
            className="mt-3 rounded border border-emerald-900 bg-emerald-950/30 px-3 py-2 text-xs text-emerald-700"
          >
            {notice}
          </p>
        )}
      </section>
      <div className="mb-4 flex flex-wrap gap-2">
        <input
          aria-label={text('Task ID', '任务 ID')}
          className="min-w-56 flex-1 rounded border border-slate-200 bg-white px-3 py-2 text-sm text-slate-800"
          placeholder={text('Task ID...', '任务 ID…')}
          value={taskId}
          onChange={(event) => setTaskId(event.target.value)}
        />
        <button
          className="rounded bg-blue-700 px-4 py-2 text-sm text-white disabled:opacity-50"
          disabled={!taskId.trim() || loading}
          onClick={() =>
            queryTaskId === taskId.trim()
              ? void load(taskId)
              : setSearchParams({ task_id: taskId.trim() })
          }
        >
          {loading
            ? text('Loading…', '加载中…')
            : graph
              ? text('Refresh runtime facts', '刷新运行时事实')
              : text('Load runtime facts', '加载运行时事实')}
        </button>
      </div>
      {graph && lastRefreshedAt && (
        <p className="mb-3 text-xs text-slate-500">
          {text('Last successful refresh', '上次成功刷新')}:{' '}
          {lastRefreshedAt.toLocaleTimeString(language)}
        </p>
      )}
      {refreshError && (
        <p
          role="alert"
          className="mb-4 rounded border border-amber-700 bg-amber-50 p-3 text-sm text-amber-800"
        >
          {text(
            'Live refresh paused; showing last successful facts.',
            '实时刷新已暂停，当前显示上次成功获取的数据。',
          )}{' '}
          {refreshError}{' '}
          <button className="underline" onClick={() => void load(queryTaskId)}>
            {text('Retry now', '立即重试')}
          </button>
        </p>
      )}
      {error && (
        <p className="mb-4 rounded border border-red-900 bg-red-950/30 p-3 text-sm text-rose-700">
          {error}
        </p>
      )}
      {!graph && !loading && !error && (
        <p className="rounded border border-slate-200 bg-white p-4 text-sm text-gray-500">
          {text(
            'Start a Demo or load an existing Task ID.',
            '启动一个场景，或加载已有的任务 ID。',
          )}
        </p>
      )}
      {graph && (
        <>
          <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_330px]">
            <GraphCanvas
              graph={graph}
              selected={selectedNodeId}
              onSelect={setSelectedNodeId}
              risks={risks}
            />
            <aside className="rounded border border-slate-200 bg-white p-4">
              <h2 className="text-sm font-medium text-slate-800">
                {text('Safety inspector', '安全检查器')}
              </h2>
              {selected ? (
                <div className="mt-3 space-y-2 text-xs">
                  <p className="font-mono text-indigo-600">
                    {selected.node_id} · {nodeName(selected, language)}
                  </p>
                  <p>
                    <span className="text-slate-500">
                      {text('ToolSpec / Tool', '工具规范 / 工具')}{' '}
                    </span>
                    {localizeTool(selected.tool_name, language)}
                  </p>
                  <p>
                    <span className="text-slate-500">
                      {text('Effect Target', 'Effect 目标')}{' '}
                    </span>
                    <span className="break-all font-mono">
                      {selected.effect_targets?.join(', ') || '—'}
                    </span>
                  </p>
                  <p>
                    <span className="text-slate-500">
                      {text('Semantics', '语义')}{' '}
                    </span>
                    {semantics(selected.effect_targets, language)}
                  </p>
                  <p>
                    <span className="text-slate-500">
                      {text('Risk', '风险')}{' '}
                    </span>
                    {risk}
                  </p>
                  <p>
                    <span className="text-slate-500">
                      {text('Runtime Decision', '运行时决定')}{' '}
                    </span>
                    {decision}
                  </p>
                  <p>
                    <span className="text-slate-500">
                      {text('Approval State', '审批状态')}{' '}
                    </span>
                    {approval?.status
                      ? localizeStatus(approval.status, language)
                      : text('NOT_REQUIRED', '不需要审批')}
                  </p>
                  <p>
                    <span className="text-slate-500">
                      {text('Execution State', '执行状态')}{' '}
                    </span>
                    {localizeStatus(selected.status, language)}
                  </p>
                  <p>
                    <span className="text-slate-500">
                      {text('Dependencies', '依赖')}{' '}
                    </span>
                    {selected.dependencies.join(', ') || '—'}
                  </p>
                  {selected.conflict && (
                    <p className="text-amber-700">
                      {text('Conflict', '冲突')}：{selected.conflict.reason} ·{' '}
                      {selected.conflict.conflicting_node_id}
                    </p>
                  )}
                </div>
              ) : (
                <p className="mt-3 text-xs text-slate-500">
                  {text('Select a node.', '请选择一个节点。')}
                </p>
              )}
              {approval?.status === 'PENDING' && (
                <section className="mt-5 border-t border-amber-900/70 pt-4">
                  <h3 className="text-sm font-medium text-amber-700">
                    {text('Approval required', '需要审批')}
                  </h3>
                  <p className="mt-1 text-xs text-slate-500">
                    {text(
                      `${localizeTool(approval.tool_name, language)} is waiting at the Runtime boundary.`,
                      `${localizeTool(approval.tool_name, language)}正在运行时边界等待审批。`,
                    )}
                  </p>
                  <p className="mt-2 font-mono text-[11px] text-slate-500">
                    {approval.approval_id}
                  </p>
                  <label
                    className="mt-3 block text-xs text-slate-500"
                    htmlFor="runtime-decided-by"
                  >
                    {text('Approver identity', '审批人身份')}
                  </label>
                  <input
                    aria-label={text('Approver identity', '审批人身份')}
                    id="runtime-decided-by"
                    className="mt-1 w-full rounded border border-slate-200 bg-white px-3 py-2 text-sm text-slate-800"
                    value={decidedBy}
                    onChange={(event) => setDecidedBy(event.target.value)}
                  />
                  <div className="mt-3 grid grid-cols-2 gap-2">
                    <button
                      className="rounded bg-emerald-700 px-3 py-2 text-xs font-medium text-white disabled:opacity-50"
                      disabled={loading || !decidedBy.trim()}
                      onClick={() => void decide('grant')}
                    >
                      {text('Grant approval', '批准')}
                    </button>
                    <button
                      className="rounded border border-rose-800 px-3 py-2 text-xs font-medium text-rose-700 disabled:opacity-50"
                      disabled={loading || !decidedBy.trim()}
                      onClick={() => void decide('deny')}
                    >
                      {text('Deny approval', '拒绝')}
                    </button>
                  </div>
                </section>
              )}
              <div className="mt-5 flex gap-3 border-t border-slate-200 pt-3 text-xs">
                <Link
                  className="text-indigo-600"
                  to={`/approvals?task_id=${encodeURIComponent(graph.task_id)}`}
                >
                  {text('Full approvals', '全部审批')}
                </Link>
                <Link
                  className="text-indigo-600"
                  to={`/audit?task_id=${encodeURIComponent(graph.task_id)}`}
                >
                  {text('Audit trail', '审计记录')}
                </Link>
              </div>
            </aside>
          </div>
          {graph.task_id.includes('demo-recovery-task') && (
            <section className="mt-5 rounded border border-slate-200 bg-white p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h2 className="text-sm font-medium text-slate-800">
                    {text('Recovery / Effect lineage', '恢复 / Effect 血缘')}
                  </h2>
                  <p className="mt-1 text-xs text-slate-500">
                    {text(
                      'Failure / Cancel ↓ Affected Closure ↓ Rollback / Preserve / Conflict',
                      '失败 / 取消 ↓ 受影响闭包 ↓ 回滚 / 保留 / 冲突',
                    )}
                  </p>
                </div>
              </div>
              {!recovery && (
                <>
                  <p className="mt-3 text-xs text-slate-500">
                    {text(
                      'Select recovery roots from real committed effects. Selecting a parent includes its descendants; selecting a child never selects its parent.',
                      '从真实已提交 Effect 中选择恢复根。选择父 Effect 会包含后代；选择子 Effect 不会反向包含父级。',
                    )}
                  </p>
                  <div className="mt-3 grid gap-2 md:grid-cols-3">
                    {effects
                      .filter((effect) => effect.status === 'COMMITTED')
                      .map((effect) => {
                        const parentTargets = (
                          effect.parent_effect_ids ?? []
                        ).map(
                          (parentId) =>
                            effects.find((item) => item.effect_id === parentId)
                              ?.target_ref ?? parentId,
                        )
                        return (
                          <label
                            key={effect.effect_id}
                            className={`flex cursor-pointer gap-3 rounded border border-slate-200 bg-white p-3 transition-colors hover:border-slate-200 ${parentTargets.length ? 'md:ml-5' : ''}`}
                          >
                            <input
                              aria-label={text(
                                `Select ${effectTargetName(effect)}`,
                                `选择 ${effectTargetName(effect)}`,
                              )}
                              className="mt-0.5 accent-sky-500"
                              type="checkbox"
                              checked={selectedRollbackEffectIds.includes(
                                effect.effect_id,
                              )}
                              disabled={loading}
                              onChange={() =>
                                toggleRollbackEffect(effect.effect_id)
                              }
                            />
                            <span>
                              <span className="block break-all font-mono text-[11px] text-slate-800">
                                {effect.target_ref}
                              </span>
                              <span className="mt-1 block text-[11px] text-slate-500">
                                {parentTargets.length
                                  ? text(
                                      `child of ${parentTargets.join(', ')}`,
                                      `子级，父 Effect：${parentTargets.join(', ')}`,
                                    )
                                  : text(
                                      'root / independent effect',
                                      '根 / 独立 Effect',
                                    )}
                              </span>
                            </span>
                          </label>
                        )
                      })}
                  </div>
                  <div className="mt-3 flex flex-wrap gap-2">
                    <button
                      className="rounded bg-amber-700 px-3 py-2 text-xs text-white disabled:opacity-50"
                      disabled={loading || !selectedRollbackEffectIds.length}
                      onClick={() => void previewRecovery()}
                    >
                      {text('Preview selected rollback', '预览所选回滚')}
                    </button>
                    {recoveryPreview && (
                      <>
                        <button
                          className="rounded bg-rose-700 px-3 py-2 text-xs text-white disabled:opacity-50"
                          disabled={loading}
                          onClick={() => void recover(false)}
                        >
                          {text('Execute selected rollback', '执行所选回滚')}
                        </button>
                        <button
                          className="rounded border border-amber-700 px-3 py-2 text-xs text-amber-700 disabled:opacity-50"
                          disabled={loading}
                          onClick={() => void recover(true)}
                        >
                          {text(
                            'User change, then execute',
                            '模拟用户修改后执行',
                          )}
                        </button>
                      </>
                    )}
                  </div>
                </>
              )}
              {recoveryPreview && (
                <p className="mt-3 rounded border border-amber-900/70 bg-amber-950/20 px-3 py-2 text-xs text-amber-700">
                  {text('Selected roots', '所选根')}{' '}
                  {recoveryPreview.selected_effect_ids.length} →{' '}
                  {text('Affected closure', '受影响闭包')}{' '}
                  {recoveryPreview.affected_effect_ids.length} →{' '}
                  {text('Rollback', '回滚')}{' '}
                  {recoveryPreview.rollback_effect_ids.length} ·{' '}
                  {text('Preserve', '保留')}{' '}
                  {recoveryPreview.preserve_effect_ids.length}
                </p>
              )}
              {recovery && (
                <p className="mt-3 rounded border border-slate-200 bg-white px-3 py-2 text-xs text-slate-600">
                  {text('Affected', '受影响')}{' '}
                  {recovery.affected_effect_ids.length} ·{' '}
                  {text('rollback', '回滚')}{' '}
                  {recovery.rollback_effect_ids.length} ·{' '}
                  {text('preserve', '保留')}{' '}
                  {recovery.preserve_effect_ids.length} ·{' '}
                  {text('conflict', '冲突')}{' '}
                  {recovery.conflict_effect_ids.length}
                </p>
              )}
              <div className="mt-3 grid gap-2 md:grid-cols-3">
                {effects.map((effect) => (
                  <div
                    key={effect.effect_id}
                    className="rounded border border-slate-200 bg-white p-3"
                  >
                    <p className="break-all font-mono text-[11px] text-slate-600">
                      {effect.target_ref}
                    </p>
                    <div className="mt-2">
                      <StatusBadge status={effect.status} />
                    </div>
                    <p className="mt-2 text-[11px] text-slate-500">
                      {effectReason(effect, language)}
                    </p>
                  </div>
                ))}
              </div>
            </section>
          )}
          <section className="mt-5">
            <h2 className="mb-2 text-sm font-medium text-slate-500">
              {text('Effects', '执行效果')}
            </h2>
            {effects.length ? (
              <div className="grid gap-2 md:grid-cols-2">
                {effects.map((effect) => (
                  <div
                    key={effect.effect_id}
                    className="rounded border border-slate-200 bg-white p-3 text-xs"
                  >
                    <span className="font-mono text-slate-600">
                      {effect.target_ref}
                    </span>{' '}
                    <span className="ml-2">
                      <StatusBadge status={effect.status} />
                    </span>
                    <span className="ml-2 font-mono text-slate-500">
                      {effect.checkpoint_id ?? '—'}
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-slate-500">
                {text(
                  'No Runtime effects recorded.',
                  '尚未记录运行时 Effect。',
                )}
              </p>
            )}
          </section>
        </>
      )}
    </div>
  )
}
